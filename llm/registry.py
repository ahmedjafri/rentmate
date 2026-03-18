import asyncio
import os
import re
import shutil
import sys
import threading
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
    """Extract soul_version from file content, defaulting to 0 if absent."""
    m = _VERSION_RE.search(text)
    return int(m.group(1)) if m else 0


class AgentRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._loop = None
        self._bus = None
        self._channel_task: asyncio.Task | None = None
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ─── Public lifecycle ─────────────────────────────────────────────────────

    def populate_all_agents(self, db: Session):
        agent_dir = DATA_DIR / DEFAULT_USER_ID
        self._write_workspace(agent_dir, db)
        print("[nanobot] Workspace populated")

    def start_gateway(self):
        with self._lock:
            if self._loop is not None:
                return
            try:
                self._loop = self._make_loop()
                print("[nanobot] Agent loop ready")
            except Exception as e:
                print(f"[nanobot] Failed to start agent: {e}")

    def stop_gateway(self):
        with self._lock:
            self._loop = None
        print("[nanobot] Agent stopped")

    def is_healthy(self) -> bool:
        return self._loop is not None

    def ensure_agent(self, user_id: str, db: Session) -> str:
        if self._loop is None:
            self.start_gateway()
        return DEFAULT_USER_ID

    def get_loop(self):
        return self._loop

    # ─── Internal ─────────────────────────────────────────────────────────────

    def _make_loop(self):
        from nanobot.agent import AgentLoop
        from nanobot.bus import MessageBus
        from nanobot.providers import LiteLLMProvider

        model = os.environ.get("LLM_MODEL", "anthropic/claude-haiku-4-5-20251001")
        api_key = os.environ.get("LLM_API_KEY", "")
        api_base = os.environ.get("LLM_BASE_URL") or None

        provider = LiteLLMProvider(
            api_key=api_key,
            api_base=api_base,
            default_model=model,
        )

        workspace = DATA_DIR / DEFAULT_USER_ID
        workspace.mkdir(parents=True, exist_ok=True)

        self._bus = MessageBus()
        return AgentLoop(
            bus=self._bus,
            provider=provider,
            workspace=workspace,
            model=model,
            max_iterations=40,
            restrict_to_workspace=False,
        )

    @staticmethod
    def _build_nanobot_config(integrations: dict):
        from nanobot.config.schema import (
            Config, ChannelsConfig, TelegramConfig, WhatsAppConfig,
        )
        tg = integrations.get("telegram", {})
        wa = integrations.get("whatsapp", {})
        channels = ChannelsConfig(
            telegram=TelegramConfig(
                enabled=tg.get("enabled", False),
                token=tg.get("token", ""),
                allow_from=tg.get("allow_from", []),
            ),
            whatsapp=WhatsAppConfig(
                enabled=wa.get("enabled", False),
                bridge_url=wa.get("bridge_url", "ws://localhost:3001"),
                bridge_token=wa.get("bridge_token", ""),
                allow_from=wa.get("allow_from", []),
            ),
        )
        return Config(channels=channels)

    async def restart_channels_async(self, integrations: dict):
        """(Re)start the nanobot ChannelManager from updated integration config."""
        if self._bus is None:
            return

        if self._channel_task and not self._channel_task.done():
            self._channel_task.cancel()
            try:
                await self._channel_task
            except (asyncio.CancelledError, Exception):
                pass
            self._channel_task = None

        config = self._build_nanobot_config(integrations)
        any_enabled = (
            config.channels.telegram.enabled
            or config.channels.whatsapp.enabled
        )
        if not any_enabled:
            print("[nanobot] No chat channels configured — skipping channel manager")
            return

        from nanobot.channels.manager import ChannelManager
        cm = ChannelManager(config, self._bus)
        self._channel_task = asyncio.create_task(cm.start_all())
        print(f"[nanobot] Chat channels starting: {cm.enabled_channels}")

    def _write_workspace(self, agent_dir: Path, db: Session):
        agent_dir.mkdir(parents=True, exist_ok=True)

        for filename in _STATIC_TEMPLATE_FILES:
            src = TEMPLATE_DIR / filename
            if not src.exists():
                continue
            dest = agent_dir / filename
            if filename == "SOUL.md" and dest.exists():
                old_v = _soul_version(dest.read_text())
                new_v = _soul_version(src.read_text())
                if new_v == old_v:
                    continue  # no change, skip overwrite
                direction = "upgraded" if new_v > old_v else "reverted"
                print(f"[nanobot] SOUL.md {direction}: v{old_v} → v{new_v}")
            shutil.copy2(src, dest)

        admin_email = os.environ.get("RENTMATE_ADMIN_EMAIL", "admin@localhost")
        account_name = os.environ.get("RENTMATE_ACCOUNT_NAME", "RentMate")

        (agent_dir / "USER.md").write_text(
            f"# USER.md - About Your Manager\n\n"
            f"- **Name:** {admin_email}\n"
            f"- **Pronouns:** Unknown — use neutral language (they/them) until told otherwise\n"
            f"- **Account:** {account_name}\n"
            f"- **Role:** admin\n\n"
            f"_(Update this as you learn more about how they prefer to work.)_\n"
        )

        data_script = Path(__file__).parent / "agent_data.py"
        action_script = Path(__file__).parent / "agent_action.py"
        workspace_abs = str((DATA_DIR / DEFAULT_USER_ID).resolve())

        (agent_dir / "TOOLS.md").write_text(
            f"# TOOLS.md - Communication Channels & Data Access\n\n"
            f"## Communication Channels\n\n"
            f"- **SMS (Dialpad)** — Inbound/outbound tenant texts route through Dialpad. "
            f"You reply automatically.\n"
            f"- **Web Chat** — Property managers chat with you via the RentMate web interface.\n"
            f"- **Chrome Extension** — Suggests replies on TenantCloud.\n\n"
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
            f"## Actions\n\n"
            f"Use the shell tool to take actions on tasks. These execute immediately and notify "
            f"the property manager's UI.\n\n"
            f"```bash\n"
            f"RENTMATE_AGENT_WORKSPACE={workspace_abs} {sys.executable} {action_script} <operation> [options]\n"
            f"```\n\n"
            f"### Action Operations\n\n"
            f"| Operation | Description | Options |\n"
            f"|-----------|-------------|--------|\n"
            f"| `propose_task` | Propose a new task for the manager to review and confirm | `--title` (required) `--category` (required) `--task-id` (required) `--urgency` `--description` |\n"
            f"| `close_task` | Close/resolve the current task | `--id <task-id>` (required) |\n"
            f"| `set_mode` | Change task mode | `--id <task-id>` (required) `--mode autonomous\\|manual\\|waiting_approval` (required) |\n\n"
            f"### Action Examples\n\n"
            f"```bash\n"
            f"# Propose a follow-up maintenance task\n"
            f"RENTMATE_AGENT_WORKSPACE={workspace_abs} {sys.executable} {action_script} propose_task \\\n"
            f"  --title \"Schedule annual HVAC inspection\" \\\n"
            f"  --category maintenance --urgency medium --task-id <task-id-from-context>\n\n"
            f"# Close the current task (use the task_id from context)\n"
            f"RENTMATE_AGENT_WORKSPACE={workspace_abs} {sys.executable} {action_script} close_task --id <task-id>\n\n"
            f"# Switch back to autonomous mode\n"
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


agent_registry = AgentRegistry()
