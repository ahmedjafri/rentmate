import logging
import threading
from pathlib import Path

from sqlalchemy.orm import Session

from backends.local_storage import ensure_runtime_storage_contract

logger = logging.getLogger(__name__)

# Paths
TEMPLATE_DIR = Path(__file__).parent / "agent_mds"


def get_agent_data_dir() -> Path:
    """Resolve `<runtime-data>/agent`. The path is created lazily by callers
    that actually need it (e.g. the settings UI writing memory/MEMORY.md)."""
    data_dir, _ = ensure_runtime_storage_contract()
    return data_dir / "agent"


def get_agent_workspace(agent_id: str) -> Path:
    """Per-account workspace path. Used by the settings UI for the
    optional ``memory/MEMORY.md`` file. Not eager-created."""
    return (get_agent_data_dir() / str(agent_id)).resolve()


class AgentRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._ready: dict[str, bool] = {}

    # ─── Public lifecycle ─────────────────────────────────────────────────────

    def start_gateway(self, account_id: str | None = None):
        """Mark a per-account agent as ready."""
        with self._lock:
            aid = str(account_id)
            self._ready[aid] = True
            print(f"[agent] Agent ready for account {aid[:8]}…")

    def stop_gateway(self, account_id=None):
        aid = str(account_id)
        self._ready.pop(aid, None)
        print(f"[agent] Agent stopped for account {aid[:8]}…")

    def is_healthy(self, account_id=None) -> bool:
        aid = str(account_id)
        return self._ready.get(aid, False)

    def ensure_agent(self, account_id, db: Session) -> str:
        account_id = str(account_id)
        if account_id not in self._ready:
            self.start_gateway(account_id)
        return account_id

    def get_loop(self, account_id: str | None = None):
        """Backward compat — returns None (no persistent loop)."""
        return None

    def build_system_prompt(self, account_id: str, *, query: str | None = None) -> str:
        bundle = self.build_system_prompt_bundle(account_id, query=query)
        return bundle["system_prompt"]

    def build_system_prompt_bundle(
        self,
        account_id: str,
        *,
        query: str | None = None,
    ) -> dict[str, object]:
        """Build the system prompt: SOUL.md template + per-account memory block.

        SOUL.md is read directly from the in-repo template
        (``llm/agent_mds/SOUL.md``) — there is no per-account on-disk copy
        anymore. Persistent memory remains per-account via ``DbMemoryStore``,
        and ``query`` biases that retrieval toward the current user message.
        """
        parts: list[str] = []
        file_parts: list[dict[str, str]] = []
        soul_path = TEMPLATE_DIR / "SOUL.md"
        if soul_path.exists():
            content = soul_path.read_text()
            if "{{tools}}" in content:
                from llm.tool_docs import render_tools_markdown
                content = content.replace("{{tools}}", render_tools_markdown())
            parts.append(content)
            file_parts.append({"type": "file", "name": "SOUL.md", "content": content})
            print(f"[agent] SOUL.md: {len(content)} chars")

        from llm.memory_store import DbMemoryStore
        try:
            memory_context = DbMemoryStore(account_id).get_memory_context(query=query)
        except Exception as exc:
            logger.warning("Failed to load memory context for account %s: %s", account_id, exc)
            memory_context = ""
        if memory_context:
            parts.append(memory_context)
        return {
            "system_prompt": "\n\n---\n\n".join(parts),
            "memory_context": memory_context,
            "parts": file_parts,
        }

    # ─── Channel management (Telegram/WhatsApp) ────────

    async def restart_channels_async(self, integrations: dict, account_id: str = ''):
        """Placeholder — channel management is handled separately from the agent."""
        tg = integrations.get("telegram", {})
        wa = integrations.get("whatsapp", {})
        any_enabled = tg.get("enabled", False) or wa.get("enabled", False)
        if not any_enabled:
            return
        print("[agent] External chat channels not yet supported")


agent_registry = AgentRegistry()
