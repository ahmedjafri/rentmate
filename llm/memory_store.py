"""DB-backed memory store for the RentMate agent.

Entity-scoped context is stored directly on entity tables (properties, units,
tenants, external_contacts) via the `context` column. General notes use the
agent_memory table.
"""
import uuid
from datetime import UTC, datetime


class DbMemoryStore:
    """Reads/writes agent memory from entity context columns + agent_memory."""

    def __init__(self, agent_id: str):
        self.agent_id = agent_id

    def _get_db(self):
        from handlers.deps import SessionLocal
        return SessionLocal.session_factory()

    # ── General notes (agent_memory table) ───────────────────────────────

    def add_note(self, content: str, *, entity_type: str = "general",
                 entity_id: str = "", entity_label: str = "") -> str:
        """Add a general note to agent_memory."""
        from db.models import AgentMemory
        db = self._get_db()
        try:
            note_id = str(uuid.uuid4())
            db.add(AgentMemory(
                id=note_id,
                agent_id=self.agent_id,
                memory_type="note:general",
                content=content,
                updated_at=datetime.now(UTC),
            ))
            db.commit()
            return note_id
        finally:
            db.close()

    def get_notes(self, entity_type: str | None = None,
                  entity_id: str | None = None) -> list[dict]:
        """Get general notes from agent_memory."""
        from db.models import AgentMemory
        db = self._get_db()
        try:
            rows = (
                db.query(AgentMemory)
                .filter(
                    AgentMemory.agent_id == self.agent_id,
                    AgentMemory.memory_type == "note:general",
                )
                .order_by(AgentMemory.updated_at.desc())
                .all()
            )
            return [{"content": r.content, "entity_type": "general"} for r in rows]
        finally:
            db.close()

    # ── System prompt context ────────────────────────────────────────────

    def get_memory_context(self) -> str:
        """Build a memory block for the system prompt from entity context columns."""
        db = self._get_db()
        try:
            parts = []

            # Entity context from the actual tables
            from db.models import ExternalContact, Property, Tenant, Unit
            from db.queries import format_address

            props = db.query(Property).filter(Property.context.isnot(None)).all()
            if props:
                parts.append("### Properties")
                for p in props:
                    label = p.name or format_address(p)
                    parts.append(f"**{label}**\n{p.context}")

            units = db.query(Unit).filter(Unit.context.isnot(None)).all()
            if units:
                parts.append("### Units")
                for u in units:
                    parts.append(f"**{u.label}**\n{u.context}")

            tenants = db.query(Tenant).filter(Tenant.context.isnot(None)).all()
            if tenants:
                parts.append("### Tenants")
                for t in tenants:
                    name = f"{t.first_name} {t.last_name}".strip()
                    parts.append(f"**{name}**\n{t.context}")

            vendors = db.query(ExternalContact).filter(ExternalContact.context.isnot(None)).all()
            if vendors:
                parts.append("### Vendors")
                for v in vendors:
                    parts.append(f"**{v.name}**\n{v.context}")

            # General notes from agent_memory
            from db.models import AgentMemory
            general = (
                db.query(AgentMemory)
                .filter(
                    AgentMemory.agent_id == self.agent_id,
                    AgentMemory.memory_type == "note:general",
                )
                .order_by(AgentMemory.updated_at.desc())
                .limit(20)
                .all()
            )
            if general:
                parts.append("### General")
                for g in general:
                    parts.append(f"- {g.content}")

            if not parts:
                # Fall back to legacy long_term
                legacy = self.read_long_term()
                if legacy:
                    return f"## Memory Notes\n{legacy}"
                return ""

            return "## Memory Notes\n\n" + "\n\n".join(parts)
        finally:
            db.close()

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
