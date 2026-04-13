import os
from typing import NamedTuple


class ResolvedModelConfig(NamedTuple):
    model: str
    api_base: str | None
    provider: str | None


_PROVIDER_BASES: dict[str, tuple[str, str | None]] = {
    "deepseek": ("https://api.deepseek.com", None),
    "anthropic": ("https://api.anthropic.com/v1", "anthropic"),
    "openai": ("https://api.openai.com/v1", None),
}


def resolve_model_config(
    *,
    model: str,
    api_base: str | None = None,
    default_base: str | None = "https://api.openai.com/v1",
) -> ResolvedModelConfig:
    actual_model = model
    provider = None
    resolved_base = api_base or None

    if "/" in model:
        provider_prefix, _, model_name = model.partition("/")
        if provider_prefix in _PROVIDER_BASES:
            inferred_base, provider = _PROVIDER_BASES[provider_prefix]
            actual_model = model_name
            if not resolved_base:
                resolved_base = inferred_base

    if not resolved_base:
        resolved_base = default_base

    return ResolvedModelConfig(model=actual_model, api_base=resolved_base, provider=provider)


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
