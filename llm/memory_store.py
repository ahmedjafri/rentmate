"""DB-backed memory store for the RentMate agent.

Supports entity-scoped notes (property, unit, tenant, vendor) and general
notes. Notes are stored in the agent_memory table with structured memory_type
keys for efficient filtering.

Memory types:
  - "note:general"           — global preferences, decisions
  - "note:property:{id}"     — property-specific context
  - "note:unit:{id}"         — unit-specific context
  - "note:tenant:{id}"       — tenant-specific context
  - "note:vendor:{id}"       — vendor-specific context
  - "long_term"              — legacy blob (backward compat)
"""
import json
import uuid
from datetime import UTC, datetime


class DbMemoryStore:
    """Reads/writes agent memory from the agent_memory DB table."""

    def __init__(self, agent_id: str):
        self.agent_id = agent_id

    def _get_db(self):
        from handlers.deps import SessionLocal
        return SessionLocal()

    # ── Entity-scoped notes ──────────────────────────────────────────────

    def _memory_type_key(self, entity_type: str, entity_id: str = "") -> str:
        if entity_type == "general" or not entity_id:
            return "note:general"
        return f"note:{entity_type}:{entity_id}"

    def add_note(
        self,
        content: str,
        entity_type: str = "general",
        entity_id: str = "",
        entity_label: str = "",
    ) -> str:
        from db.models import AgentMemory
        db = self._get_db()
        try:
            note_id = str(uuid.uuid4())
            now = datetime.now(UTC)
            meta = {}
            if entity_label:
                meta["label"] = entity_label
            if entity_type != "general":
                meta["entity_type"] = entity_type
                if entity_id:
                    meta["entity_id"] = entity_id

            db.add(AgentMemory(
                id=note_id,
                agent_id=self.agent_id,
                memory_type=self._memory_type_key(entity_type, entity_id),
                content=content,
                updated_at=now,
            ))
            db.commit()
            return note_id
        finally:
            db.close()

    def get_notes(
        self,
        entity_type: str | None = None,
        entity_id: str | None = None,
    ) -> list[dict]:
        from db.models import AgentMemory
        db = self._get_db()
        try:
            query = db.query(AgentMemory).filter(
                AgentMemory.agent_id == self.agent_id,
                AgentMemory.memory_type.like("note:%"),
            )
            if entity_type and entity_id:
                query = query.filter(
                    AgentMemory.memory_type == self._memory_type_key(entity_type, entity_id)
                )
            elif entity_type:
                query = query.filter(
                    AgentMemory.memory_type.like(f"note:{entity_type}%")
                )
            rows = query.order_by(AgentMemory.updated_at.desc()).all()
            notes = []
            for row in rows:
                # Parse entity info from memory_type key
                parts = row.memory_type.split(":")
                note = {
                    "id": row.id,
                    "content": row.content,
                    "entity_type": parts[1] if len(parts) > 1 else "general",
                    "updated_at": row.updated_at.isoformat() if row.updated_at else "",
                }
                if len(parts) > 2:
                    note["entity_id"] = parts[2]
                notes.append(note)
            return notes
        finally:
            db.close()

    # ── System prompt context ────────────────────────────────────────────

    def get_memory_context(self) -> str:
        """Build a memory block for the system prompt.

        Entity-scoped notes are grouped by type, general notes come last.
        """
        notes = self.get_notes()
        if not notes:
            # Fall back to legacy long_term memory
            legacy = self.read_long_term()
            if legacy:
                return f"## Long-term Memory\n{legacy}"
            return ""

        sections: dict[str, list[str]] = {}
        for note in notes:
            et = note["entity_type"]
            label = et.capitalize()
            if et not in sections:
                sections[et] = []
            entity_id = note.get("entity_id", "")
            prefix = f"[{entity_id[:8]}] " if entity_id else ""
            sections[et].append(f"- {prefix}{note['content']}")

        parts = ["## Memory Notes"]
        # Entity notes first, general last
        for et in ["property", "unit", "tenant", "vendor"]:
            if et in sections:
                parts.append(f"\n### {et.capitalize()}")
                parts.extend(sections[et])
        if "general" in sections:
            parts.append("\n### General")
            parts.extend(sections["general"])

        return "\n".join(parts)

    # ── Legacy blob (backward compat) ────────────────────────────────────

    def read_long_term(self) -> str:
        from db.models import AgentMemory
        db = self._get_db()
        try:
            row = (
                db.query(AgentMemory)
                .filter_by(agent_id=self.agent_id, memory_type="long_term")
                .first()
            )
            return row.content if row else ""
        finally:
            db.close()

    def write_long_term(self, content: str) -> None:
        from db.models import AgentMemory
        db = self._get_db()
        try:
            row = (
                db.query(AgentMemory)
                .filter_by(agent_id=self.agent_id, memory_type="long_term")
                .first()
            )
            now = datetime.now(UTC)
            if row:
                row.content = content
                row.updated_at = now
            else:
                db.add(AgentMemory(
                    id=str(uuid.uuid4()),
                    agent_id=self.agent_id,
                    memory_type="long_term",
                    content=content,
                    updated_at=now,
                ))
            db.commit()
        finally:
            db.close()
