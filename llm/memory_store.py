"""DB-backed memory store for nanobot agent.

Replaces the default file-based MemoryStore so that agent long-term memory
and conversation history survive container restarts without needing disk
persistence.
"""
import uuid
from datetime import UTC, datetime
from pathlib import Path

from nanobot.agent.memory import MemoryStore


class DbMemoryStore(MemoryStore):
    """MemoryStore subclass that reads/writes from the agent_memory table."""

    def __init__(self, workspace: Path, agent_id: str):
        super().__init__(workspace)
        self.agent_id = agent_id

    def _get_db(self):
        from handlers.deps import SessionLocal
        return SessionLocal()

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

    def append_history(self, entry: str) -> None:
        from db.models import AgentMemory
        db = self._get_db()
        try:
            # Append to a single history row per agent (cheaper than one row per entry)
            row = (
                db.query(AgentMemory)
                .filter_by(agent_id=self.agent_id, memory_type="history")
                .first()
            )
            now = datetime.now(UTC)
            if row:
                row.content = row.content + entry.rstrip() + "\n\n"
                row.updated_at = now
            else:
                db.add(AgentMemory(
                    id=str(uuid.uuid4()),
                    agent_id=self.agent_id,
                    memory_type="history",
                    content=entry.rstrip() + "\n\n",
                    updated_at=now,
                ))
            db.commit()
        finally:
            db.close()

    def get_memory_context(self) -> str:
        long_term = self.read_long_term()
        return f"## Long-term Memory\n{long_term}" if long_term else ""
