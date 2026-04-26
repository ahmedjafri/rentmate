"""DB-backed memory store for the RentMate agent."""
import uuid
from datetime import UTC, datetime

from backends.local_auth import resolve_account_id, resolve_org_id
from llm.retrieval import RetrievalRequest, compose_prompt_context, retrieve_context


class DbMemoryStore:
    """Reads/writes agent memory from entity context columns + agent_memory."""

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.creator_id = int(agent_id) if str(agent_id).isdigit() else resolve_account_id()
        self.org_id = resolve_org_id()

    def _get_db(self):
        from db.session import SessionLocal
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
                org_id=self.org_id,
                creator_id=self.creator_id,
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
                    AgentMemory.org_id == self.org_id,
                    AgentMemory.agent_id == self.agent_id,
                    AgentMemory.creator_id == self.creator_id,
                    AgentMemory.memory_type == "note:general",
                )
                .order_by(AgentMemory.updated_at.desc())
                .all()
            )
            return [{"content": r.content, "entity_type": "general"} for r in rows]
        finally:
            db.close()

    # ── System prompt context ────────────────────────────────────────────

    def get_memory_context(self, query: str | None = None) -> str:
        """Build a memory block for the system prompt via ranked retrieval.

        ``query`` should be the user's current message so retrieval ranks
        memories relevant to the immediate ask. Falls back to a generic
        account-overview query when no message is available (e.g. during
        agent warmup, migrations, or surfaces that build the prompt
        outside a chat turn).
        """
        ranking_query = (query or "").strip() or (
            "property management account overview memory notes "
            "active leases vendors tasks"
        )
        db = self._get_db()
        try:
            bundle = retrieve_context(db, RetrievalRequest(
                surface="system_prompt",
                intent="system_prompt",
                query=ranking_query,
                creator_id=self.creator_id,
                org_id=self.org_id,
                limit=12,
            ))
            block = compose_prompt_context(bundle, title="Memory Notes")
            if block:
                return block
            legacy = self.read_long_term()
            return f"## Memory Notes\n{legacy}" if legacy else ""
        finally:
            db.close()

    # ── Legacy blob (backward compat) ────────────────────────────────────

    def read_long_term(self) -> str:
        from db.models import AgentMemory
        db = self._get_db()
        try:
            row = (
                db.query(AgentMemory)
                .filter_by(org_id=self.org_id, agent_id=self.agent_id, creator_id=self.creator_id, memory_type="long_term")
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
                .filter_by(org_id=self.org_id, agent_id=self.agent_id, creator_id=self.creator_id, memory_type="long_term")
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
                    org_id=self.org_id,
                    creator_id=self.creator_id,
                    memory_type="long_term",
                    content=content,
                    updated_at=now,
                ))
            db.commit()
        finally:
            db.close()
