import os
from typing import Any
from typing import NamedTuple


class ResolvedModelConfig(NamedTuple):
    model: str
    litellm_model: str
    api_base: str | None
    provider: str | None


_PROVIDER_BASES: dict[str, tuple[str, str | None]] = {
    "deepseek": ("https://api.deepseek.com", None),
    "anthropic": ("https://api.anthropic.com/v1", "anthropic"),
    "openai": ("https://api.openai.com/v1", None),
}

_OPENROUTER_BASE = "https://openrouter.ai/api/v1"


def _is_openrouter_base(api_base: str | None) -> bool:
    return (api_base or "").rstrip("/") == _OPENROUTER_BASE


def resolve_model_config(
    *,
    model: str,
    api_base: str | None = None,
    default_base: str | None = "https://api.openai.com/v1",
) -> ResolvedModelConfig:
    actual_model = model
    litellm_model = model
    provider = None
    resolved_base = api_base or None

    if model.startswith("openrouter/"):
        actual_model = model.partition("/")[2]
        litellm_model = model
        provider = "openrouter"
        if not resolved_base:
            resolved_base = _OPENROUTER_BASE
    elif _is_openrouter_base(resolved_base):
        actual_model = model
        litellm_model = model if model.startswith("openrouter/") else f"openrouter/{model}"
        provider = "openrouter"

    if "/" in model:
        provider_prefix, _, model_name = model.partition("/")
        if provider is None and provider_prefix in _PROVIDER_BASES:
            inferred_base, provider = _PROVIDER_BASES[provider_prefix]
            actual_model = model_name
            litellm_model = model
            if not resolved_base:
                resolved_base = inferred_base

    if not resolved_base:
        resolved_base = default_base

    return ResolvedModelConfig(
        model=actual_model,
        litellm_model=litellm_model,
        api_base=resolved_base,
        provider=provider,
    )


def resolve_env_model_config(
    *,
    model_env: str = "LLM_MODEL",
    base_env: str = "LLM_BASE_URL",
    default_model: str = "openai/gpt-4o-mini",
    default_base: str | None = "https://api.openai.com/v1",
) -> ResolvedModelConfig:
    model = os.getenv(model_env, default_model)
    api_base = os.getenv(base_env) or None
    return resolve_model_config(model=model, api_base=api_base, default_base=default_base)


def build_litellm_request_kwargs(
    *,
    model: str,
    api_base: str | None = None,
    api_key: str | None = None,
    app_name: str | None = None,
    referer: str | None = None,
    default_base: str | None = "https://api.openai.com/v1",
) -> dict[str, Any]:
    resolved = resolve_model_config(model=model, api_base=api_base, default_base=default_base)
    kwargs: dict[str, Any] = {
        "model": resolved.litellm_model,
        "api_key": api_key,
        "base_url": resolved.api_base,
    }
    if resolved.provider == "openrouter":
        headers: dict[str, str] = {}
        if referer:
            headers["HTTP-Referer"] = referer
        if app_name:
            headers["X-Title"] = app_name
        if headers:
            kwargs["extra_headers"] = headers
    return kwargs
