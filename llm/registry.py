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


class _DbSessionManager:
    """SessionManager backed by RentMate's messages table.

    Nanobot's default SessionManager persists chat history to JSONL files,
    duplicating what RentMate already stores in the database.  This adapter
    reads/writes from the DB so there is a single source of truth.

    Session keys follow the pattern ``task:{task_id}`` or ``chat:{conv_id}``.
    """

    def __init__(self):
        from nanobot.session.manager import Session as NanobotSession
        self._NanobotSession = NanobotSession
        self._cache: dict[str, object] = {}

    # ── helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_conversation_id(key: str) -> str | None:
        """Map a nanobot session key to a RentMate conversation_id."""
        from handlers.deps import SessionLocal
        prefix, _, id_part = key.partition(":")
        if not id_part:
            return None
        if prefix == "task":
            db = SessionLocal()
            try:
                from db.models import Task
                task = db.query(Task).filter_by(id=id_part).first()
                return task.ai_conversation_id if task else None
            finally:
                db.close()
        # chat: / sms: keys already contain the conversation_id
        return id_part

    def _load_from_db(self, key: str):
        """Load messages from the DB into a nanobot Session."""
        conv_id = self._resolve_conversation_id(key)
        session = self._NanobotSession(key=key)
        if not conv_id:
            return session
        from handlers.deps import SessionLocal
        from db.models import Message, MessageType
        db = SessionLocal()
        try:
            rows = (
                db.query(Message)
                .filter(
                    Message.conversation_id == conv_id,
                    Message.message_type.in_([
                        MessageType.MESSAGE, MessageType.THREAD,  # include legacy THREAD
                    ]),
                )
                .order_by(Message.sent_at)
                .all()
            )
            for m in rows:
                role = "assistant" if m.is_ai else "user"
                session.messages.append({
                    "role": role,
                    "content": m.body or "",
                    "timestamp": m.sent_at.isoformat() if m.sent_at else "",
                })
        finally:
            db.close()
        return session

    # ── SessionManager interface ──────────────────────────────────────────

    def get_or_create(self, key: str):
        if key not in self._cache:
            self._cache[key] = self._load_from_db(key)
        return self._cache[key]

    def save(self, session) -> None:
        # Messages are persisted by the chat handler after the agent finishes,
        # so save is a no-op — we just keep the in-memory cache current.
        self._cache[session.key] = session

    def invalidate(self, key: str) -> None:
        self._cache.pop(key, None)

    def list_sessions(self) -> list:
        return []

# Paths
AGENTS_DIR = Path(__file__).parent.parent / "agents"
TEMPLATE_DIR = AGENTS_DIR / "template"
_data_base = Path(os.environ.get("RENTMATE_DATA_DIR", str(Path(__file__).parent.parent / "data")))
DATA_DIR = _data_base / "agent"

_STATIC_TEMPLATE_FILES = ["AGENTS.md", "SOUL.md", "IDENTITY.md", "HEARTBEAT.md"]

_VERSION_RE = re.compile(r"^#\s*soul_version:\s*(\d+)", re.MULTILINE)


def _soul_version(text: str) -> int:
    """Extract soul_version from file content, defaulting to 0 if absent."""
    m = _VERSION_RE.search(text)
    return int(m.group(1)) if m else 0


class AgentRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._loops: dict[str, object] = {}   # account_id → AgentLoop
        self._buses: dict[str, object] = {}   # account_id → MessageBus
        self._channel_task: asyncio.Task | None = None
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ─── Public lifecycle ─────────────────────────────────────────────────────

    def populate_all_agents(self, db: Session):
        """Write workspace files for the default account (others are lazy-initialized)."""
        agent_dir = DATA_DIR / DEFAULT_USER_ID
        self._write_workspace(agent_dir, db, DEFAULT_USER_ID)
        print("[nanobot] Workspace populated")

    def start_gateway(self, account_id: str | None = None):
        """Start (or ensure) the agent loop for an account. Defaults to DEFAULT_USER_ID."""
        aid = account_id or DEFAULT_USER_ID
        with self._lock:
            if aid in self._loops:
                return
            try:
                self._loops[aid] = self._make_loop(aid)
                print(f"[nanobot] Agent loop ready for account {aid[:8]}…")
            except Exception as e:
                print(f"[nanobot] Failed to start agent for {aid[:8]}…: {e}")

    def stop_gateway(self, account_id: str | None = None):
        aid = account_id or DEFAULT_USER_ID
        with self._lock:
            self._loops.pop(aid, None)
            self._buses.pop(aid, None)
        print(f"[nanobot] Agent stopped for account {aid[:8]}…")

    def is_healthy(self, account_id: str | None = None) -> bool:
        aid = account_id or DEFAULT_USER_ID
        return aid in self._loops

    def ensure_agent(self, account_id: str, db: Session) -> str:
        """Ensure an agent loop exists for the given account. Returns the account_id."""
        if account_id not in self._loops:
            # Write workspace files (lazy init for new accounts)
            agent_dir = DATA_DIR / account_id
            self._write_workspace(agent_dir, db, account_id)
            self.start_gateway(account_id)
        return account_id

    def get_loop(self, account_id: str | None = None):
        aid = account_id or DEFAULT_USER_ID
        return self._loops.get(aid)

    # ─── Internal ─────────────────────────────────────────────────────────────

    def _make_loop(self, account_id: str):
        from nanobot.agent import AgentLoop
        from nanobot.bus import MessageBus
        from nanobot.config.schema import ExecToolConfig, WebSearchConfig
        from nanobot.providers.openai_compat_provider import OpenAICompatProvider

        model = os.environ.get("LLM_MODEL", "anthropic/claude-haiku-4-5-20251001")
        api_key = os.environ.get("LLM_API_KEY", "")
        api_base = os.environ.get("LLM_BASE_URL") or None

        # Map LiteLLM-style provider/model names to direct API endpoints
        actual_model = model
        if "/" in model and not api_base:
            provider_prefix, _, model_name = model.partition("/")
            _PROVIDER_BASES = {
                "deepseek": "https://api.deepseek.com/v1",
                "anthropic": "https://api.anthropic.com/v1",
            }
            if provider_prefix in _PROVIDER_BASES:
                api_base = _PROVIDER_BASES[provider_prefix]
                actual_model = model_name

        provider = OpenAICompatProvider(
            api_key=api_key,
            api_base=api_base,
            default_model=actual_model,
        )

        workspace = DATA_DIR / account_id
        workspace.mkdir(parents=True, exist_ok=True)

        # Web search: use Brave if a key is configured, otherwise DuckDuckGo
        brave_key = os.environ.get("BRAVE_API_KEY") or None

        bus = MessageBus()
        self._buses[account_id] = bus
        loop = AgentLoop(
            bus=bus,
            provider=provider,
            workspace=workspace,
            model=actual_model,
            max_iterations=40,
            restrict_to_workspace=False,
            exec_config=ExecToolConfig(enable=False),
            session_manager=_DbSessionManager(),
            web_search_config=WebSearchConfig(
                provider="brave" if brave_key else "duckduckgo",
                api_key=brave_key or "",
            ),
        )

        # Remove tools the agent should not use — file I/O, shell, subagents
        for tool_name in ("read_file", "write_file", "edit_file", "list_dir", "spawn_agent", "send_message"):
            if loop.tools.has(tool_name):
                loop.tools.unregister(tool_name)

        # Register RentMate-specific tools (write actions → Suggestions)
        from llm.tools import ProposeTaskTool, CloseTaskTool, SetModeTool, AttachVendorTool, LookupVendorsTool, CreateVendorTool
        loop.tools.register(ProposeTaskTool())
        loop.tools.register(CloseTaskTool())
        loop.tools.register(SetModeTool())
        loop.tools.register(AttachVendorTool())
        loop.tools.register(LookupVendorsTool())
        loop.tools.register(CreateVendorTool())

        # Replace file-based memory with DB-backed memory
        from llm.memory_store import DbMemoryStore
        loop.memory_consolidator.store = DbMemoryStore(workspace, account_id)

        return loop

    @staticmethod
    def _build_nanobot_config(integrations: dict):
        from nanobot.config.schema import Config, ChannelsConfig

        tg = integrations.get("telegram", {})
        wa = integrations.get("whatsapp", {})
        channels = ChannelsConfig(
            telegram={
                "enabled": tg.get("enabled", False),
                "token": tg.get("token", ""),
                "allow_from": tg.get("allow_from", []),
            },
            whatsapp={
                "enabled": wa.get("enabled", False),
                "bridge_url": wa.get("bridge_url", "ws://localhost:3001"),
                "bridge_token": wa.get("bridge_token", ""),
                "allow_from": wa.get("allow_from", []),
            },
        )
        return Config(channels=channels)

    async def restart_channels_async(self, integrations: dict, account_id: str = DEFAULT_USER_ID):
        """(Re)start the nanobot ChannelManager from updated integration config."""
        bus = self._buses.get(account_id)
        if bus is None:
            return

        if self._channel_task and not self._channel_task.done():
            self._channel_task.cancel()
            try:
                await self._channel_task
            except (asyncio.CancelledError, Exception):
                pass
            self._channel_task = None

        config = self._build_nanobot_config(integrations)
        tg = integrations.get("telegram", {})
        wa = integrations.get("whatsapp", {})
        any_enabled = tg.get("enabled", False) or wa.get("enabled", False)
        if not any_enabled:
            print("[nanobot] No chat channels configured — skipping channel manager")
            return

        from nanobot.channels.manager import ChannelManager
        cm = ChannelManager(config, bus)
        self._channel_task = asyncio.create_task(cm.start_all())
        print(f"[nanobot] Chat channels starting: {cm.enabled_channels}")

    @staticmethod
    def _db_read_file(db: Session, agent_id: str, filename: str) -> str | None:
        """Read a bootstrap file from the agent_memory table."""
        from db.models import AgentMemory
        row = db.query(AgentMemory).filter_by(
            agent_id=agent_id, memory_type=f"file:{filename}"
        ).first()
        return row.content if row else None

    @staticmethod
    def _db_write_file(db: Session, agent_id: str, filename: str, content: str):
        """Persist a bootstrap file to the agent_memory table."""
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
            # 1. Try DB first (source of truth for existing agents)
            db_content = self._db_read_file(db, agent_id, filename)
            if db_content is not None:
                if filename == "SOUL.md":
                    # Check if template has a newer version
                    src = TEMPLATE_DIR / filename
                    if src.exists():
                        new_v = _soul_version(src.read_text())
                        old_v = _soul_version(db_content)
                        if new_v > old_v:
                            db_content = src.read_text()
                            self._db_write_file(db, agent_id, filename, db_content)
                            print(f"[nanobot] SOUL.md upgraded: v{old_v} → v{new_v}")
                dest.write_text(db_content)
                continue
            # 2. Fall back to template (first boot)
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
        action_script = Path(__file__).parent / "agent_action.py"
        workspace_abs = str((DATA_DIR / DEFAULT_USER_ID).resolve())

        (agent_dir / "TOOLS.md").write_text(
            f"# TOOLS.md - Communication Channels & Data Access\n\n"
            f"## Communication Channels\n\n"
            f"- **SMS (Dialpad)** — Inbound/outbound tenant texts route through Dialpad. "
            f"You reply automatically.\n"
            f"- **Web Chat** — Property managers chat with you via the RentMate web interface.\n\n"
            f"## Live Data\n\n"
            f"Use the shell tool to fetch live property/tenant/lease data. Always prefer a live "
            f"query over any cached or remembered data — the database is the source of truth.\n\n"
            f"```bash\n"
            f"{sys.executable} {data_script} <operation> [options]\n"
            f"```\n\n"
            f"### Data Operations\n\n"
            f"| Operation | Description | Options |\n"
            f"|-----------|-------------|--------|\n"
            f"| `properties` | All properties with units, occupancy, and lease summary | |\n"
            f"| `tenants` | All tenants with active lease info (unit, property, rent, status) | |\n"
            f"| `leases` | All leases with tenant and property details | |\n"
            f"| `tasks` | Task list (maintenance, lease issues, etc.) | `--category` `--status` |\n"
            f"| `task` | Single task with full message thread | `--id <uid>` |\n"
            f"| `messages` | Messages for a conversation/SMS thread | `--id <conversation-id>` |\n\n"
            f"### Data Examples\n\n"
            f"```bash\n"
            f"{sys.executable} {data_script} properties\n"
            f"{sys.executable} {data_script} tenants\n"
            f"{sys.executable} {data_script} tasks --category maintenance --status open\n"
            f"{sys.executable} {data_script} task --id <task-uid>\n"
            f"{sys.executable} {data_script} messages --id <conversation-id>\n"
            f"```\n\n"
            f"## Write Actions (require manager confirmation)\n\n"
            f"All write operations are **queued for human confirmation** — they do not execute\n"
            f"immediately. The manager's UI will show a confirmation card before any change is\n"
            f"committed to the database. Never call these in response to ambiguous requests.\n\n"
            f"```bash\n"
            f"RENTMATE_AGENT_WORKSPACE={workspace_abs} {sys.executable} {action_script} <operation> [options]\n"
            f"```\n\n"
            f"### Write Operations\n\n"
            f"| Operation | Description | Options |\n"
            f"|-----------|-------------|--------|\n"
            f"| `propose_task` | Propose a new task — manager must approve before it is created | `--title` (required) `--category` (required) `--task-id` (required) `--urgency` `--description` |\n"
            f"| `close_task` | Request to close/resolve a task — manager must confirm | `--id <task-id>` (required) |\n"
            f"| `set_mode` | Request a task mode change — manager must confirm | `--id <task-id>` (required) `--mode autonomous\\|manual\\|waiting_approval` (required) |\n\n"
            f"### Write Examples\n\n"
            f"```bash\n"
            f"# Propose a follow-up maintenance task\n"
            f"RENTMATE_AGENT_WORKSPACE={workspace_abs} {sys.executable} {action_script} propose_task \\\n"
            f"  --title \"Schedule annual HVAC inspection\" \\\n"
            f"  --category maintenance --urgency medium --task-id <task-id-from-context>\n\n"
            f"# Request to close the current task (manager must confirm)\n"
            f"RENTMATE_AGENT_WORKSPACE={workspace_abs} {sys.executable} {action_script} close_task --id <task-id>\n\n"
            f"# Request a mode switch (manager must confirm)\n"
            f"RENTMATE_AGENT_WORKSPACE={workspace_abs} {sys.executable} {action_script} set_mode --id <task-id> --mode autonomous\n"
            f"```\n\n"
            f"### DO NOT\n\n"
            f"- **Do not install packages** (apt-get, pip, brew, etc.) to access data.\n"
            f"- **Do not connect to the database directly** — do not use sqlite3, sqlalchemy, "
            f"or any other library to open the database file.\n"
            f"- **Do not search the filesystem for the database.**\n"
            f"- **Do not write raw SQL.** The operations above are the only supported way to "
            f"read or act on RentMate data.\n\n"
            f"## Vendor Notes\n\n"
            f"_(Add vendor contacts here as you learn them.)_\n"
        )
        # Persist TOOLS.md to DB (regenerated each startup with current paths)
        tools_content = (agent_dir / "TOOLS.md").read_text()
        self._db_write_file(db, agent_id, "TOOLS.md", tools_content)

        db.commit()


agent_registry = AgentRegistry()
