from __future__ import annotations

from typing import Any

from agent.litellm_utils import LLMLane, completion_json_with_retries


def completion_json(
    *,
    messages: list[dict[str, str]],
    model: str | None = None,
    api_base: str | None = None,
    temperature: float = 0.0,
    retries: int = 1,
    timeout: float | None = None,
) -> tuple[dict[str, Any], str, str | None]:
    try:
        return completion_json_with_retries(
            messages=messages,
            model=model,
            api_base=api_base,
            temperature=temperature,
            retries=retries,
            timeout=timeout,
            lane=LLMLane.CHEAP,
        )
    except Exception as exc:
        raise RuntimeError(f"Eval LLM call failed: {exc}") from exc
