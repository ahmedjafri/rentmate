"""Entity-context + task-note tools.

Two writers, two read/edit helpers:

- ``RememberAboutEntityTool`` — durable per-entity context (preferences,
  quirks, recurring patterns, stable constraints, stakeholder context,
  compliance). A heuristic gate (length, PII strip, ``note_kind`` shape,
  operational-phrasing reject, dedup) rejects low-signal saves so the
  retrieval index stays useful instead of accumulating noise.
- ``AddTaskNoteTool`` — short progress notes appended to ``Task.context``
  for in-flight task state. No gate; task scope is by definition
  short-lived and the agent already has plenty of friction recording it.
- ``RecallMemoryTool`` — read-only fetch of an entity's combined
  shared (``HasContext.context``) + private (``EntityNote``) notes.
- ``EditMemoryTool`` — replace the full context for one entity (compact,
  correct, or clear). Used after ``recall_memory`` returns notes the
  agent wants to consolidate.

All four reject placeholder ids (e.g. ``"tenant_id_from_context"``) up
front via ``_check_placeholder_ids`` so junk never hits the persistence
layer.

The old ``SaveMemoryTool`` (and its ``general``/``task``/``entity`` scope
discriminator) is gone; ``general`` was duplicating ``DbMemoryStore``
and ``task`` is now ``add_task_note``. See
``llm/agent_mds/SOUL.md`` for the recording policy the agent reads.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from agent.tools._common import (
    Tool,
    ToolMode,
    _check_placeholder_ids,
    _load_entity_by_public_id,
    _public_entity_id,
    tool_session,
)
from integrations.local_auth import resolve_account_id

# ─── Heuristic gate primitives (shared by RememberAboutEntityTool) ────

# Order matters — replacements run sequentially. Each pattern is paired
# with a placeholder so the post-strip text still scans grammatically;
# the gate then re-checks length, so notes that *only* contained the
# stripped values get rejected as too short.
_PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # UUIDs — internal pks, external_ids, anything in canonical form
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "[id]"),
    # Email
    (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "[email]"),
    # NANP-shaped phone numbers
    (re.compile(r"\+?1?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"), "[phone]"),
    # ``tenant_id``, ``lease_id``, etc. mentioned in prose
    (re.compile(r"\b(tenant|lease|property|unit|document|vendor|task)_id(s)?\b", re.I), r"\1"),
    # ``lease 123``, ``tenant #45`` style references
    (re.compile(r"\b(lease|tenant|property|unit|document|vendor|task)\s*#?\s*\d{2,}\b", re.I), r"\1"),
]

# Phrases that signal the note is operational/transient — these should
# live on a Task or in a chat message, not in durable entity context.
_TRANSIENT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bwaiting (for|on|to)\b", re.I),
    re.compile(r"\bI\s+(just|am going to|am about to|will|need to)\b", re.I),
    re.compile(r"\babout to\b", re.I),
    re.compile(r"\b(today|tomorrow|tonight|this (morning|afternoon|evening|week))\b", re.I),
    re.compile(r"\bnext (mon|tues|wed|thurs|fri|satur|sun)day\b", re.I),
    re.compile(r"\bcurrently (working on|trying to|resolving)\b", re.I),
    re.compile(r"\bin progress\b", re.I),
]

_FREQUENCY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b\d+\s*(time|times|month|months|year|years|week|weeks|day|days)\b", re.I),
    re.compile(r"\b(recurring|every|whenever|annually|quarterly|monthly|weekly|seasonal)\b", re.I),
]

_COMPLIANCE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"§\s*\d+"),               # §37.3
    re.compile(r"\bRCW\s*\d+"),            # WA Revised Code
    re.compile(r"\b\d{1,3}\.\d{1,3}\b"),   # 37.3 etc.
    re.compile(r"\b(code|ordinance|notice|law|statute|required|regulation|RCW|HUD|HOA)\b", re.I),
]

_VALID_NOTE_KINDS = (
    "preference", "quirk", "pattern", "constraint", "stakeholder_context", "compliance",
)
_VALID_ENTITY_TYPES = ("property", "unit", "tenant", "vendor", "document")

_MIN_LEN = 30
_MAX_LEN = 400


def _strip_pii(content: str) -> str:
    """Remove IDs, emails, phone numbers, and lease/property/etc.
    bookkeeping references. Tightens whitespace afterward so the
    post-strip note doesn't read like a redacted document."""
    out = content
    for pattern, repl in _PII_PATTERNS:
        out = pattern.sub(repl, out)
    # Collapse runs of whitespace + the stub markers we left behind.
    out = re.sub(r"\s+", " ", out).strip()
    return out


