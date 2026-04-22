from __future__ import annotations

import json
import os
from time import sleep
from typing import Any

import litellm

from llm.model_config import build_litellm_request_kwargs, resolve_model_config


def _default_timeout_seconds() -> float:
    raw = os.getenv("LITELLM_REQUEST_TIMEOUT_SECONDS") or os.getenv("EVAL_LLM_TIMEOUT_SECONDS") or "45"
    try:
        return float(raw)
    except ValueError:
        return 45.0


def completion_with_retries(
    *,
    messages: list[dict[str, Any]],
    model: str | None = None,
    api_base: str | None = None,
    temperature: float = 0.0,
    retries: int = 2,
    timeout: float | None = None,
    **extra_kwargs: Any,
) -> tuple[Any, str, str | None]:
    chosen_model = model or os.getenv("LLM_MODEL", "openai/gpt-4o-mini")
    chosen_base = api_base if api_base is not None else (os.getenv("LLM_BASE_URL") or None)
    resolved = resolve_model_config(model=chosen_model, api_base=chosen_base)
    request_kwargs = build_litellm_request_kwargs(
        model=chosen_model,
        api_base=chosen_base,
        api_key=os.getenv("LLM_API_KEY"),
        app_name=os.getenv("OPENROUTER_APP_TITLE") or "RentMate",
        referer=os.getenv("OPENROUTER_HTTP_REFERER") or os.getenv("PUBLIC_APP_URL") or os.getenv("APP_BASE_URL"),
    )
    request_kwargs.update({
        "messages": messages,
        "temperature": temperature,
        "timeout": timeout if timeout is not None else _default_timeout_seconds(),
    })
    request_kwargs.update(extra_kwargs)

    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            response = litellm.completion(**request_kwargs)
            return response, resolved.litellm_model, resolved.api_base
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt == retries - 1:
                break
            sleep(1.0 * (attempt + 1))

    raise RuntimeError(
        "LLM call failed after retries "
        f"(model={resolved.litellm_model}, base_url={resolved.api_base}): "
        f"{type(last_exc).__name__}: {last_exc}"
    ) from last_exc


def completion_json_with_retries(
    *,
    messages: list[dict[str, Any]],
    model: str | None = None,
    api_base: str | None = None,
    temperature: float = 0.0,
    retries: int = 2,
    timeout: float | None = None,
    **extra_kwargs: Any,
) -> tuple[dict[str, Any], str, str | None]:
    response, litellm_model, resolved_base = completion_with_retries(
        messages=messages,
        model=model,
        api_base=api_base,
        temperature=temperature,
        retries=retries,
        timeout=timeout,
        response_format={"type": "json_object"},
        **extra_kwargs,
    )
    text = (response.choices[0].message.content or "").strip() or "{}"
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text), litellm_model, resolved_base
