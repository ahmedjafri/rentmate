import os
import re
import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from backends.local_auth import _lookup_account_id

# Paths
TEMPLATE_DIR = Path(__file__).parent / "agent_mds"
_data_base = Path(os.environ.get("RENTMATE_DATA_DIR", str(Path(__file__).parent.parent / "data")))
DATA_DIR = _data_base / "agent"

_STATIC_TEMPLATE_FILES = ["SOUL.md"]

_VERSION_RE = re.compile(r"^#\s*soul_version:\s*(\d+)", re.MULTILINE)


def _soul_version(text: str) -> int:
    m = _VERSION_RE.search(text)
    return int(m.group(1)) if m else 0


def get_agent_workspace(agent_id: str) -> Path:
    return (DATA_DIR / str(agent_id)).resolve()


def ensure_agent_runtime_dirs(agent_id: str) -> dict[str, Path]:
    workspace = get_agent_workspace(agent_id)
    hermes_home = workspace
    home_dir = hermes_home / "home"
    workspace.mkdir(parents=True, exist_ok=True)
    home_dir.mkdir(parents=True, exist_ok=True)
    return {
        "workspace": workspace,
        "hermes_home": hermes_home,
        "working_dir": home_dir,
    }


def _register_rentmate_tools():
    """Register RentMate-specific tools with the agent tool registry."""
    from tools.registry import registry

    from llm.tools import (
        AnalyzeDocumentTool,
        CloseTaskTool,
        CreateDocumentTool,
        CreatePropertyTool,
        CreateScheduledTaskTool,
        CreateSuggestionTool,
        CreateTenantTool,
        CreateVendorTool,
        EditMemoryTool,
        LookupVendorsTool,
        MessageExternalPersonTool,
        ProposeTaskTool,
        ReadDocumentTool,
        RecallMemoryTool,
        SaveMemoryTool,
        UpdateOnboardingTool,
    )

    for tool_cls in (
        ProposeTaskTool, CloseTaskTool, MessageExternalPersonTool,
        LookupVendorsTool, CreateVendorTool,
        SaveMemoryTool, RecallMemoryTool, EditMemoryTool,
        CreatePropertyTool, CreateTenantTool, CreateSuggestionTool,
        CreateScheduledTaskTool,
        CreateDocumentTool,
        ReadDocumentTool, AnalyzeDocumentTool,
        UpdateOnboardingTool,
    ):
        tool = tool_cls()
        # Flat schema — get_definitions() wraps it in {"type":"function","function":...}
        schema = {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        }

        # Async handler — bridged via _run_async when is_async=True
        async def _handler(args, _tool=tool, **kwargs):
            return await _tool.execute(**args)

        registry.register(
            name=tool.name,
            toolset="rentmate",
            schema=schema,
            handler=_handler,
            is_async=True,
            description=tool.description,
        )


class AgentRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._ready: dict[str, bool] = {}  # account_id → True when workspace is populated
        self._tools_registered = False
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ─── Public lifecycle ─────────────────────────────────────────────────────

    def populate_all_agents(self, db: Session):
        agent_dir = get_agent_workspace(str(_lookup_account_id()))
        self._write_workspace(agent_dir, db, str(_lookup_account_id()))
        print("[agent] Workspace populated")

    def start_gateway(self, account_id: str | None = None):
        """Register RentMate tools (once). No persistent loop needed."""
        with self._lock:
            if not self._tools_registered:
                _register_rentmate_tools()
                self._tools_registered = True
                print("[agent] RentMate tools registered")
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
            agent_dir = get_agent_workspace(account_id)
            self._write_workspace(agent_dir, db, account_id)
            self.start_gateway(account_id)
        return account_id

    def get_loop(self, account_id: str | None = None):
        """Backward compat — returns None (no persistent loop)."""
        return None

    def build_system_prompt(self, account_id: str) -> str:
        """Build the full system prompt from workspace files + persistent memory."""
        agent_dir = get_agent_workspace(account_id)
        parts = []
        for filename in ["SOUL.md"]:
            path = agent_dir / filename
            if path.exists():
                content = path.read_text()
                parts.append(content)
                if filename == "SOUL.md":
                    print(f"[agent] SOUL.md: {len(content)} chars, v{_soul_version(content)}")
        # Inject persistent memory from DB
        from llm.memory_store import DbMemoryStore
        memory_context = DbMemoryStore(account_id).get_memory_context()
        if memory_context:
            parts.append(memory_context)
        return "\n\n---\n\n".join(parts)

    # ─── Channel management (Telegram/WhatsApp) ────────

    async def restart_channels_async(self, integrations: dict, account_id: str = ''):
        """Placeholder — channel management is handled separately from the agent."""
        tg = integrations.get("telegram", {})
        wa = integrations.get("whatsapp", {})
        any_enabled = tg.get("enabled", False) or wa.get("enabled", False)
        if not any_enabled:
            return
        print("[agent] External chat channels not yet supported")

    # ─── DB helpers for workspace files ───────────────────────────────────────

    @staticmethod
    def _db_read_file(db: Session, agent_id: str, filename: str) -> str | None:
        from db.models import AgentMemory
        creator_id = int(agent_id) if str(agent_id).isdigit() else _lookup_account_id()
        row = db.query(AgentMemory).filter_by(
            creator_id=creator_id, memory_type=f"file:{filename}"
        ).first()
        return row.content if row else None

    @staticmethod
    def _db_write_file(db: Session, agent_id: str, filename: str, content: str, *, creator_id: int | None = None):
        import uuid as _uuid

        from db.models import AgentMemory
        resolved_creator_id = creator_id or (int(agent_id) if str(agent_id).isdigit() else _lookup_account_id())
        row = db.query(AgentMemory).filter_by(
            creator_id=resolved_creator_id, memory_type=f"file:{filename}"
        ).first()
        now = datetime.now(UTC)
        if row:
            row.content = content
            row.updated_at = now
        else:
            db.add(AgentMemory(
                id=str(_uuid.uuid4()),
                creator_id=resolved_creator_id,
                memory_type=f"file:{filename}",
                content=content,
                updated_at=now,
            ))

    def _write_workspace(self, agent_dir: Path, db: Session, account_id: str = '', *, creator_id: int | None = None):
        ensure_agent_runtime_dirs(account_id)
        agent_dir.mkdir(parents=True, exist_ok=True)
        agent_id = account_id
        _cid = creator_id or (int(account_id) if account_id.isdigit() else None)

        for filename in _STATIC_TEMPLATE_FILES:
            dest = agent_dir / filename
            db_content = self._db_read_file(db, agent_id, filename)
            if db_content is not None:
                if filename == "SOUL.md":
                    src = TEMPLATE_DIR / filename
                    if src.exists():
                        new_v = _soul_version(src.read_text())
                        old_v = _soul_version(db_content)
                        if new_v > old_v:
                            db_content = src.read_text()
                            self._db_write_file(db, agent_id, filename, db_content, creator_id=_cid)
                            print(f"[agent] SOUL.md upgraded: v{old_v} → v{new_v}")
                dest.write_text(db_content)
                continue
            src = TEMPLATE_DIR / filename
            if src.exists():
                content = src.read_text()
                shutil.copy2(src, dest)
                self._db_write_file(db, agent_id, filename, content, creator_id=_cid)

        db.commit()


agent_registry = AgentRegistry()
