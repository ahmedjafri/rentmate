import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from gql.services.settings_service import get_autonomy_settings, load_app_settings  # noqa: F401 — re-exported
from handlers.deps import require_user

router = APIRouter()

_ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
_DATA_DIR = Path(os.environ.get("RENTMATE_DATA_DIR", str(Path(__file__).parent.parent / "data")))
_SETTINGS_FILE = _DATA_DIR / "settings.json"


def read_env_file() -> dict:
    result = {}
    if not os.path.exists(_ENV_FILE):
        return result
    with open(_ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                result[k.strip()] = v.strip().strip('"').strip("'")
    return result


def write_env_file(updates: dict):
    lines = []
    handled = set()
    if os.path.exists(_ENV_FILE):
        with open(_ENV_FILE) as f:
            for line in f:
                stripped = line.rstrip("\n")
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    k = stripped.split("=", 1)[0].strip()
                    if k in updates:
                        lines.append(f"{k}={updates[k]}")
                        handled.add(k)
                    else:
                        lines.append(stripped)
                else:
                    lines.append(stripped)
    for k, v in updates.items():
        if k not in handled:
            lines.append(f"{k}={v}")
    with open(_ENV_FILE, "w") as f:
        f.write("\n".join(lines) + "\n")


def _save_app_settings(data: dict):
    _SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SETTINGS_FILE.write_text(json.dumps(data, indent=2))


_INTEGRATIONS_FILE = _DATA_DIR / "integrations.json"

_SECRET_FIELDS = {"token", "bridge_token", "api_key"}


def load_integrations() -> dict:
    if _INTEGRATIONS_FILE.exists():
        try:
            return json.loads(_INTEGRATIONS_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_integrations(data: dict):
    _INTEGRATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _INTEGRATIONS_FILE.write_text(json.dumps(data, indent=2))


_SECRET_MASK = "\u2022" * 8  # ••••••••

def _mask_integrations(stored: dict) -> dict:
    """Return integration config with secrets masked (non-empty if set)."""
    result = {}
    for ch in ("quo", "telegram", "whatsapp"):
        ch_cfg = dict(stored.get(ch, {}))
        for f in _SECRET_FIELDS:
            if f in ch_cfg and ch_cfg[f]:
                ch_cfg[f] = _SECRET_MASK
            elif f in ch_cfg:
                ch_cfg[f] = ""
        result[ch] = ch_cfg
    return result


class QuoIntegration(BaseModel):
    enabled: bool = False
    api_key: Optional[str] = None
    from_number: Optional[str] = None
    phone_whitelist: Optional[List[str]] = None


class TelegramIntegration(BaseModel):
    enabled: bool = False
    token: Optional[str] = None
    allow_from: Optional[List[str]] = None


class WhatsAppIntegration(BaseModel):
    enabled: bool = False
    bridge_url: Optional[str] = None
    bridge_token: Optional[str] = None
    allow_from: Optional[List[str]] = None


class IntegrationsBody(BaseModel):
    quo: Optional[QuoIntegration] = None
    telegram: Optional[TelegramIntegration] = None
    whatsapp: Optional[WhatsAppIntegration] = None


class SettingsBody(BaseModel):
    api_key: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    autonomy: Optional[Dict[str, str]] = None


@router.get("/settings")
async def get_settings(request: Request):
    await require_user(request)
    stored = load_app_settings()
    return {
        "api_key": _SECRET_MASK if os.getenv("LLM_API_KEY") else "",
        "model": os.getenv("LLM_MODEL", "openai/gpt-4o-mini"),
        "base_url": os.getenv("LLM_BASE_URL", ""),
        "autonomy": stored.get("autonomy", _DEFAULT_AUTONOMY),
    }


@router.post("/settings")
async def update_settings(body: SettingsBody, request: Request):
    await require_user(request)

    # LLM config — persisted to .env
    env_updates = {}
    if body.api_key:
        os.environ["LLM_API_KEY"] = body.api_key
        env_updates["LLM_API_KEY"] = body.api_key
    if body.model is not None:
        os.environ["LLM_MODEL"] = body.model
        env_updates["LLM_MODEL"] = body.model
    if body.base_url is not None:
        os.environ["LLM_BASE_URL"] = body.base_url
        env_updates["LLM_BASE_URL"] = body.base_url
    if env_updates:
        write_env_file(env_updates)
        from llm import llm as llm_module
        llm_module.reconfigure()

    # Autonomy settings — persisted to data/settings.json
    if body.autonomy is not None:
        stored = load_app_settings()
        stored["autonomy"] = body.autonomy
        _save_app_settings(stored)

    return {"ok": True}


@router.get("/settings/integrations")
async def get_integrations(request: Request):
    await require_user(request)
    stored = load_integrations()
    return _mask_integrations(stored)


@router.post("/settings/integrations/quo/test")
async def test_quo(request: Request):
    """Test a Quo API key by listing phone numbers."""
    await require_user(request)
    import httpx
    body = await request.json()
    api_key = body.get("api_key") or ""
    if not api_key:
        stored = load_integrations().get("quo", {})
        api_key = stored.get("api_key", "")
    if not api_key:
        return {"ok": False, "error": "No API key provided"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://api.openphone.com/v1/phone-numbers",
                headers={"Authorization": api_key},
            )
        if r.status_code == 200:
            data = r.json()
            numbers = data.get("data", [])
            name = numbers[0].get("name", "Quo") if numbers else "Quo"
            return {"ok": True, "company": name, "status": f"{len(numbers)} number(s)"}
        return {"ok": False, "error": f"Quo returned {r.status_code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _can_register_webhook() -> bool:
    """Return True only when explicitly configured for production."""
    return bool(os.environ.get("RENTMATE_PUBLIC_URL"))


@router.get("/settings/integrations/quo/webhook")
async def get_quo_webhook_status(request: Request):
    """Return the current webhook status and whether registration is allowed."""
    await require_user(request)
    stored = load_integrations()
    dp = stored.get("quo", {})
    can_register = _can_register_webhook()
    return {
        "webhook_url": dp.get("webhook_url"),
        "can_register": can_register,
        "reason": "Webhook registration requires a public URL. Set RENTMATE_PUBLIC_URL or deploy to a public server." if not can_register else None,
    }


@router.post("/settings/integrations/quo/webhook")
async def register_quo_webhook(request: Request):
    """Save the webhook URL for Quo. Quo webhooks are configured in workspace settings."""
    await require_user(request)
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}

    webhook_url = body.get("webhook_url", "").strip()
    if not webhook_url:
        public_url = os.environ.get("RENTMATE_PUBLIC_URL", "").rstrip("/")
        if not public_url:
            scheme = request.headers.get("x-forwarded-proto", "https")
            host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
            public_url = f"{scheme}://{host}"
        webhook_url = f"{public_url}/quo-webhook"

    if not _can_register_webhook():
        return {"ok": False, "error": "Webhook registration requires a public URL. Set RENTMATE_PUBLIC_URL or deploy to a public server."}

    stored = load_integrations()
    quo = stored.get("quo", {})
    quo["webhook_url"] = webhook_url
    stored["quo"] = quo
    _save_integrations(stored)
    return {
        "ok": True,
        "webhook_url": webhook_url,
        "message": "Webhook URL saved. Configure this URL in your Quo workspace settings under Integrations > Webhooks, subscribing to message.received events.",
    }


@router.post("/settings/integrations")
async def update_integrations(body: IntegrationsBody, request: Request):
    await require_user(request)
    stored = load_integrations()

    channel_map = [
        ("quo", body.quo, ["api_key", "phone_whitelist", "enabled"]),
        ("telegram", body.telegram, ["token", "allow_from", "enabled"]),
        ("whatsapp", body.whatsapp, ["bridge_url", "bridge_token", "allow_from", "enabled"]),
    ]
    for ch_name, ch_body, _ in channel_map:
        if ch_body is None:
            continue
        ch_data = stored.get(ch_name, {})
        for field, value in ch_body.model_dump(exclude_none=True).items():
            if field in _SECRET_FIELDS:
                if value:  # only overwrite if non-empty
                    ch_data[field] = value
            else:
                ch_data[field] = value
        stored[ch_name] = ch_data

    _save_integrations(stored)

    from llm.registry import agent_registry
    await agent_registry.restart_channels_async(stored)

    return {"ok": True}


# ─── Agent integrations (tools & capabilities) ──────────────────────────────

class AgentIntegrationsBody(BaseModel):
    brave_api_key: Optional[str] = None
    web_search_enabled: bool = False


@router.get("/settings/agent/integrations")
async def get_agent_integrations(request: Request):
    await require_user(request)
    stored = load_app_settings()
    agent_int = stored.get("agent_integrations", {})
    return {
        "brave_api_key": "",  # never echo the key back
        "web_search_enabled": agent_int.get("web_search_enabled", False),
    }


@router.post("/settings/agent/integrations")
async def update_agent_integrations(body: AgentIntegrationsBody, request: Request):
    await require_user(request)

    env_updates = {}
    if body.brave_api_key:
        os.environ["BRAVE_API_KEY"] = body.brave_api_key
        env_updates["BRAVE_API_KEY"] = body.brave_api_key

    stored = load_app_settings()
    agent_int = stored.get("agent_integrations", {})
    agent_int["web_search_enabled"] = body.web_search_enabled
    stored["agent_integrations"] = agent_int
    _save_app_settings(stored)

    if env_updates:
        write_env_file(env_updates)

    # Restart the agent loop so it picks up the new config
    from llm.registry import agent_registry
    agent_registry.stop_gateway()
    agent_registry.start_gateway()

    return {"ok": True}


# ─── Agent workspace files ────────────────────────────────────────────────────

_AGENT_FILES = [
    {"filename": "SOUL.md",           "readonly": True},
    {"filename": "AGENTS.md",         "readonly": False},
    {"filename": "IDENTITY.md",       "readonly": True},
    {"filename": "HEARTBEAT.md",      "readonly": False},
    {"filename": "memory/MEMORY.md",  "readonly": False},
    {"filename": "USER.md",           "readonly": False},
    {"filename": "TOOLS.md",          "readonly": True},
]

_AGENT_FILENAMES = {f["filename"] for f in _AGENT_FILES}


def _agent_workspace() -> Path:
    from llm.registry import DATA_DIR, DEFAULT_USER_ID
    return DATA_DIR / DEFAULT_USER_ID


@router.get("/settings/agent/files")
async def get_agent_files(request: Request):
    await require_user(request)
    workspace = _agent_workspace()
    result = []
    for entry in _AGENT_FILES:
        path = workspace / entry["filename"]
        content = path.read_text() if path.exists() else ""
        result.append({"filename": entry["filename"], "content": content, "readonly": entry["readonly"]})
    return result


class AgentFileBody(BaseModel):
    content: str


@router.put("/settings/agent/files/{filename:path}")
async def update_agent_file(filename: str, body: AgentFileBody, request: Request):
    await require_user(request)
    if filename not in _AGENT_FILENAMES:
        raise HTTPException(status_code=404, detail="Unknown file")
    readonly = next(f["readonly"] for f in _AGENT_FILES if f["filename"] == filename)
    if readonly:
        raise HTTPException(status_code=403, detail="File is read-only")
    path = _agent_workspace() / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body.content)
    return {"ok": True}