def _has_frequency_anchor(content: str) -> bool:
    return any(p.search(content) for p in _FREQUENCY_PATTERNS)


def _has_compliance_anchor(content: str) -> bool:
    return any(p.search(content) for p in _COMPLIANCE_PATTERNS)


def _is_operational(content: str) -> bool:
    return any(p.search(content) for p in _TRANSIENT_PATTERNS)


def _normalize_for_dedup(text: str) -> set[str]:
    """Lowercase + tokenize on word boundaries; drop stopwords-ish
    fillers so two paraphrases with different glue words still collide.
    Cheap; if false-positives bite, swap in proper stemming later."""
    fillers = {
        "the", "a", "an", "of", "to", "and", "or", "in", "on", "for",
        "with", "is", "are", "was", "were", "be", "been", "this", "that",
        "by", "as", "at", "from", "it", "its",
    }
    tokens = {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in fillers}
    return tokens


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


# Threshold biased toward catching paraphrases: agents often re-state
# the same fact with different glue words, and sending them to
# ``edit_memory`` is cheap. The cost of a false-positive reject is
# low — the agent gets a specific error pointing at edit_memory.
_DEDUP_THRESHOLD = 0.6


def _split_existing_notes(blob: str | None) -> list[str]:
    """Notes are joined with ``\\n---\\n`` (plus historical newline-only
    separators). Splitting handles both."""
    if not blob:
        return []
    chunks: list[str] = []
    for major in (blob or "").split("\n---\n"):
        for line in major.splitlines():
            line = line.strip()
            if line:
                chunks.append(line)
    return chunks


# ─── Tools ────────────────────────────────────────────────────────────


