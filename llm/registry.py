import asyncio
import os
import re
import shutil
import sys
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
    """Register RentMate-specific tools with the Hermes tool registry."""
    from tools.registry import registry
    from llm.tools import (
        ProposeTaskTool, CloseTaskTool, SetModeTool,
        AttachVendorTool, LookupVendorsTool, CreateVendorTool, UpdateStepsTool,
        SaveMemoryTool, RecallMemoryTool,
    )

    for tool_cls in (
        ProposeTaskTool, CloseTaskTool, SetModeTool,
        AttachVendorTool, LookupVendorsTool, CreateVendorTool, UpdateStepsTool,
        SaveMemoryTool, RecallMemoryTool,
    ):
        tool = tool_cls()
        # Flat schema — get_definitions() wraps it in {"type":"function","function":...}
        schema = {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        }

        # Async handler — Hermes bridges it via _run_async when is_async=True
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
        print("[hermes] Workspace populated")

    def start_gateway(self, account_id: str | None = None):
        """Register RentMate tools with Hermes (once). No persistent loop needed."""
        with self._lock:
            if not self._tools_registered:
                _register_rentmate_tools()
                self._tools_registered = True
                print("[hermes] RentMate tools registered")
            aid = account_id or DEFAULT_USER_ID
            self._ready[aid] = True
            print(f"[hermes] Agent ready for account {aid[:8]}…")

    def stop_gateway(self, account_id: str | None = None):
        aid = account_id or DEFAULT_USER_ID
        self._ready.pop(aid, None)
        print(f"[hermes] Agent stopped for account {aid[:8]}…")

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
        """Backward compat — returns None (no persistent loop in Hermes mode)."""
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
                    print(f"[hermes] SOUL.md: {len(content)} chars, v{_soul_version(content)}")
        # Inject persistent memory from DB
        from llm.memory_store import DbMemoryStore
        memory_context = DbMemoryStore(account_id).get_memory_context()
        if memory_context:
            parts.append(memory_context)
        return "\n\n---\n\n".join(parts)

    # ─── Channel management (Telegram/WhatsApp — not Hermes-specific) ────────

    async def restart_channels_async(self, integrations: dict, account_id: str = DEFAULT_USER_ID):
        """Placeholder — channel management is handled separately from the agent."""
        tg = integrations.get("telegram", {})
        wa = integrations.get("whatsapp", {})
        any_enabled = tg.get("enabled", False) or wa.get("enabled", False)
        if not any_enabled:
            return
        print("[hermes] External chat channels not yet supported in Hermes mode")

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
                            print(f"[hermes] SOUL.md upgraded: v{old_v} → v{new_v}")
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
            f"# TOOLS.md - Communication Channels & Data Access\n\n"
            f"## Communication Channels\n\n"
            f"- **SMS (Quo)** — Inbound/outbound tenant texts route through Quo. "
            f"You reply automatically.\n"
            f"- **Web Chat** — Property managers chat with you via the RentMate web interface.\n\n"
            f"## Live Data\n\n"
            f"Use the available tools to fetch live property/tenant/lease data. Always prefer a live "
            f"query over any cached or remembered data — the database is the source of truth.\n\n"
            f"### Data Operations\n\n"
            f"| Operation | Description | Options |\n"
            f"|-----------|-------------|--------|\n"
            f"| `properties` | All properties with units, occupancy, and lease summary | |\n"
            f"| `tenants` | All tenants with active lease info (unit, property, rent, status) | |\n"
            f"| `leases` | All leases with tenant and property details | |\n"
            f"| `tasks` | Task list (maintenance, lease issues, etc.) | `--category` `--status` |\n"
            f"| `task` | Single task with full message thread | `--id <uid>` |\n"
            f"| `messages` | Messages for a conversation/SMS thread | `--id <conversation-id>` |\n\n"
            f"## Write Actions (require manager confirmation)\n\n"
            f"All write operations are **queued for human confirmation** — they do not execute\n"
            f"immediately. The manager's UI will show a confirmation card before any change is\n"
            f"committed to the database. Never call these in response to ambiguous requests.\n\n"
            f"### Write Operations\n\n"
            f"| Operation | Description |\n"
            f"|-----------|-------------|\n"
            f"| `propose_task` | Propose a new task — manager must approve before it is created |\n"
            f"| `close_task` | Request to close/resolve a task — manager must confirm |\n"
            f"| `set_mode` | Request a task mode change — manager must confirm |\n"
            f"| `update_steps` | Set or update progress steps for a task (immediate) |\n\n"
            f"### DO NOT\n\n"
            f"- **Do not install packages** (apt-get, pip, brew, etc.) to access data.\n"
            f"- **Do not connect to the database directly** — do not use sqlite3, sqlalchemy, "
            f"or any other library to open the database file.\n"
            f"- **Do not search the filesystem for the database.**\n"
            f"- **Do not write raw SQL.** Use the provided tools for all data operations.\n\n"
            f"## Vendor Notes\n\n"
            f"_(Add vendor contacts here as you learn them.)_\n"
        )
        tools_content = (agent_dir / "TOOLS.md").read_text()
        self._db_write_file(db, agent_id, "TOOLS.md", tools_content)

        db.commit()


agent_registry = AgentRegistry()
