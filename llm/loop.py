"""In-house agent reasoning loop using LiteLLM as the unified LLM client.

Replaces ``run_agent.AIAgent`` from hermes-agent. Consumes tool definitions
and dispatch from ``llm.dispatch``, calls ``litellm.acompletion`` with the
OpenAI Chat Completions schema, parses tool calls from the response, and
iterates until the model returns a turn with no tool calls or
``max_iterations`` is reached.

Tenant safety: AgentLoop instances are per-request. Each loop captures the
authenticated account_id at construction time and re-asserts the active
contextvar matches before every tool dispatch — a belt-and-braces guard
against contextvar bleed across tenants.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import litellm

from backends.local_auth import resolve_account_id
from llm.dispatch import dispatch, tool_definitions

logger = logging.getLogger(__name__)


ProgressCallback = Callable[..., Any]
CompleteCallback = Callable[[str, str, dict[str, Any], str], Any]
StepCallback = Callable[..., Any]


class TenantIsolationError(RuntimeError):
    """Raised when the active account contextvar differs from the loop's bound account.

    Should never fire in normal operation. If it does, a request crossed
    tenants — fail loudly rather than risk leaking data.
    """


class AgentLoop:
    """Per-request agent reasoning loop.

    One instance per ``chat_with_agent`` call. Not safe to share across
    requests — internal message buffer mutates as the loop runs.
    """

    def __init__(
        self,
        *,
        model: str,
        system_message: str,
        account_id: int,
        org_id: int | None = None,
        max_iterations: int = 40,
        tool_progress_callback: ProgressCallback | None = None,
        tool_complete_callback: CompleteCallback | None = None,
        step_callback: StepCallback | None = None,
        extra_completion_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self.model = model
        self.system_message = system_message
        self.account_id = int(account_id)
        self.org_id = int(org_id) if org_id is not None else None
        self.max_iterations = max_iterations
        self.tool_progress_callback = tool_progress_callback
        self.tool_complete_callback = tool_complete_callback
        self.step_callback = step_callback
        self.extra_completion_kwargs = dict(extra_completion_kwargs or {})

    def _assert_tenant(self) -> None:
        try:
            active = resolve_account_id()
        except RuntimeError as e:
            raise TenantIsolationError(
                f"No account contextvar set inside agent loop bound to account {self.account_id}"
            ) from e
        if active != self.account_id:
            raise TenantIsolationError(
                f"Active account {active} does not match agent loop's bound account {self.account_id}"
            )

    async def run(
        self,
        *,
        user_message: str,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        self._assert_tenant()

        messages: list[dict[str, Any]] = []
        if self.system_message:
            messages.append({"role": "system", "content": self.system_message})
        for prior in conversation_history or []:
            role = prior.get("role")
            if role in ("user", "assistant"):
                messages.append({"role": role, "content": prior.get("content", "")})
        if user_message:
            messages.append({"role": "user", "content": user_message})

        tools = tool_definitions()
        input_tokens_total = 0
        output_tokens_total = 0
        api_calls = 0
        completed_tools: list[str] = []
        final_response = ""

        for iteration in range(self.max_iterations):
            self._assert_tenant()
            if self.step_callback is not None:
                try:
                    maybe = self.step_callback(iteration=iteration, prev_tools=list(completed_tools))
                    if asyncio.iscoroutine(maybe):
                        await maybe
                except Exception:
                    logger.exception("step_callback raised")

            try:
                response = await litellm.acompletion(
                    model=self.model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    **self.extra_completion_kwargs,
                )
            except Exception as e:
                logger.exception("litellm.acompletion failed")
                final_response = f"API call failed: {e}"
                break

            api_calls += 1
            usage = getattr(response, "usage", None)
            if usage is not None:
                input_tokens_total += int(getattr(usage, "prompt_tokens", 0) or 0)
                output_tokens_total += int(getattr(usage, "completion_tokens", 0) or 0)

            choice = response.choices[0]
            msg = choice.message
            assistant_text = getattr(msg, "content", None) or ""
            tool_calls = getattr(msg, "tool_calls", None) or []

            if not tool_calls:
                final_response = assistant_text
                messages.append({"role": "assistant", "content": assistant_text})
                break

            assistant_msg = {
                "role": "assistant",
                "content": assistant_text or None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments
                            if isinstance(tc.function.arguments, str)
                            else json.dumps(tc.function.arguments or {}),
                        },
                    }
                    for tc in tool_calls
                ],
            }
            messages.append(assistant_msg)

            for tc in tool_calls:
                self._assert_tenant()
                fn_name = tc.function.name
                raw_args = tc.function.arguments
                if isinstance(raw_args, str):
                    try:
                        fn_args = json.loads(raw_args) if raw_args.strip() else {}
                    except json.JSONDecodeError:
                        fn_args = {}
                        logger.warning("Tool %s sent unparseable JSON args: %r", fn_name, raw_args)
                else:
                    fn_args = dict(raw_args or {})

                preview = ""
                await _maybe_call(
                    self.tool_progress_callback,
                    event_type="tool.started",
                    tool_name=fn_name,
                    preview=preview,
                    args=fn_args,
                )

                try:
                    result = await dispatch(fn_name, fn_args)
                except Exception as e:
                    logger.exception("Tool %s raised", fn_name)
                    result = json.dumps({"status": "error", "error": str(e)})

                await _maybe_call(
                    self.tool_complete_callback,
                    tc.id,
                    fn_name,
                    fn_args,
                    result,
                )
                await _maybe_call(
                    self.tool_progress_callback,
                    event_type="tool.completed",
                    tool_name=fn_name,
                    preview=None,
                    args=fn_args,
                )

                completed_tools.append(fn_name)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result if isinstance(result, str) else str(result),
                })
        else:
            logger.warning("AgentLoop hit max_iterations=%d without resolving", self.max_iterations)
            if not final_response:
                final_response = "I apologize, but I encountered repeated errors and was unable to complete your request."

        return {
            "final_response": final_response,
            "messages": messages,
            "input_tokens": input_tokens_total,
            "output_tokens": output_tokens_total,
            "api_calls": api_calls,
            "completed": completed_tools,
        }


async def _maybe_call(callback: Any, *args: Any, **kwargs: Any) -> None:
    if callback is None:
        return
    try:
        result = callback(*args, **kwargs)
        if asyncio.iscoroutine(result):
            await result
    except Exception:
        logger.exception("AgentLoop callback raised")