class RememberAboutEntityTool(Tool):
    """Save a durable, high-signal note about a property/unit/tenant/vendor/document.

    The heuristic gate (length bounds, PII strip, kind-specific shape
    check, operational-phrase reject, Jaccard dedup) is intentionally
    strict — retrieval surfaces these notes into every future agent
    invocation that touches the same entity, so noise here directly
    wastes context budget on every later turn.
    """

    @property
    def name(self) -> str:
        return "remember_about_entity"

    @property
    def description(self) -> str:
        return (
            "Save a 1-3 sentence durable note about an entity "
            "(property, unit, tenant, vendor, document) — the kind of "
            "thing you'd want to know next time you touch this entity: "
            "preferences, quirks, recurring patterns, stable constraints, "
            "stakeholder context, compliance. NOT for today's task "
            "progress, contact info, IDs, or anything already in the "
            "schema (lease end dates, rent amounts, unit labels, "
            "phone/email — retrieval pulls those automatically). The "
            "server enforces a relevance gate; if your note is "
            "operational, too short, all-PII, or near-duplicate of an "
            "existing note you'll get a specific rejection telling you "
            "what to fix or what tool to use instead."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["entity_type", "entity_id", "note_kind", "content"],
            "properties": {
                "entity_type": {
                    "type": "string",
                    "enum": list(_VALID_ENTITY_TYPES),
                },
                "entity_id": {
                    "type": "string",
                    "description": (
                        "Entity external UUID from lookup_properties / "
                        "lookup_tenants / etc. Do not pass internal "
                        "integer pks or placeholder strings."
                    ),
                },
                "note_kind": {
                    "type": "string",
                    "enum": list(_VALID_NOTE_KINDS),
                    "description": (
                        "preference (recipient/owner choice), quirk "
                        "(specific oddity), pattern (recurring "
                        "behavior — REQUIRES a frequency anchor like "
                        "'every 6 months' or 'third time'), constraint "
                        "(rule that bounds future actions), "
                        "stakeholder_context (owner/PM relationship "
                        "facts), compliance (regulatory — REQUIRES a "
                        "citation or regulatory keyword)."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": (
                        "1-3 sentences. 30-400 characters after PII "
                        "stripping. State the durable fact; do not "
                        "include IDs, phone numbers, emails, or "
                        "today's operational details."
                    ),
                },
                "visibility": {
                    "type": "string",
                    "enum": ["shared", "private"],
                    "description": (
                        "shared (default) writes to the entity row "
                        "visible to every account in this org. private "
                        "writes to a per-creator EntityNote — use only "
                        "for assessments you don't want other accounts "
                        "to see."
                    ),
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        # Placeholder ids first so the early reject doesn't waste a DB
        # round-trip on the lookup below.
        err = _check_placeholder_ids(kwargs, [("entity_id", None)])
        if err:
            return err

        entity_type = (kwargs.get("entity_type") or "").strip().lower()
        entity_id = (kwargs.get("entity_id") or "").strip()
        note_kind = (kwargs.get("note_kind") or "").strip().lower()
        raw_content = (kwargs.get("content") or "").strip()
        visibility = (kwargs.get("visibility") or "shared").strip().lower()

        if entity_type not in _VALID_ENTITY_TYPES:
            return _err(f"entity_type must be one of {list(_VALID_ENTITY_TYPES)}, got {entity_type!r}.")
        if note_kind not in _VALID_NOTE_KINDS:
            return _err(f"note_kind must be one of {list(_VALID_NOTE_KINDS)}, got {note_kind!r}.")
        if visibility not in ("shared", "private"):
            return _err(f"visibility must be 'shared' or 'private', got {visibility!r}.")
        if not raw_content:
            return _err("content is required.")

        # ── Gate step 1: PII strip (so length check applies to the
        #    persisted form, not the noisy input).
        cleaned = _strip_pii(raw_content)
        if not cleaned:
            return _err("content was entirely IDs / contact info — nothing durable to save.")

        # ── Gate step 2: length bounds on the cleaned form.
        if len(cleaned) < _MIN_LEN:
            return _err(
                f"content is too short ({len(cleaned)} chars after PII strip; "
                f"need ≥ {_MIN_LEN}). State a durable fact in 1-3 sentences."
            )
        if len(cleaned) > _MAX_LEN:
            return _err(
                f"content is too long ({len(cleaned)} chars; max {_MAX_LEN}). "
                "Tighten to 1-3 sentences — longer notes are noisier on retrieval."
            )

        # ── Gate step 3: operational/transient phrasing.
        if _is_operational(cleaned):
            return _err(
                "content reads as operational/transient (today's plan, "
                "in-progress work). That belongs on the task — use "
                "add_task_note. Entity context is for durable facts."
            )

        # ── Gate step 4: kind-specific shape requirements.
        if note_kind == "pattern" and not _has_frequency_anchor(cleaned):
            return _err(
                "note_kind='pattern' requires a frequency anchor "
                "(e.g. 'third time in 18 months', 'every winter', "
                "'recurring quarterly'). Either add one or pick "
                "a different note_kind (quirk for one-offs)."
            )
        if note_kind == "compliance" and not _has_compliance_anchor(cleaned):
            return _err(
                "note_kind='compliance' requires a citation or "
                "regulatory keyword (e.g. '§37.3', 'RCW 59.18', "
                "'HOA notice rule', 'code-required'). Add one or "
                "pick a different note_kind."
            )

        # ── Persist + dedup against existing notes for this entity.
        with tool_session() as db:
            entity = _load_entity_by_public_id(db, entity_type, entity_id)
            if entity is None:
                return _err(
                    f"{entity_type} {entity_id!r} not found. Pass the external "
                    "UUID from lookup_properties / lookup_tenants / etc. — "
                    "internal integer pks aren't valid here."
                )

            from sqlalchemy.orm.attributes import flag_modified

            from db.models import EntityNote

            creator_id = resolve_account_id()
            public_entity_id = _public_entity_id(entity)

            shared_existing = entity.context or ""
            private_note: EntityNote | None = (
                db.query(EntityNote)
                .filter_by(
                    creator_id=creator_id,
                    entity_type=entity_type,
                    entity_id=public_entity_id,
                )
                .first()
            )
            private_existing = private_note.content if private_note else ""

            # Dedup considers BOTH visibilities — a private note that
            # echoes a shared one is still noise.
            existing_chunks = _split_existing_notes(shared_existing) + _split_existing_notes(private_existing)
            new_tokens = _normalize_for_dedup(cleaned)
            for chunk in existing_chunks:
                # Strip the leading ``[YYYY-MM-DD] (kind)`` prefix the
                # writer adds so the date+kind don't dominate the
                # similarity score.
                bare = re.sub(r"^\[[^\]]+\]\s*(\([^)]+\)\s*)?", "", chunk)
                if _jaccard(_normalize_for_dedup(bare), new_tokens) >= _DEDUP_THRESHOLD:
                    return _err(
                        f"Similar note already saved: {bare[:120]!r}. "
                        "Use edit_memory to update it instead of adding a "
                        "near-duplicate."
                    )

            # ── Write.
            now = datetime.now(UTC)
            stamped = f"[{now.strftime('%Y-%m-%d')}] ({note_kind}) {cleaned}"
            label = (
                getattr(entity, "name", None)
                or getattr(entity, "label", None)
                or entity_type
            )

            if visibility == "shared":
                entity.context = (f"{shared_existing}\n---\n{stamped}".strip()
                                  if shared_existing else stamped)
                flag_modified(entity, "context")
            else:
                if private_note is not None:
                    private_note.content = (
                        f"{private_existing}\n---\n{stamped}".strip()
                        if private_existing else stamped
                    )
                    private_note.updated_at = now
                else:
                    db.add(EntityNote(
                        creator_id=creator_id,
                        entity_type=entity_type,
                        entity_id=public_entity_id,
                        content=stamped,
                        created_at=now,
                        updated_at=now,
                    ))

        # TODO(memory): swap heuristic gate for a small Haiku classifier
        # call once heuristic false-positives become annoying. Score
        # durability + tighten the summary in one shot.
        return json.dumps({
            "status": "ok",
            "message": f"Saved {note_kind} note for {label}.",
            "note_kind": note_kind,
            "entity_id": public_entity_id,
            "applied_summary": cleaned,
        })


class AddTaskNoteTool(Tool):
    """Append a short progress note to a task's ``context`` column.

    Companion to ``remember_about_entity``: this is the right place for
    operational state ("vendor confirmed Tue 2pm", "tenant uploaded
    consent form"). The note gets stamped with a date but otherwise
    concatenated to ``Task.context``; the gate is just length bounds +
    PII strip + ``task_id`` placeholder check.
    """

    @property
    def name(self) -> str:
        return "add_task_note"

    @property
    def description(self) -> str:
        return (
            "Append a short progress note to the current task. Use "
            "this for in-flight task state: appointments confirmed, "
            "documents received, decisions made. NOT for durable "
            "per-entity context (that's remember_about_entity) or for "
            "outbound communications (that's message_person). The "
            "note appears in the task's ``context`` column for the "
            "next agent turn on this task."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["task_id", "note"],
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Real task id (numeric per-org id from list_tasks).",
                },
                "note": {
                    "type": "string",
                    "description": "Short progress note. Up to 500 chars after PII strip.",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        err = _check_placeholder_ids(kwargs, [("task_id", "list_tasks")])
        if err:
            return err

        task_id = (kwargs.get("task_id") or "").strip()
        raw_note = (kwargs.get("note") or "").strip()
        if not task_id:
            return _err("task_id is required.")
        if not raw_note:
            return _err("note is required.")

        cleaned = _strip_pii(raw_note)
        if not cleaned:
            return _err("note was entirely IDs / contact info — nothing to save.")
        if len(cleaned) > 500:
            cleaned = cleaned[:500].rstrip() + "…"

        with tool_session() as db:
            from db.models import Task as TaskModel
            task = db.query(TaskModel).filter_by(id=task_id).first()
            if task is None:
                return _err(f"Task {task_id} not found.")

            now = datetime.now(UTC).strftime("%Y-%m-%d")
            entry = f"[{now}] {cleaned}"
            task.context = f"{task.context}\n{entry}".strip() if task.context else entry

        return json.dumps({"status": "ok", "message": "Task note saved.", "applied_summary": cleaned})


class RecallMemoryTool(Tool):
    """Read back stored context notes, optionally filtered by entity."""

    mode = ToolMode.READ_ONLY

    @property
    def name(self) -> str:
        return "recall_memory"

    @property
    def description(self) -> str:
        return (
            "Read your long-term memory notes for one or more entities. "
            "Returns shared (entity row) + private (per-creator) notes "
            "for the requested entity_type / entity_id. Useful before "
            "calling edit_memory (consolidate/correct) or before "
            "messaging an entity (refresh durable context)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "entity_type": {
                    "type": "string",
                    "enum": list(_VALID_ENTITY_TYPES),
                    "description": "Filter by entity type. Omit to query general notes.",
                },
                "entity_id": {
                    "type": "string",
                    "description": (
                        "Filter to a specific entity (external UUID from "
                        "lookup_*). Omit to get every entity of the given "
                        "type that has notes."
                    ),
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        err = _check_placeholder_ids(kwargs, [("entity_id", None)])
        if err:
            return err

        entity_type = (kwargs.get("entity_type") or "").strip().lower() or None
        entity_id = (kwargs.get("entity_id") or "").strip() or None

        # Without an entity_type, fall back to agent-level memory store
        # — used to be ``general`` scope on SaveMemoryTool.
        if entity_type is None:
            from agent.memory_store import DbMemoryStore
            store = DbMemoryStore(str(resolve_account_id()))
            notes = store.get_notes(entity_type="general")
            if not notes:
                return json.dumps({"notes": [], "message": "No general notes found."})
            return json.dumps({"notes": notes, "count": len(notes)})

        if entity_type not in _VALID_ENTITY_TYPES:
            return _err(f"entity_type must be one of {list(_VALID_ENTITY_TYPES)}, got {entity_type!r}.")

        _MODEL_NAMES = {
            "property": "Property",
            "unit": "Unit",
            "tenant": "Tenant",
            "vendor": "User",
            "document": "Document",
        }

        import db.models as models
        from db.models import EntityNote
        from db.session import SessionLocal

        db = SessionLocal.session_factory()
        try:
            model_cls = getattr(models, _MODEL_NAMES[entity_type])
            creator_id = resolve_account_id()

            if entity_id:
                entity = _load_entity_by_public_id(db, entity_type, entity_id)
                entities = [entity] if entity else []
            else:
                entities = db.query(model_cls).all()

            results: list[dict[str, Any]] = []
            for e in entities:
                if not e:
                    continue
                label = (
                    getattr(e, "name", None)
                    or getattr(e, "label", None)
                    or str(getattr(e, "id", ""))[:8]
                )
                shared = e.context or ""
                public_eid = _public_entity_id(e)
                private_note = (
                    db.query(EntityNote)
                    .filter_by(creator_id=creator_id, entity_type=entity_type, entity_id=public_eid)
                    .first()
                )
                private = private_note.content if private_note else ""
                if shared or private:
                    results.append({
                        "entity_type": entity_type,
                        "entity_id": public_eid,
                        "label": label,
                        "shared_context": shared,
                        "private_notes": private,
                    })
            if not results:
                return json.dumps({"notes": [], "message": f"No {entity_type} context found."})
            return json.dumps({"notes": results, "count": len(results)})
        finally:
            db.close()


class EditMemoryTool(Tool):
    """Replace the entire context for an entity — compact, correct, or clear notes."""

    @property
    def name(self) -> str:
        return "edit_memory"

    @property
    def description(self) -> str:
        return (
            "Replace the full context notes for an entity. Use this to "
            "remove stale entries, compact verbose notes, or correct "
            "mistakes. First call recall_memory to read the current "
            "notes, then call edit_memory with the cleaned-up "
            "version. Pass an empty string to clear all notes for an "
            "entity."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["entity_type", "entity_id", "new_context"],
            "properties": {
                "entity_type": {
                    "type": "string",
                    "enum": list(_VALID_ENTITY_TYPES),
                },
                "entity_id": {
                    "type": "string",
                    "description": "External UUID from lookup_*. Internal pks are rejected.",
                },
                "new_context": {
                    "type": "string",
                    "description": "Replacement text (PII still gets stripped). Empty = clear.",
                },
                "visibility": {
                    "type": "string",
                    "enum": ["shared", "private"],
                    "description": (
                        "shared (default) edits the entity row visible "
                        "to all accounts; private edits this account's "
                        "EntityNote."
                    ),
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        err = _check_placeholder_ids(kwargs, [("entity_id", None)])
        if err:
            return err

        entity_type = (kwargs.get("entity_type") or "").strip().lower()
        entity_id = (kwargs.get("entity_id") or "").strip()
        raw_new = kwargs.get("new_context")
        if raw_new is None:
            return _err("new_context is required (pass empty string to clear).")
        visibility = (kwargs.get("visibility") or "shared").strip().lower()

        if entity_type not in _VALID_ENTITY_TYPES:
            return _err(f"entity_type must be one of {list(_VALID_ENTITY_TYPES)}, got {entity_type!r}.")
        if visibility not in ("shared", "private"):
            return _err(f"visibility must be 'shared' or 'private', got {visibility!r}.")

        cleaned = _strip_pii(str(raw_new)).strip()

        with tool_session() as db:
            entity = _load_entity_by_public_id(db, entity_type, entity_id)
            if entity is None:
                return _err(
                    f"{entity_type} {entity_id!r} not found. Pass the external UUID."
                )
            label = (
                getattr(entity, "name", None)
                or getattr(entity, "label", None)
                or entity_type
            )

            if visibility == "shared":
                from sqlalchemy.orm.attributes import flag_modified
                entity.context = cleaned or None
                flag_modified(entity, "context")
                action = "cleared" if not cleaned else "updated"
                return json.dumps({"status": "ok", "message": f"Shared context {action} for {label}."})

            from db.models import EntityNote
            creator_id = resolve_account_id()
            public_eid = _public_entity_id(entity)
            note = (
                db.query(EntityNote)
                .filter_by(creator_id=creator_id, entity_type=entity_type, entity_id=public_eid)
                .first()
            )
            now = datetime.now(UTC)
            if cleaned:
                if note is not None:
                    note.content = cleaned
                    note.updated_at = now
                else:
                    db.add(EntityNote(
                        creator_id=creator_id,
                        entity_type=entity_type,
                        entity_id=public_eid,
                        content=cleaned,
                        created_at=now,
                        updated_at=now,
                    ))
            elif note is not None:
                db.delete(note)
        action = "cleared" if not cleaned else "updated"
        return json.dumps({"status": "ok", "message": f"Private notes {action} for {label}."})


def _err(message: str) -> str:
    return json.dumps({"status": "error", "message": message})


__all__ = [
    "AddTaskNoteTool",
    "EditMemoryTool",
    "RecallMemoryTool",
    "RememberAboutEntityTool",
]
