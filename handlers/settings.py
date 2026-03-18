import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from handlers.deps import require_user

router = APIRouter()

_ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
_DATA_DIR = Path(os.environ.get("RENTMATE_DATA_DIR", str(Path(__file__).parent.parent / "data")))
_SETTINGS_FILE = _DATA_DIR / "settings.json"

_DEFAULT_AUTONOMY = {
    "rent": "suggest",
    "maintenance": "suggest",
    "leasing": "suggest",
    "compliance": "suggest",
}


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


def load_app_settings() -> dict:
    if _SETTINGS_FILE.exists():
        try:
            return json.loads(_SETTINGS_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_app_settings(data: dict):
    _SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SETTINGS_FILE.write_text(json.dumps(data, indent=2))


def get_autonomy_settings() -> dict:
    return load_app_settings().get("autonomy", _DEFAULT_AUTONOMY)


_INTEGRATIONS_FILE = _DATA_DIR / "integrations.json"

_SECRET_FIELDS = {"token", "bridge_token"}


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


def _mask_integrations(stored: dict) -> dict:
    """Return integration config with secrets replaced by empty string."""
    result = {}
    for ch in ("telegram", "whatsapp"):
        ch_cfg = dict(stored.get(ch, {}))
        for f in _SECRET_FIELDS:
            if f in ch_cfg:
                ch_cfg[f] = ""
        result[ch] = ch_cfg
    return result


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
        "api_key": "",  # never echo the key back
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


@router.post("/settings/integrations")
async def update_integrations(body: IntegrationsBody, request: Request):
    await require_user(request)
    stored = load_integrations()

    channel_map = [
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
