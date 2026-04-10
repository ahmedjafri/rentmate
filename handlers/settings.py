import os
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from gql.services.settings_service import (  # noqa: F401 — re-exported
    _DEFAULT_AUTONOMY,
    get_agent_integrations,
    get_autonomy_settings,
    get_integrations,
    get_llm_settings,
    load_app_settings,
    save_agent_integrations,
    save_app_settings,
    save_integrations,
    save_llm_settings,
)
from handlers.deps import require_user

router = APIRouter()


_SECRET_FIELDS = {"token", "bridge_token", "api_key"}


def load_integrations() -> dict:
    """Read integrations from DB. Re-exported from settings_service."""
    return get_integrations()


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
    llm = get_llm_settings()
    return {
        "api_key": _SECRET_MASK if llm.get("api_key") else "",
        "model": llm.get("model", "openai/gpt-4o-mini"),
        "base_url": llm.get("base_url", ""),
        "autonomy": stored.get("autonomy", _DEFAULT_AUTONOMY),
    }


@router.post("/settings")
async def update_settings(body: SettingsBody, request: Request):
    await require_user(request)

    # LLM config — persisted to DB, cached in os.environ
    has_llm_update = False
    if body.api_key:
        os.environ["LLM_API_KEY"] = body.api_key
        has_llm_update = True
    if body.model is not None:
        os.environ["LLM_MODEL"] = body.model
        has_llm_update = True
    if body.base_url is not None:
        os.environ["LLM_BASE_URL"] = body.base_url
        has_llm_update = True
    if has_llm_update:
        save_llm_settings(
            api_key=body.api_key or None,
            model=body.model,
            base_url=body.base_url,
        )
        from llm import llm as llm_module
        llm_module.reconfigure()
        # Mark onboarding step done if active
        from gql.services.settings_service import get_onboarding_state, is_llm_configured, update_onboarding_step
        from handlers.deps import SessionLocal as _SL
        if body.api_key and is_llm_configured():
            _db = _SL()
            try:
                _state = get_onboarding_state(_db)
                if _state and _state.get("status") == "active":
                    update_onboarding_step(_db, step="configure_llm")
                    _db.commit()
            finally:
                _db.close()

    # Autonomy settings — persisted to DB
    if body.autonomy is not None:
        stored = load_app_settings()
        stored["autonomy"] = body.autonomy
        save_app_settings(stored)

    return {"ok": True}


@router.post("/settings/llm/test")
async def test_llm(request: Request):
    """Send a tiny completion request to verify the LLM config works.

    Uses the same provider-mapping logic as the real agent path so the test
    accurately reflects whether chat will succeed.
    """
    await require_user(request)
    import time

    from openai import OpenAI

    model = os.getenv("LLM_MODEL", "openai/gpt-4o-mini")
    api_key = os.getenv("LLM_API_KEY", "")
    base_url = os.getenv("LLM_BASE_URL") or None

    if not api_key:
        return {"ok": False, "error": "No API key configured."}

    # Mirror the provider mapping from llm/client.py so the test hits the
    # same endpoint the agent will use.
    actual_model = model
    if "/" in model and not base_url:
        provider_prefix, _, model_name = model.partition("/")
        _PROVIDER_BASES = {
            "deepseek": "https://api.deepseek.com",
            "anthropic": "https://api.anthropic.com/v1",
            "openai": "https://api.openai.com/v1",
        }
        if provider_prefix in _PROVIDER_BASES:
            base_url = _PROVIDER_BASES[provider_prefix]
            actual_model = model_name

    # Default base_url for the OpenAI client
    if not base_url:
        base_url = "https://api.openai.com/v1"

    t0 = time.time()
    try:
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=15)
        resp = client.chat.completions.create(
            model=actual_model,
            messages=[{"role": "user", "content": "Reply with exactly: OK"}],
            max_tokens=5,
        )
        elapsed = round(time.time() - t0, 2)
        reply = (resp.choices[0].message.content or "").strip()
        return {
            "ok": True,
            "model": f"{actual_model} via {base_url}",
            "reply": reply,
            "elapsed": elapsed,
        }
    except Exception as e:
        elapsed = round(time.time() - t0, 2)
        return {
            "ok": False,
            "model": f"{actual_model} via {base_url}",
            "error": str(e)[:300],
            "elapsed": elapsed,
        }


@router.get("/settings/integrations")
async def get_integrations_endpoint(request: Request):
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
    save_integrations(stored)
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

    save_integrations(stored)

    from llm.registry import agent_registry
    await agent_registry.restart_channels_async(stored)

    return {"ok": True}


# ─── Agent integrations (tools & capabilities) ──────────────────────────────

class AgentIntegrationsBody(BaseModel):
    brave_api_key: Optional[str] = None
    web_search_enabled: bool = False


@router.get("/settings/agent/integrations")
async def get_agent_integrations_endpoint(request: Request):
    await require_user(request)
    agent_int = get_agent_integrations()
    return {
        "brave_api_key": "",  # never echo the key back
        "web_search_enabled": agent_int.get("web_search_enabled", False),
    }


@router.post("/settings/agent/integrations")
async def update_agent_integrations(body: AgentIntegrationsBody, request: Request):
    await require_user(request)

    if body.brave_api_key:
        os.environ["BRAVE_API_KEY"] = body.brave_api_key

    save_agent_integrations(
        brave_api_key=body.brave_api_key or None,
        web_search_enabled=body.web_search_enabled,
    )

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
    from backends.local_auth import _lookup_account_id
    from llm.registry import DATA_DIR
    return DATA_DIR / str(_lookup_account_id())


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
