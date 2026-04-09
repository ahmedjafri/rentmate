import json
import os
import re
import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from backends.local_auth import DEFAULT_USER_ID

# Paths
AGENTS_DIR = Path(__file__).parent.parent / "agents"
TEMPLATE_DIR = AGENTS_DIR / "template"
_data_base = Path(os.environ.get("RENTMATE_DATA_DIR", str(Path(__file__).parent.parent / "data")))
DATA_DIR = _data_base / "agent"

_STATIC_TEMPLATE_FILES = ["AGENTS.md", "SOUL.md", "IDENTITY.md", "HEARTBEAT.md"]

_VERSION_RE = re.compile(r"^#\s*soul_version:\s*(\d+)", re.MULTILINE)


def _soul_version(text: str) -> int:
    m = _VERSION_RE.search(text)
    return int(m.group(1)) if m else 0


def _register_rentmate_tools():
    """Register RentMate-specific tools with the agent tool registry."""
    from tools.registry import registry

    from llm.tools import (
        AnalyzeDocumentTool,
        AttachEntityToTaskTool,
        CloseTaskTool,
        CreatePropertyTool,
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
        SetModeTool,
        UpdateOnboardingTool,
        UpdateStepsTool,
    )

    for tool_cls in (
        ProposeTaskTool, CloseTaskTool, SetModeTool,
        AttachEntityToTaskTool, MessageExternalPersonTool,
        LookupVendorsTool, CreateVendorTool, UpdateStepsTool,
        SaveMemoryTool, RecallMemoryTool, EditMemoryTool,
        CreatePropertyTool, CreateTenantTool, CreateSuggestionTool,
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
        agent_dir = DATA_DIR / DEFAULT_USER_ID
        self._write_workspace(agent_dir, db, DEFAULT_USER_ID)
        print("[agent] Workspace populated")

    def start_gateway(self, account_id: str | None = None):
        """Register RentMate tools (once). No persistent loop needed."""
        with self._lock:
            if not self._tools_registered:
                _register_rentmate_tools()
                self._tools_registered = True
                print("[agent] RentMate tools registered")
            aid = account_id or DEFAULT_USER_ID
            self._ready[aid] = True
            print(f"[agent] Agent ready for account {aid[:8]}…")

    def stop_gateway(self, account_id: str | None = None):
        aid = account_id or DEFAULT_USER_ID
        self._ready.pop(aid, None)
        print(f"[agent] Agent stopped for account {aid[:8]}…")

    def is_healthy(self, account_id: str | None = None) -> bool:
        aid = account_id or DEFAULT_USER_ID
        return self._ready.get(aid, False)

    def ensure_agent(self, account_id: str, db: Session) -> str:
        if account_id not in self._ready:
            agent_dir = DATA_DIR / account_id
            self._write_workspace(agent_dir, db, account_id)
            self.start_gateway(account_id)
        return account_id

    def get_loop(self, account_id: str | None = None):
        """Backward compat — returns None (no persistent loop)."""
        return None

    def build_system_prompt(self, account_id: str) -> str:
        """Build the full system prompt from workspace files + persistent memory."""
        agent_dir = DATA_DIR / account_id
        parts = []
        for filename in ["SOUL.md", "USER.md", "TOOLS.md"]:
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
        # Inject onboarding addendum if onboarding is active
        self._maybe_append_onboarding(parts)
        return "\n\n---\n\n".join(parts)

    @staticmethod
    def _maybe_append_onboarding(parts: list[str]) -> None:
        """Append ONBOARDING.md to the system prompt if onboarding is active."""
        from db.session import SessionLocal
        from gql.services.settings_service import get_onboarding_state

        db = SessionLocal.session_factory()
        try:
            state = get_onboarding_state(db)
            if not state or state.get("status") != "active":
                return
            onboarding_path = TEMPLATE_DIR / "ONBOARDING.md"
            if not onboarding_path.exists():
                return
            content = onboarding_path.read_text()
            content += f"\n\n## Current onboarding state\n```json\n{json.dumps(state, indent=2)}\n```"
            parts.append(content)
            print("[agent] Onboarding addendum injected into system prompt")
        finally:
            db.close()

    # ─── Channel management (Telegram/WhatsApp) ────────

    async def restart_channels_async(self, integrations: dict, account_id: str = DEFAULT_USER_ID):
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
        row = db.query(AgentMemory).filter_by(
            agent_id=agent_id, memory_type=f"file:{filename}"
        ).first()
        return row.content if row else None

    @staticmethod
    def _db_write_file(db: Session, agent_id: str, filename: str, content: str):
        import uuid as _uuid

        from db.models import AgentMemory
        row = db.query(AgentMemory).filter_by(
            agent_id=agent_id, memory_type=f"file:{filename}"
        ).first()
        now = datetime.now(UTC)
        if row:
            row.content = content
            row.updated_at = now
        else:
            db.add(AgentMemory(
                id=str(_uuid.uuid4()),
                agent_id=agent_id,
                memory_type=f"file:{filename}",
                content=content,
                updated_at=now,
            ))

    def _write_workspace(self, agent_dir: Path, db: Session, account_id: str = DEFAULT_USER_ID):
        agent_dir.mkdir(parents=True, exist_ok=True)
        agent_id = account_id

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
                            self._db_write_file(db, agent_id, filename, db_content)
                            print(f"[agent] SOUL.md upgraded: v{old_v} → v{new_v}")
                dest.write_text(db_content)
                continue
            src = TEMPLATE_DIR / filename
            if src.exists():
                content = src.read_text()
                shutil.copy2(src, dest)
                self._db_write_file(db, agent_id, filename, content)

        admin_email = os.environ.get("RENTMATE_ADMIN_EMAIL", "admin@localhost")
        account_name = os.environ.get("RENTMATE_ACCOUNT_NAME", "RentMate")

        user_md = agent_dir / "USER.md"
        db_user = self._db_read_file(db, agent_id, "USER.md")
        if db_user is not None:
            user_md.write_text(db_user)
        elif not user_md.exists():
            content = (
                f"# USER.md - About Your Manager\n\n"
                f"- **Name:** {admin_email}\n"
                f"- **Pronouns:** Unknown — use neutral language (they/them) until told otherwise\n"
                f"- **Account:** {account_name}\n"
                f"- **Role:** admin\n\n"
                f"_(Update this as you learn more about how they prefer to work.)_\n"
            )
            user_md.write_text(content)
            self._db_write_file(db, agent_id, "USER.md", content)

        data_script = Path(__file__).parent / "agent_data.py"
        workspace_abs = str((DATA_DIR / DEFAULT_USER_ID).resolve())

        (agent_dir / "TOOLS.md").write_text(
            "# TOOLS.md - Communication Channels & Data Access\n\n"
            "## Communication Channels\n\n"
            "- **SMS (Quo)** — Inbound/outbound tenant texts route through Quo. "
            "You reply automatically.\n"
            "- **Web Chat** — Property managers chat with you via the RentMate web interface.\n\n"
            "## Live Data\n\n"
            "Use the available tools to fetch live property/tenant/lease data. Always prefer a live "
            "query over any cached or remembered data — the database is the source of truth.\n\n"
            "### Data Operations\n\n"
            "| Operation | Description | Options |\n"
            "|-----------|-------------|--------|\n"
            "| `properties` | All properties with units, occupancy, and lease summary | |\n"
            "| `tenants` | All tenants with active lease info (unit, property, rent, status) | |\n"
            "| `leases` | All leases with tenant and property details | |\n"
            "| `tasks` | Task list (maintenance, lease issues, etc.) | `--category` `--status` |\n"
            "| `task` | Single task with full message thread | `--id <uid>` |\n"
            "| `messages` | Messages for a conversation/SMS thread | `--id <conversation-id>` |\n\n"
            "## Write Actions (require manager confirmation)\n\n"
            "All write operations are **queued for human confirmation** — they do not execute\n"
            "immediately. The manager's UI will show a confirmation card before any change is\n"
            "committed to the database. Never call these in response to ambiguous requests.\n\n"
            "### Write Operations\n\n"
            "| Operation | Description |\n"
            "|-----------|-------------|\n"
            "| `propose_task` | Propose a new task — manager must approve before it is created |\n"
            "| `close_task` | Request to close/resolve a task — manager must confirm |\n"
            "| `set_mode` | Request a task mode change — manager must confirm |\n"
            "| `update_steps` | Set or update progress steps for a task (immediate) |\n\n"
            "### DO NOT\n\n"
            "- **Do not install packages** (apt-get, pip, brew, etc.) to access data.\n"
            "- **Do not connect to the database directly** — do not use sqlite3, sqlalchemy, "
            "or any other library to open the database file.\n"
            "- **Do not search the filesystem for the database.**\n"
            "- **Do not write raw SQL.** Use the provided tools for all data operations.\n\n"
            "## Vendor Notes\n\n"
            "_(Add vendor contacts here as you learn them.)_\n"
        )
        tools_content = (agent_dir / "TOOLS.md").read_text()
        self._db_write_file(db, agent_id, "TOOLS.md", tools_content)

        db.commit()


agent_registry = AgentRegistry()
