from __future__ import annotations

import os
from typing import Any

from agent.litellm_utils import completion_json_with_retries


def completion_json(
    *,
    messages: list[dict[str, str]],
    model: str | None = None,
    api_base: str | None = None,
    temperature: float = 0.0,
    retries: int = 1,
    timeout: float | None = None,
) -> tuple[dict[str, Any], str, str | None]:
    chosen_model = model or os.getenv("EVAL_JUDGE_MODEL") or os.getenv("LLM_MODEL", "openai/gpt-4o-mini")
    chosen_base = api_base if api_base is not None else (os.getenv("EVAL_JUDGE_BASE_URL") or os.getenv("LLM_BASE_URL") or None)
    try:
        return completion_json_with_retries(
            messages=messages,
            model=chosen_model,
            api_base=chosen_base,
            temperature=temperature,
            retries=retries,
            timeout=timeout,
        )
    except Exception as exc:
        raise RuntimeError(f"Eval LLM call failed: {exc}") from exc
