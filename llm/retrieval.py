"""Domain-specific memory retrieval and ranking for RentMate."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import chromadb
from openai import OpenAI
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backends.local_auth import resolve_account_id, resolve_org_id
from db.models import (
    AgentMemory,
    Conversation,
    Document,
    DocumentTag,
    EntityNote,
    Lease,
    MemoryItem,
    MessageType,
    Property,
    Task,
    Tenant,
    Unit,
    User,
)
from db.queries import format_address, tenant_display_name
from llm.history_filters import is_transient_tool_failure_text
from llm.model_config import resolve_model_config
from llm.tracing import log_trace

logger = logging.getLogger(__name__)


def _data_dir() -> Path:
    return Path(os.getenv("RENTMATE_DATA_DIR", "./data"))


CHROMA_PATH = _data_dir() / "chroma"
COLLECTION_NAME = "rentmate_memory_items"
EMBED_DIM = 128
RERANK_TOP_K = 8
RERANK_ENABLED_INTENTS = {"answer_question", "triage"}


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def embed_text(text: str, dim: int = EMBED_DIM) -> list[float]:
    vec = [0.0] * dim
    for token in _tokenize(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "big") % dim
        sign = -1.0 if digest[4] % 2 else 1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _hash_content(title: str | None, content: str, metadata: dict[str, Any] | None) -> str:
    payload = f"{title or ''}\n{content}\n{metadata or {}}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class MemorySourceRef:
    source_type: str
    source_id: str
    entity_type: str
    entity_id: str
    visibility: str = "shared"


@dataclass
class MemoryRecord:
    source: MemorySourceRef
    title: str | None
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievalRequest:
    surface: str
    intent: str
    query: str = ""
    task_id: str | None = None
    property_id: str | None = None
    unit_id: str | None = None
    tenant_id: str | None = None
    vendor_id: str | None = None
    creator_id: int | None = None
    org_id: int | None = None
    limit: int = 12


@dataclass
class RankedContextItem:
    memory_item_id: str
    source_type: str
    source_id: str
    entity_type: str
    entity_id: str
    title: str | None
    content: str
    metadata: dict[str, Any]
    heuristic_score: float
    vector_score: float
    final_score: float
    reasons: list[str]


@dataclass
class RankedContextBundle:
    request: RetrievalRequest
    items: list[RankedContextItem]


class ChromaMemoryIndex:
    def __init__(self) -> None:
        CHROMA_PATH.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(CHROMA_PATH))
        self.collection = self.client.get_or_create_collection(name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"})

    def upsert(self, item: MemoryItem) -> None:
        self.collection.upsert(
            ids=[item.id],
            documents=[item.content],
            embeddings=[embed_text(item.content)],
            metadatas=[{
                "org_id": item.org_id,
                "creator_id": item.creator_id,
                "visibility": item.visibility,
                "source_type": item.source_type,
                "entity_type": item.entity_type,
                "entity_id": item.entity_id,
            }],
        )

    def delete(self, ids: list[str]) -> None:
        if ids:
            self.collection.delete(ids=ids)

    def query(self, request: RetrievalRequest, *, where: dict[str, Any], n_results: int) -> dict[str, float]:
        query_text = request.query or request.intent or "property management"
        result = self.collection.query(
            query_embeddings=[embed_text(query_text)],
            where=where,
            n_results=n_results,
            include=["distances"],
        )
        ids = result.get("ids", [[]])[0]
        distances = result.get("distances", [[]])[0]
        scores: dict[str, float] = {}
        for item_id, distance in zip(ids, distances):
            # cosine distance -> similarity
            scores[item_id] = 1.0 - float(distance)
        return scores

    def reset(self) -> None:
        try:
            self.client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass
        self.collection = self.client.get_or_create_collection(name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"})


def _today_str() -> str:
    return date.today().isoformat()


_COMPLIANCE_QUERY_HINTS = {
    "compliance",
    "legal",
    "law",
    "notice",
    "evict",
    "eviction",
    "vacate",
    "detainer",
    "landlord",
    "manager",
    "owner",
    "statutory",
    "cure",
    "quit",
    "deduction",
    "deposit",
}

_CURRENT_IDENTITY_HINTS = {
    "landlord",
    "manager",
    "owner",
    "contact",
    "company",
    "payee",
    "pay-to",
}


def _is_compliance_sensitive_request(request: RetrievalRequest, query_tokens: set[str]) -> bool:
    query_text = (request.query or "").lower()
    if request.intent.lower() in {"draft_document", "legal_answer", "compliance_answer"}:
        return True
    return bool(_COMPLIANCE_QUERY_HINTS & query_tokens) or any(
        phrase in query_text
        for phrase in [
            "pay or vacate",
            "legal notice",
            "security deposit",
            "statutory form",
            "unlawful detainer",
        ]
    )


def _targets_current_identity_or_contact(query_tokens: set[str], query_text: str) -> bool:
    return bool(_CURRENT_IDENTITY_HINTS & query_tokens) or any(
        phrase in query_text
        for phrase in [
            "who should appear",
            "who goes on",
            "current landlord",
            "current manager",
            "manager contact",
        ]
    )


def _property_records(db: Session) -> list[MemoryRecord]:
    records: list[MemoryRecord] = []
    for prop in db.query(Property).filter(Property.org_id == resolve_org_id()).all():
        label = prop.name or format_address(prop)
        facts = [
            f"Property: {label}",
            f"Address: {format_address(prop)}",
            f"Type: {prop.property_type or 'unknown'}",
        ]
        if prop.context:
            facts.append(f"Context: {prop.context}")
        records.append(MemoryRecord(
            source=MemorySourceRef("property", str(prop.id), "property", str(prop.id), "shared"),
            title=label,
            content="\n".join(facts),
            metadata={"property_id": str(prop.id), "property_type": prop.property_type or ""},
        ))
    return records


def _unit_records(db: Session) -> list[MemoryRecord]:
    records: list[MemoryRecord] = []
    for unit in db.query(Unit).filter(Unit.org_id == resolve_org_id()).all():
        prop = unit.property
        label = f"{prop.name or format_address(prop)} {unit.label}" if prop else unit.label
        facts = [
            f"Unit: {unit.label}",
            f"Property ID: {unit.property_id}",
        ]
        if unit.context:
            facts.append(f"Context: {unit.context}")
        records.append(MemoryRecord(
            source=MemorySourceRef("unit", str(unit.id), "unit", str(unit.id), "shared"),
            title=label,
            content="\n".join(facts),
            metadata={"property_id": str(unit.property_id), "unit_label": unit.label},
        ))
    return records


def _tenant_records(db: Session) -> list[MemoryRecord]:
    records: list[MemoryRecord] = []
    for tenant in db.query(Tenant).filter(Tenant.org_id == resolve_org_id()).all():
        name = tenant_display_name(tenant)
        user = tenant.user
        active_lease = next((lease for lease in tenant.leases if lease.end_date and lease.end_date >= date.today()), None)
        facts = [f"Tenant: {name}"]
        if user:
            if user.email:
                facts.append(f"Email: {user.email}")
            if user.phone:
                facts.append(f"Phone: {user.phone}")
        if active_lease and active_lease.unit:
            facts.append(f"Unit: {active_lease.unit.label}")
            facts.append(f"Property ID: {active_lease.property_id}")
        if tenant.context:
            facts.append(f"Context: {tenant.context}")
        records.append(MemoryRecord(
            source=MemorySourceRef("tenant", str(tenant.external_id), "tenant", str(tenant.external_id), "shared"),
            title=name,
            content="\n".join(facts),
            metadata={
                "tenant_id": str(tenant.external_id),
                "property_id": str(active_lease.property_id) if active_lease else "",
                "unit_id": str(active_lease.unit_id) if active_lease else "",
            },
        ))
    return records


def _vendor_records(db: Session) -> list[MemoryRecord]:
    records: list[MemoryRecord] = []
    for vendor in db.query(User).filter(User.org_id == resolve_org_id(), User.user_type == "vendor").all():
        facts = [f"Vendor: {vendor.name}"]
        if vendor.role_label:
            facts.append(f"Vendor type: {vendor.role_label}")
        if vendor.phone:
            facts.append(f"Phone: {vendor.phone}")
        if vendor.email:
            facts.append(f"Email: {vendor.email}")
        if vendor.context:
            facts.append(f"Context: {vendor.context}")
        records.append(MemoryRecord(
            source=MemorySourceRef("vendor", str(vendor.external_id), "vendor", str(vendor.external_id), "shared"),
            title=vendor.name,
            content="\n".join(facts),
            metadata={"vendor_id": str(vendor.external_id), "vendor_type": vendor.role_label or ""},
        ))
    return records


def _lease_records(db: Session) -> list[MemoryRecord]:
    records: list[MemoryRecord] = []
    today = date.today()
    for lease in db.query(Lease).filter(Lease.org_id == resolve_org_id()).all():
        tenant_name = tenant_display_name(lease.tenant) if lease.tenant else "Tenant"
        title = f"Lease for {tenant_name}"
        is_active = bool(lease.start_date and lease.end_date and lease.start_date <= today <= lease.end_date)
        is_expired = bool(lease.end_date and lease.end_date < today)
        facts = [
            f"Lease starts: {lease.start_date}",
            f"Lease ends: {lease.end_date}",
            f"Rent amount: ${lease.rent_amount}/month",
            f"Payment status: {lease.payment_status or 'current'}",
        ]
        if lease.unit:
            facts.append(f"Unit: {lease.unit.label}")
        if lease.property:
            facts.append(f"Property: {lease.property.name or format_address(lease.property)}")
        records.append(MemoryRecord(
            source=MemorySourceRef("lease", str(lease.id), "lease", str(lease.id), "shared"),
            title=title,
            content="\n".join(facts),
            metadata={
                "tenant_id": str(lease.tenant.external_id) if lease.tenant else "",
                "property_id": str(lease.property_id),
                "unit_id": str(lease.unit_id),
                "lease_start": str(lease.start_date),
                "lease_end": str(lease.end_date),
                "payment_status": lease.payment_status or "",
                "is_active": is_active,
                "is_expired": is_expired,
                "source_confidence": "active_lease" if is_active else "expired_lease" if is_expired else "historical_lease",
            },
        ))
    return records


def _task_records(db: Session) -> list[MemoryRecord]:
    records: list[MemoryRecord] = []
    for task in db.query(Task).filter(Task.org_id == resolve_org_id()).all():
        title = task.title or f"Task {task.id}"
        facts = [
            f"Task: {title}",
            f"Status: {getattr(task.task_status, 'value', task.task_status) or 'active'}",
            f"Mode: {getattr(task.task_mode, 'value', task.task_mode) or 'manual'}",
            f"Category: {getattr(task.category, 'value', task.category) or 'general'}",
            f"Urgency: {getattr(task.urgency, 'value', task.urgency) or 'normal'}",
        ]
        if task.context:
            facts.append(f"Task notes: {task.context}")
        records.append(MemoryRecord(
            source=MemorySourceRef("task", str(task.id), "task", str(task.id), "shared"),
            title=title,
            content="\n".join(facts),
            metadata={
                "task_id": str(task.id),
                "property_id": str(task.property_id or ""),
                "unit_id": str(task.unit_id or ""),
            },
        ))
    return records


def _entity_note_records(db: Session, creator_id: int) -> list[MemoryRecord]:
    records: list[MemoryRecord] = []
    notes = db.query(EntityNote).filter(
        EntityNote.org_id == resolve_org_id(),
        EntityNote.creator_id == creator_id,
    ).all()
    for note in notes:
        records.append(MemoryRecord(
            source=MemorySourceRef("entity_note", str(note.id), note.entity_type, note.entity_id, "private"),
            title=f"Private {note.entity_type} note",
            content=note.content,
            metadata={"entity_type": note.entity_type, "entity_id": note.entity_id},
        ))
    return records


def _general_note_records(db: Session, creator_id: int) -> list[MemoryRecord]:
    records: list[MemoryRecord] = []
    notes = db.query(AgentMemory).filter(
        AgentMemory.org_id == resolve_org_id(),
        AgentMemory.creator_id == creator_id,
        AgentMemory.memory_type == "note:general",
    ).all()
    for note in notes:
        records.append(MemoryRecord(
            source=MemorySourceRef("agent_memory", str(note.id), "general", "general", "private"),
            title="General memory note",
            content=note.content,
            metadata={"memory_type": note.memory_type},
        ))
    return records


def _compact_text(text: str | None, *, max_len: int = 220) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "..."


def _conversation_note_lines(conv: Conversation) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()

    def add(prefix: str, text: str | None) -> None:
        value = _compact_text(text)
        if not value:
            return
        line = f"{prefix}{value}"
        if line in seen:
            return
        seen.add(line)
        lines.append(line)

    if conv.conversation_type:
        add("Conversation type: ", str(conv.conversation_type))
    if conv.property_id:
        add("Property ID: ", str(conv.property_id))
    if conv.unit_id:
        add("Unit ID: ", str(conv.unit_id))

    msgs = [
        m for m in sorted(conv.messages or [], key=lambda m: m.sent_at)
        if (m.body or "").strip() and m.message_type in (
            MessageType.MESSAGE,
            MessageType.THREAD,
            MessageType.CONTEXT,
            MessageType.SUGGESTION,
            MessageType.ACTION,
            MessageType.DRAFT_AI_REPLY,
        )
    ]
    if not msgs:
        return lines

    context_msgs = [m for m in msgs if m.message_type == MessageType.CONTEXT]
    for m in context_msgs[-2:]:
        add("Context note: ", m.body)

    for m in reversed(msgs):
        meta = m.meta if isinstance(m.meta, dict) else {}
        action_card = meta.get("action_card") if isinstance(meta, dict) else None
        if isinstance(action_card, dict):
            title = _compact_text(action_card.get("title"))
            summary = _compact_text(action_card.get("summary"))
            candidate = ". ".join(part for part in (title, summary) if part)
            if is_transient_tool_failure_text(candidate):
                continue
            if title and summary:
                add("AI note: ", f"{title}. {summary}")
            elif title:
                add("AI note: ", title)
            elif summary:
                add("AI note: ", summary)
        if len(lines) >= 5:
            break

    preference_markers = (
        "do not",
        "don't",
        "dont",
        "instead",
        "prefer",
        "use this one",
        "same task",
        "create a draft",
        "no suggestion",
    )
    for m in reversed(msgs):
        if m.is_ai:
            continue
        body = (m.body or "").strip()
        lower = body.lower()
        if any(marker in lower for marker in preference_markers):
            add("User preference: ", body)
            break

    return lines


def _conversation_records(db: Session) -> list[MemoryRecord]:
    records: list[MemoryRecord] = []
    conversations = db.query(Conversation).filter(Conversation.org_id == resolve_org_id()).all()
    for conv in conversations:
        note_lines = _conversation_note_lines(conv)
        if not note_lines:
            continue
        records.append(MemoryRecord(
            source=MemorySourceRef("conversation_note", str(conv.id), "conversation", str(conv.external_id or conv.id), "shared"),
            title=conv.subject or f"Conversation {conv.id}",
            content="\n".join(note_lines),
            metadata={
                "conversation_id": str(conv.external_id or conv.id),
                "conversation_type": str(conv.conversation_type or ""),
                "property_id": str(conv.property_id or ""),
                "unit_id": str(conv.unit_id or ""),
            },
        ))
    return records


def _document_records(db: Session) -> list[MemoryRecord]:
    records: list[MemoryRecord] = []
    tag_map: dict[str, dict[str, str]] = {}
    for tag in db.query(DocumentTag).filter(DocumentTag.org_id == resolve_org_id()).all():
        entry = tag_map.setdefault(str(tag.document_id), {})
        if tag.property_id and not entry.get("property_id"):
            entry["property_id"] = str(tag.property_id)
        if tag.unit_id and not entry.get("unit_id"):
            entry["unit_id"] = str(tag.unit_id)
        if tag.tenant_id and not entry.get("tenant_id"):
            entry["tenant_id"] = str(tag.tenant_id)
    docs = db.query(Document).filter(Document.org_id == resolve_org_id(), Document.context.isnot(None)).all()
    for doc in docs:
        tag_meta = tag_map.get(str(doc.id), {})
        records.append(MemoryRecord(
            source=MemorySourceRef("document", str(doc.id), "document", str(doc.id), "shared"),
            title=doc.filename,
            content=doc.context or "",
            metadata={
                "document_type": doc.document_type or "",
                "document_id": str(doc.id),
                "property_id": tag_meta.get("property_id", ""),
                "unit_id": tag_meta.get("unit_id", ""),
                "tenant_id": tag_meta.get("tenant_id", ""),
                "created_at": doc.created_at.isoformat() if doc.created_at else "",
                "confirmed_at": doc.confirmed_at.isoformat() if doc.confirmed_at else "",
                "source_confidence": "confirmed_document" if doc.confirmed_at else "unconfirmed_document",
            },
        ))
    return records


def collect_memory_records(db: Session, *, creator_id: int | None = None) -> list[MemoryRecord]:
    creator = creator_id or resolve_account_id()
    return [
        *_property_records(db),
        *_unit_records(db),
        *_tenant_records(db),
        *_vendor_records(db),
        *_lease_records(db),
        *_task_records(db),
        *_entity_note_records(db, creator),
        *_general_note_records(db, creator),
        *_conversation_records(db),
        *_document_records(db),
    ]


def sync_memory_index(db: Session, *, creator_id: int | None = None) -> int:
    creator = creator_id or resolve_account_id()
    index = ChromaMemoryIndex()
    existing = {
        (row.source_type, row.source_id): row
        for row in db.execute(
            select(MemoryItem).where(
                MemoryItem.org_id == resolve_org_id(),
                MemoryItem.creator_id == creator,
            )
        ).scalars()
    }

    seen_keys: set[tuple[str, str]] = set()
    count = 0
    for record in collect_memory_records(db, creator_id=creator):
        key = (record.source.source_type, record.source.source_id)
        seen_keys.add(key)
        content_hash = _hash_content(record.title, record.content, record.metadata)
        row = existing.get(key)
        if row is None:
            row = MemoryItem(
                id=str(uuid.uuid4()),
                org_id=resolve_org_id(),
                creator_id=creator,
                source_type=record.source.source_type,
                source_id=record.source.source_id,
                entity_type=record.source.entity_type,
                entity_id=record.source.entity_id,
                visibility=record.source.visibility,
                title=record.title,
                content=record.content,
                content_hash=content_hash,
                metadata_json=record.metadata,
                updated_at=datetime.now(UTC),
            )
            db.add(row)
            db.flush()
        elif row.content_hash != content_hash or row.title != record.title or (row.metadata_json or {}) != record.metadata:
            row.entity_type = record.source.entity_type
            row.entity_id = record.source.entity_id
            row.visibility = record.source.visibility
            row.title = record.title
            row.content = record.content
            row.content_hash = content_hash
            row.metadata_json = record.metadata
            row.updated_at = datetime.now(UTC)
        index.upsert(row)
        count += 1

    stale_ids = [row.id for key, row in existing.items() if key not in seen_keys]
    if stale_ids:
        db.execute(delete(MemoryItem).where(MemoryItem.id.in_(stale_ids)))
        index.delete(stale_ids)
    db.commit()
    log_trace("memory_sync", "memory", f"Synced {count} memory items", detail={"count": count, "creator_id": creator})
    return count


def _base_where(creator_id: int) -> dict[str, Any]:
    return {"org_id": resolve_org_id()}


def _eligible_items(db: Session, creator_id: int) -> list[MemoryItem]:
    items = db.execute(
        select(MemoryItem).where(
            MemoryItem.org_id == resolve_org_id(),
            MemoryItem.creator_id == creator_id,
        )
    ).scalars().all()
    return [
        item for item in items
        if item.visibility == "shared" or item.creator_id == creator_id
    ]


def _heuristic_score(request: RetrievalRequest, item: MemoryItem, query_tokens: set[str]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    metadata = item.metadata_json or {}
    query_text = (request.query or "").lower()
    compliance_sensitive = _is_compliance_sensitive_request(request, query_tokens)
    targets_identity = _targets_current_identity_or_contact(query_tokens, query_text)

    def bump(value: float, reason: str) -> None:
        nonlocal score
        score += value
        reasons.append(reason)

    if request.task_id and metadata.get("task_id") == str(request.task_id):
        bump(5.0, "same task")
    if request.property_id and metadata.get("property_id") == str(request.property_id):
        bump(4.0, "same property")
    if request.unit_id and metadata.get("unit_id") == str(request.unit_id):
        bump(4.5, "same unit")
    if request.tenant_id and metadata.get("tenant_id") == str(request.tenant_id):
        bump(4.5, "same tenant")
    if request.vendor_id and metadata.get("vendor_id") == str(request.vendor_id):
        bump(4.5, "same vendor")

    intent = request.intent.lower()
    if intent in {"draft_message", "follow_up"} and item.source_type in {"tenant", "vendor", "conversation_note", "task"}:
        bump(1.5, "messaging intent prior")
    if intent in {"triage", "answer_question"} and item.source_type in {"lease", "task", "property", "unit"}:
        bump(1.2, "triage intent prior")
    if (
        item.source_type == "conversation_note"
        and any(marker in query_text for marker in ["don't create", "dont create", "do not create"])
    ):
        conversation_type = str(metadata.get("conversation_type") or "")
        if conversation_type in {"suggestion_ai", "task_ai"}:
            bump(-2.0, f"downranked prior {conversation_type} after explicit rejection")
    if "rent" in query_tokens or "payment" in query_tokens or "late" in query_tokens:
        if item.source_type == "lease":
            bump(2.5, "rent intent matched lease")
        if metadata.get("payment_status"):
            bump(1.0, "payment metadata")
    if {"renewal", "lease", "expire", "expiring"} & query_tokens:
        if item.source_type == "lease":
            bump(2.0, "lease renewal intent")
        if metadata.get("lease_end"):
            bump(1.0, "lease end date present")
    if {"vendor", "plumber", "electrician", "repair", "maintenance", "hvac"} & query_tokens:
        if item.source_type in {"vendor", "task", "property", "unit"}:
            bump(1.5, "maintenance/vendor intent")

    if compliance_sensitive:
        if item.source_type == "lease":
            if metadata.get("is_expired"):
                bump(-6.0, "expired lease blocked for compliance-sensitive facts")
            elif metadata.get("is_active"):
                bump(1.8, "active lease preferred for compliance-sensitive facts")
        if item.source_type == "document" and metadata.get("document_type") == "lease":
            bump(-3.5 if targets_identity else -2.0, "lease document treated as low-confidence for compliance-sensitive facts")
            if metadata.get("confirmed_at"):
                bump(0.75, "confirmed document")
        if targets_identity and item.source_type in {"property", "unit", "task", "entity_note", "tenant", "vendor"}:
            bump(1.5, "current operational source preferred for identity/contact facts")

    content_tokens = set(_tokenize(f"{item.title or ''} {item.content}"))
    overlap = query_tokens & content_tokens
    if overlap:
        bump(min(2.0, 0.2 * len(overlap)), f"token overlap: {', '.join(sorted(list(overlap))[:5])}")
        if item.source_type in {"tenant", "vendor"}:
            bump(2.0, "person-centric entity overlap")
        elif item.source_type == "lease":
            bump(0.8, "person-linked lease overlap")

    return score, reasons


def _resolve_rerank_client_config() -> tuple[str, str, str | None] | None:
    api_key = os.getenv("LLM_API_KEY", "")
    if not api_key:
        return None

    configured_model = os.getenv("LLM_RERANK_MODEL") or os.getenv("LLM_MODEL", "")
    if not configured_model:
        return None
    configured_base = os.getenv("LLM_RERANK_BASE_URL") or os.getenv("LLM_BASE_URL") or None
    resolved = resolve_model_config(model=configured_model, api_base=configured_base)
    return resolved.model, api_key, resolved.api_base


def _extract_json_object(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except Exception:
        return None


def _llm_rerank(request: RetrievalRequest, ranked: list[RankedContextItem]) -> list[RankedContextItem]:
    if request.intent not in RERANK_ENABLED_INTENTS or len(ranked) < 2:
        return ranked
    config = _resolve_rerank_client_config()
    if config is None:
        return ranked

    actual_model, api_key, api_base = config
    shortlist = ranked[: min(RERANK_TOP_K, len(ranked))]
    payload_items = [
        {
            "index": index,
            "source_type": item.source_type,
            "title": item.title,
            "content": item.content[:600],
            "reasons": item.reasons[:5],
        }
        for index, item in enumerate(shortlist)
    ]
    system_prompt = (
        "You are a property-management retrieval reranker. "
        "Return which context items are most relevant to answer the user's question. "
        "Prefer the directly referenced person/entity first when the query is about a named tenant/vendor, "
        "then the linked lease, then property/unit facts, then tasks/conversation scaffolding."
    )
    user_prompt = json.dumps({
        "query": request.query,
        "intent": request.intent,
        "items": payload_items,
        "return_format": {"ordered_indices": [0, 1], "reason": "short explanation"},
    }, default=str)

    try:
        client = OpenAI(api_key=api_key, base_url=api_base, timeout=10)
        response = client.chat.completions.create(
            model=actual_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
            max_tokens=250,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or ""
        parsed = _extract_json_object(content)
        ordered_indices = parsed.get("ordered_indices") if isinstance(parsed, dict) else None
        if not isinstance(ordered_indices, list):
            return ranked
        normalized = [int(i) for i in ordered_indices if isinstance(i, int) or (isinstance(i, str) and str(i).isdigit())]
        seen: set[int] = set()
        ordered_shortlist: list[RankedContextItem] = []
        for idx in normalized:
            if 0 <= idx < len(shortlist) and idx not in seen:
                item = shortlist[idx]
                item.final_score += (len(shortlist) - len(ordered_shortlist)) * 0.35
                item.reasons.append("llm rerank")
                ordered_shortlist.append(item)
                seen.add(idx)
        for idx, item in enumerate(shortlist):
            if idx not in seen:
                ordered_shortlist.append(item)
        log_trace(
            "memory_rerank",
            request.surface,
            f"LLM reranked {len(shortlist)} items for {request.intent}",
            task_id=request.task_id,
            detail={
                "request": {
                    "surface": request.surface,
                    "intent": request.intent,
                    "query": request.query,
                    "task_id": request.task_id,
                    "property_id": request.property_id,
                    "unit_id": request.unit_id,
                    "tenant_id": request.tenant_id,
                    "vendor_id": request.vendor_id,
                    "limit": request.limit,
                },
                "model": actual_model,
                "shortlist": [
                    {
                        "memory_item_id": item.memory_item_id,
                        "title": item.title,
                        "source_type": item.source_type,
                        "final_score": item.final_score,
                        "reasons": item.reasons,
                    }
                    for item in shortlist
                ],
                "ordered_indices": normalized,
                "reason": parsed.get("reason") if isinstance(parsed, dict) else None,
            },
        )
        return ordered_shortlist + ranked[len(shortlist):]
    except Exception as exc:
        log_trace(
            "error",
            "memory_rerank",
            f"LLM rerank failed: {type(exc).__name__}",
            task_id=request.task_id,
            detail={
                "request": {
                    "surface": request.surface,
                    "intent": request.intent,
                    "query": request.query,
                    "task_id": request.task_id,
                    "property_id": request.property_id,
                    "unit_id": request.unit_id,
                    "tenant_id": request.tenant_id,
                    "vendor_id": request.vendor_id,
                    "limit": request.limit,
                },
                "error": str(exc),
            },
        )
        return ranked


def retrieve_context(db: Session, request: RetrievalRequest) -> RankedContextBundle:
    creator = request.creator_id or resolve_account_id()
    request.creator_id = creator
    request.org_id = request.org_id or resolve_org_id()
    disable_vector_index = os.getenv("RENTMATE_DISABLE_VECTOR_INDEX", "").lower() in {"1", "true", "yes"}

    if not disable_vector_index:
        try:
            sync_memory_index(db, creator_id=creator)
        except Exception as exc:
            logger.warning("Skipping memory index sync during retrieval: %s", exc)
            log_trace(
                "warning",
                "memory_sync",
                "Memory index sync skipped during retrieval",
                task_id=request.task_id,
                detail={"error": str(exc), "creator_id": creator},
            )

    items = _eligible_items(db, creator)
    query_tokens = set(_tokenize(request.query or request.intent))
    vector_scores: dict[str, float] = {}
    if items and not disable_vector_index:
        try:
            index = ChromaMemoryIndex()
            vector_scores = index.query(request, where=_base_where(creator), n_results=min(50, max(10, request.limit * 4)))
        except Exception as exc:
            logger.warning("Skipping vector memory query during retrieval: %s", exc)
            log_trace(
                "warning",
                "memory_query",
                "Vector memory query skipped during retrieval",
                task_id=request.task_id,
                detail={"error": str(exc), "creator_id": creator},
            )

    ranked: list[RankedContextItem] = []
    request_embedding = embed_text(request.query or request.intent or "")
    for item in items:
        heuristic_score, reasons = _heuristic_score(request, item, query_tokens)
        vector_score = vector_scores.get(item.id)
        if vector_score is None:
            vector_score = _cosine(request_embedding, embed_text(item.content))
        final_score = heuristic_score * 0.7 + vector_score * 3.0
        ranked.append(RankedContextItem(
            memory_item_id=item.id,
            source_type=item.source_type,
            source_id=item.source_id,
            entity_type=item.entity_type,
            entity_id=item.entity_id,
            title=item.title,
            content=item.content,
            metadata=item.metadata_json or {},
            heuristic_score=heuristic_score,
            vector_score=vector_score,
            final_score=final_score,
            reasons=reasons,
        ))

    ranked.sort(key=lambda item: item.final_score, reverse=True)
    ranked = _llm_rerank(request, ranked)
    ranked.sort(key=lambda item: item.final_score, reverse=True)
    ranked = ranked[: request.limit]

    log_trace(
        "memory_rank",
        request.surface,
        f"Retrieved {len(ranked)} items for {request.intent}",
        task_id=request.task_id,
        detail={
            "request": {
                "surface": request.surface,
                "intent": request.intent,
                "query": request.query,
                "task_id": request.task_id,
                "property_id": request.property_id,
                "unit_id": request.unit_id,
                "tenant_id": request.tenant_id,
                "vendor_id": request.vendor_id,
                "limit": request.limit,
            },
            "candidate_count": len(items),
            "top_items": [
                {
                    "memory_item_id": item.memory_item_id,
                    "source_type": item.source_type,
                    "title": item.title,
                    "final_score": item.final_score,
                    "vector_score": item.vector_score,
                    "heuristic_score": item.heuristic_score,
                    "reasons": item.reasons,
                }
                for item in ranked[:10]
            ],
        },
    )

    return RankedContextBundle(request=request, items=ranked)


def compose_prompt_context(bundle: RankedContextBundle, *, title: str = "Retrieved context") -> str:
    if not bundle.items:
        return ""
    lines = [f"## {title}", f"Retrieved on {_today_str()} for intent: {bundle.request.intent}"]
    for index, item in enumerate(bundle.items, start=1):
        heading = item.title or f"{item.source_type}:{item.source_id}"
        lines.append(f"\n### {index}. {heading}")
        lines.append(f"Source: {item.source_type} | Entity: {item.entity_type} {item.entity_id}")
        lines.append(item.content)
    return "\n".join(lines)


def list_memory_items(db: Session, *, creator_id: int | None = None, query: str = "", limit: int = 200) -> list[MemoryItem]:
    creator = creator_id or resolve_account_id()
    stmt = select(MemoryItem).where(
        MemoryItem.org_id == resolve_org_id(),
        MemoryItem.creator_id == creator,
    ).order_by(MemoryItem.updated_at.desc())
    rows = db.execute(stmt).scalars().all()
    if query:
        q = query.lower()
        rows = [row for row in rows if q in (row.title or "").lower() or q in (row.content or "").lower()]
    return rows[:limit]
