"""Domain-specific memory retrieval and ranking for RentMate."""

from __future__ import annotations

import hashlib
import json
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
from llm.tracing import log_trace


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
    for lease in db.query(Lease).filter(Lease.org_id == resolve_org_id()).all():
        tenant_name = tenant_display_name(lease.tenant) if lease.tenant else "Tenant"
        title = f"Lease for {tenant_name}"
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
                "lease_end": str(lease.end_date),
                "payment_status": lease.payment_status or "",
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


def _conversation_records(db: Session) -> list[MemoryRecord]:
    records: list[MemoryRecord] = []
    conversations = db.query(Conversation).filter(Conversation.org_id == resolve_org_id()).all()
    for conv in conversations:
        msgs = [
            m for m in sorted(conv.messages or [], key=lambda m: m.sent_at)[-6:]
            if (m.body or "").strip() and m.message_type in (MessageType.MESSAGE, MessageType.THREAD, MessageType.CONTEXT)
        ]
        if not msgs:
            continue
        text = "\n".join(f"{'AI' if m.is_ai else (m.sender_name or 'User')}: {m.body}" for m in msgs)
        records.append(MemoryRecord(
            source=MemorySourceRef("conversation", str(conv.id), "conversation", str(conv.external_id or conv.id), "shared"),
            title=conv.subject or f"Conversation {conv.id}",
            content=text,
            metadata={"conversation_id": str(conv.external_id or conv.id), "conversation_type": str(conv.conversation_type or "")},
        ))
    return records


def _document_records(db: Session) -> list[MemoryRecord]:
    records: list[MemoryRecord] = []
    docs = db.query(Document).filter(Document.org_id == resolve_org_id(), Document.context.isnot(None)).all()
    for doc in docs:
        records.append(MemoryRecord(
            source=MemorySourceRef("document", str(doc.id), "document", str(doc.id), "shared"),
            title=doc.filename,
            content=doc.context or "",
            metadata={"document_type": doc.document_type or "", "document_id": str(doc.id)},
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
    if intent in {"draft_message", "follow_up"} and item.source_type in {"tenant", "vendor", "conversation", "task"}:
        bump(1.5, "messaging intent prior")
    if intent in {"triage", "answer_question"} and item.source_type in {"lease", "task", "property", "unit"}:
        bump(1.2, "triage intent prior")
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

    actual_model = configured_model
    api_base = configured_base
    if "/" in configured_model and not api_base:
        provider_prefix, _, model_name = configured_model.partition("/")
        provider_bases = {
            "deepseek": "https://api.deepseek.com",
            "anthropic": "https://api.anthropic.com/v1",
            "openai": "https://api.openai.com/v1",
        }
        if provider_prefix in provider_bases:
            api_base = provider_bases[provider_prefix]
            actual_model = model_name
    if not api_base:
        api_base = "https://api.openai.com/v1"
    return actual_model, api_key, api_base


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
                "query": request.query,
                "model": actual_model,
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
            detail={"query": request.query, "error": str(exc)},
        )
        return ranked


def retrieve_context(db: Session, request: RetrievalRequest) -> RankedContextBundle:
    creator = request.creator_id or resolve_account_id()
    request.creator_id = creator
    request.org_id = request.org_id or resolve_org_id()

    sync_memory_index(db, creator_id=creator)

    items = _eligible_items(db, creator)
    query_tokens = set(_tokenize(request.query or request.intent))
    vector_scores: dict[str, float] = {}
    if items:
        index = ChromaMemoryIndex()
        vector_scores = index.query(request, where=_base_where(creator), n_results=min(50, max(10, request.limit * 4)))

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
            "query": request.query,
            "intent": request.intent,
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
