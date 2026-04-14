"""RentMate agent client.

When ``RENTMATE_AGENT_URL`` is set, agent calls go to the hosted service.
Otherwise, falls back to the local agent.

``chat_with_agent`` is the core LLM execution function — it initializes
the AI agent, runs a conversation, and bridges progress events.
"""
import asyncio
import contextvars
import json
import os
import queue
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import httpx

from backends.local_auth import (
    reset_fallback_request_context,
    resolve_account_id,
    resolve_org_id,
    set_fallback_request_context,
)
from llm.model_config import resolve_model_config
from llm.registry import agent_registry, ensure_agent_runtime_dirs
from llm.tools import current_user_message
from llm.tracing import log_trace, make_trace_envelope

AGENT_URL = os.getenv("RENTMATE_AGENT_URL")  # e.g. https://agent.rentmate.com

current_trace_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "current_trace_context",
    default=None,
)
current_failed_tools: contextvars.ContextVar[list[dict[str, Any]]] = contextvars.ContextVar(
    "current_failed_tools",
    default=[],
)
current_completed_tools: contextvars.ContextVar[list[str]] = contextvars.ContextVar(
    "current_completed_tools",
    default=[],
)


@dataclass
class AgentResponse:
    reply: str
    side_effects: list[dict] = field(default_factory=list)


# ─── Tool labels for progress display ────────────────────────────────────────

_TOOL_LABELS = {
    "lookup_vendors": "Searching vendors",
    "propose_task": "Proposing task",
    "close_task": "Closing task",
    "message_person": "Sending message",
    "create_vendor": "Creating vendor",
    "save_memory": "Saving note",
    "recall_memory": "Checking memory",
    "edit_memory": "Editing memory",
    "create_property": "Creating property",
    "create_tenant": "Creating tenant",
    "create_suggestion": "Creating suggestion",
    "create_scheduled_task": "Scheduling task",
    "create_document": "Creating document",
    "read_document": "Reading document",
    "analyze_document": "Analyzing document",
    "update_onboarding": "Updating setup progress",
}


_DOCUMENT_CLAIM_PATTERNS = [
    re.compile(pattern, re.I)
    for pattern in [
        r"\bcreated document\b",
        r"\bi(?:'ve| have) created .*document\b",
        r"\bavailable in your documents area\b",
        r"\bdownload (?:it|the pdf)\b",
    ]
]

_MUTATING_TOOLS = {
    "propose_task",
    "close_task",
    "message_person",
    "create_vendor",
    "create_property",
    "create_tenant",
    "create_suggestion",
    "create_scheduled_task",
    "create_document",
}


def _collect_pending_side_effects(*, pending_items: list[dict] | None) -> list[dict]:
    side_effects: list[dict] = []
    for pending in (pending_items or []):
        side_effects.append({
            "type": pending.get("type", "suggestion_message"),
            **pending,
        })
    return side_effects


def _reply_claims_document_created(reply: str) -> bool:
    return any(pattern.search(reply or "") for pattern in _DOCUMENT_CLAIM_PATTERNS)


def _has_document_side_effect(side_effects: list[dict]) -> bool:
    for effect in side_effects:
        action_card = ((effect.get("meta") or {}).get("action_card") or {}) if isinstance(effect, dict) else {}
        if action_card.get("kind") == "document":
            return True
    return False


def _failed_mutating_tool_switched(
    *,
    failed_tools: list[dict[str, Any]],
    completed_tools: list[str],
) -> tuple[str, str] | None:
    failed_mutating = [
        tool.get("tool_name")
        for tool in failed_tools
        if tool.get("tool_name") in _MUTATING_TOOLS
    ]
    completed_mutating = [tool for tool in completed_tools if tool in _MUTATING_TOOLS]
    for failed_tool in failed_mutating:
        for completed_tool in completed_mutating:
            if completed_tool != failed_tool:
                return failed_tool, completed_tool
    return None


def _synthesize_failed_tool_reply(failed_tools: list[dict[str, Any]]) -> str | None:
    if not failed_tools:
        return None
    failed_tool = failed_tools[-1]
    tool_name = str(failed_tool.get("tool_name") or "tool")
    label = _TOOL_LABELS.get(tool_name, tool_name.replace("_", " "))
    raw_error = str(failed_tool.get("error") or "").strip()
    if raw_error:
        if len(raw_error) > 500:
            raw_error = raw_error[:500] + "…"
        return f"{label} failed: {raw_error}"
    return f"{label} failed."


def _reply_is_only_tool_progress(reply: str) -> bool:
    lines = [line.strip() for line in (reply or "").splitlines() if line.strip()]
    if not lines:
        return True
    for line in lines:
        if line == "Thinking…":
            continue
        if line.startswith("Thinking ("):
            continue
        if any(
            line == label or line.startswith(f"{label}:")
            for label in _TOOL_LABELS.values()
        ):
            continue
        return False
    return True


# ─── Local agent execution ───────────────────────────────────────────────────


async def chat_with_agent(
    agent_id: str,
    session_key: str,
    messages: list[dict],
    on_progress: Optional[Callable] = None,
    trace_context: dict[str, Any] | None = None,
) -> str:
    """Run the AI agent with the given messages and return its text reply."""
    from run_agent import AIAgent  # noqa: F401 — optional dep

    model = os.getenv("LLM_MODEL", "anthropic/claude-haiku-4-5-20251001")
    api_key = os.getenv("LLM_API_KEY", "")
    api_base = os.getenv("LLM_BASE_URL") or None
    resolved_model = resolve_model_config(model=model, api_base=api_base)
    actual_model = resolved_model.model
    api_base = resolved_model.api_base
    provider = resolved_model.provider

    # Extract system message and conversation history
    system_message = agent_registry.build_system_prompt(agent_id)
    sys_content = next((m["content"] for m in messages if m.get("role") == "system"), None)
    if sys_content:
        system_message = f"{system_message}\n\n---\n\n{sys_content}"

    conversation_history = [
        {"role": m["role"], "content": m["content"]}
        for m in messages
        if m.get("role") in ("user", "assistant")
        # Filter out poisoned responses that contain simulated tool calls
        and "[True]" not in (m.get("content") or "")
    ]

    user_message = ""
    if conversation_history and conversation_history[-1]["role"] == "user":
        user_message = conversation_history.pop()["content"]
    user_message_token = current_user_message.set(user_message)

    # Queue for bridging progress from the sync agent thread to async SSE
    progress_queue: queue.Queue[str] = queue.Queue()
    progress_events: list[str] = []

    # Extract task_id from session_key for tracing (e.g. "task:abc-123")
    _trace_task_id = session_key.split(":", 1)[1] if session_key.startswith("task:") else None
    _trace_source = "assess" if session_key.startswith("eval:") else ("chat" if not _trace_task_id else "chat")
    _trace_conversation_id = str((trace_context or {}).get("conversation_id") or "") or None

    def _tool_progress(event_type: str, tool_name: str, preview: str | None, args: dict | None, **kwargs):
        label = _TOOL_LABELS.get(tool_name, tool_name)
        trace_detail = current_trace_context.get() or trace_context or {}
        trace_conversation_id = (
            str(trace_detail.get("conversation_id") or "") if isinstance(trace_detail, dict) else ""
        ) or _trace_conversation_id
        if event_type == "tool.started":
            hint = ""
            if args:
                if tool_name == "lookup_vendors" and args.get("vendor_type"):
                    hint = f" ({args['vendor_type']})"
                elif tool_name == "propose_task" and args.get("title"):
                    hint = f": {args['title'][:60]}"
                elif tool_name == "save_memory":
                    et = args.get("entity_type", "general")
                    el = args.get("entity_label", "")
                    if el:
                        hint = f" → {et}: {el}"
                    elif et != "general":
                        hint = f" → {et}"
                elif tool_name == "recall_memory":
                    et = args.get("entity_type")
                    if et:
                        hint = f" ({et})"
                elif tool_name == "message_person":
                    etype = args.get("entity_type", "")
                    draft = args.get("draft_message", "")
                    hint = f" → {etype}"
                    if draft:
                        hint += f": {draft[:80]}"
                elif tool_name == "create_property":
                    addr = args.get("address", "")
                    hint = f": {addr[:60]}" if addr else ""
                elif tool_name == "create_tenant":
                    name = f"{args.get('first_name', '')} {args.get('last_name', '')}".strip()
                    hint = f": {name}" if name else ""
                elif tool_name == "create_suggestion":
                    title = args.get("title", "")
                    hint = f": {title[:60]}" if title else ""
                elif tool_name in ("read_document", "analyze_document"):
                    doc_id = args.get("document_id", "")
                    if doc_id:
                        try:
                            from db.models import Document as _Doc
                            from db.session import SessionLocal as _SL
                            _db = _SL()
                            _d = _db.query(_Doc.filename).filter_by(id=doc_id).first()
                            _db.close()
                            hint = f": {_d[0]}" if _d else f" ({doc_id[:12]}…)"
                        except Exception:
                            hint = f" ({doc_id[:12]}…)"
                    if args.get("list_recent"):
                        hint = " (listing recent)"
                elif tool_name == "edit_memory":
                    et = args.get("entity_type", "")
                    hint = f" → {et}" if et else ""
                elif tool_name == "close_task":
                    hint = ""
            msg = f"{label}{hint}"
            progress_events.append(msg)
            progress_queue.put(msg)
            log_trace(
                "tool_call",
                _trace_source,
                msg,
                task_id=_trace_task_id,
                conversation_id=trace_conversation_id,
                tool_name=tool_name,
                detail=make_trace_envelope(
                    "tool_call",
                    tool_name=tool_name,
                    args=args or {},
                    preview=preview,
                    trace_context=trace_detail,
                ),
            )
        elif event_type == "tool.completed":
            is_error = kwargs.get("is_error", False)
            if is_error:
                failed = list(current_failed_tools.get())
                failed.append({
                    "tool_name": tool_name,
                    "args": args or {},
                    "error": kwargs.get("error") or kwargs.get("result") or "",
                })
                current_failed_tools.set(failed)
                raw_error_detail = kwargs.get("error", "") or kwargs.get("result", "")
                display_error = raw_error_detail
                if isinstance(display_error, str) and len(display_error) > 220:
                    display_error = display_error[:220] + "…"
                msg = f"{label}: error" + (f" — {display_error}" if display_error else "")
                progress_events.append(msg)
                progress_queue.put(msg)
                log_trace(
                    "error",
                    _trace_source,
                    msg,
                    task_id=_trace_task_id,
                    conversation_id=trace_conversation_id,
                    tool_name=tool_name,
                    detail=make_trace_envelope(
                        "tool_error",
                        tool_name=tool_name,
                        error=str(raw_error_detail),
                        result=kwargs.get("result"),
                        trace_context=trace_detail,
                    ),
                )
            else:
                completed = list(current_completed_tools.get())
                completed.append(tool_name)
                current_completed_tools.set(completed)
                result = kwargs.get("result", "")
                if isinstance(result, str) and len(result) > 500:
                    result = result[:500] + "…"
                log_trace(
                    "tool_result",
                    _trace_source,
                    f"{label} completed",
                    task_id=_trace_task_id,
                    conversation_id=trace_conversation_id,
                    tool_name=tool_name,
                    detail=make_trace_envelope(
                        "tool_result",
                        tool_name=tool_name,
                        result=result,
                        trace_context=trace_detail,
                    ),
                )

    def _step_callback(iteration: int, prev_tools: list | None, **kwargs):
        pass  # progress is emitted via _tool_progress

    print(f"[agent] model={actual_model} provider={provider} base_url={api_base}")
    print(f"[agent] system_prompt={len(system_message)} chars, history={len(conversation_history)} msgs, user_message={len(user_message)} chars")
    runtime_dirs = ensure_agent_runtime_dirs(agent_id)
    hermes_home = runtime_dirs["hermes_home"]

    agent = AIAgent(
        base_url=api_base,
        api_key=api_key,
        provider=provider,
        model=actual_model,
        max_iterations=40,
        enabled_toolsets=["rentmate"],
        quiet_mode=True,
        platform="api",
        session_id=session_key,
        skip_context_files=True,
        skip_memory=True,
        hermes_home=hermes_home,
        tool_progress_callback=_tool_progress,
        step_callback=_step_callback,
        verbose_logging=bool(os.getenv("AGENT_VERBOSE")),
    )
    agent._tool_use_enforcement = True

    _orig_build = agent._build_api_kwargs

    def _patched_build_api_kwargs(messages):
        kw = _orig_build(messages)
        if agent.tools and "tools" in kw and "tool_choice" not in kw:
            kw["tool_choice"] = "auto"
        return kw
    agent._build_api_kwargs = _patched_build_api_kwargs

    async def _run_with_progress():
        loop = asyncio.get_event_loop()
        executor_context = contextvars.copy_context()
        task = loop.run_in_executor(
            None,
            lambda: executor_context.run(
                agent.run_conversation,
                user_message=user_message,
                system_message=system_message,
                conversation_history=conversation_history if conversation_history else None,
            ),
        )
        while not task.done():
            try:
                msg = progress_queue.get_nowait()
                if msg and on_progress:
                    await on_progress(msg)
            except queue.Empty:
                pass
            await asyncio.sleep(0.1)
        while not progress_queue.empty():
            msg = progress_queue.get_nowait()
            if msg and on_progress:
                await on_progress(msg)
        return task.result()

    try:
        result = await _run_with_progress()
    finally:
        current_user_message.reset(user_message_token)

    if isinstance(result, dict):
        print(f"[agent] api_calls={result.get('api_calls', '?')} "
              f"completed={result.get('completed', '?')} "
              f"input_tokens={result.get('input_tokens', '?')} "
              f"output_tokens={result.get('output_tokens', '?')} "
              f"progress_events={len(progress_events)}")
        if progress_events:
            for evt in progress_events:
                print(f"[agent]   progress: {evt}")
        reply = result.get("final_response", "")
        if not reply:
            msgs = result.get("messages", [])
            for m in reversed(msgs):
                if m.get("role") == "assistant" and m.get("content"):
                    reply = m["content"]
                    break
        # The agent library returns API errors as normal text replies
        # rather than raising exceptions.  Detect these and re-raise so
        # the SSE error path fires (red bubble, not blue).
        if reply and _is_agent_error_reply(reply):
            raise RuntimeError(reply)
        return reply
    return str(result)


def _is_agent_error_reply(reply: str) -> bool:
    """Return True if the agent reply is actually an API/infrastructure error."""
    _ERROR_PREFIXES = (
        "API call failed",
        "Operation interrupted",
        "I apologize, but I encountered repeated errors",
    )
    return reply.startswith(_ERROR_PREFIXES)


# ─── Public API ──────────────────────────────────────────────────────────────


async def call_agent(
    agent_id: str,
    *, session_key: str,
    messages: list[dict],
    on_progress: Optional[Callable] = None,
    account_context: dict[str, Any] | None = None,
    trace_context: dict[str, Any] | None = None,
) -> AgentResponse:
    """Call the agent and return its response.

    If ``RENTMATE_AGENT_URL`` is configured, sends an HTTP request to the
    hosted service.  Otherwise, falls back to the local agent.
    """
    if not AGENT_URL:
        return await _local_fallback(agent_id, session_key, messages, on_progress, trace_context)

    stream = on_progress is not None
    payload = {
        "agent_id": agent_id,
        "session_key": session_key,
        "messages": messages,
        "stream": stream,
    }
    if account_context:
        payload["account_context"] = account_context

    if stream:
        return await _stream_request(payload, on_progress)
    else:
        return await _sync_request(payload)


async def _stream_request(
    payload: dict,
    on_progress: Callable,
) -> AgentResponse:
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
        async with client.stream("POST", f"{AGENT_URL}/v1/agent/chat", json=payload) as resp:
            resp.raise_for_status()
            buffer = ""
            async for chunk in resp.aiter_text():
                buffer += chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line[6:])
                    if event["type"] == "progress":
                        await on_progress(event.get("text", ""), tool_hint=event.get("tool_hint"))
                    elif event["type"] == "done":
                        return AgentResponse(
                            reply=event["reply"],
                            side_effects=event.get("side_effects", []),
                        )
                    elif event["type"] == "error":
                        raise RuntimeError(event.get("message", "Agent error"))
    raise RuntimeError("Agent stream ended without done event")


async def _sync_request(payload: dict) -> AgentResponse:
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
        resp = await client.post(f"{AGENT_URL}/v1/agent/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return AgentResponse(
            reply=data["reply"],
            side_effects=data.get("side_effects", []),
        )


async def _local_fallback(
    agent_id: str,
    session_key: str,
    messages: list[dict],
    on_progress: Optional[Callable] = None,
    trace_context: dict[str, Any] | None = None,
) -> AgentResponse:
    """Run the agent locally (dev mode)."""
    from llm.tools import current_user_message, pending_suggestion_messages

    token = pending_suggestion_messages.set([])
    failed_tools_token = current_failed_tools.set([])
    completed_tools_token = current_completed_tools.set([])
    latest_user = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
    user_token = current_user_message.set(latest_user)
    trace_token = current_trace_context.set(trace_context)
    fallback_token = None
    try:
        fallback_token = set_fallback_request_context(
            account_id=resolve_account_id(),
            org_id=resolve_org_id(),
        )
    except RuntimeError:
        fallback_token = None
    try:
        reply = await chat_with_agent(agent_id, session_key, messages, on_progress, trace_context=trace_context)
        side_effects = _collect_pending_side_effects(pending_items=pending_suggestion_messages.get())
        failed_tools = list(current_failed_tools.get())
        completed_tools = list(current_completed_tools.get())
        if _reply_claims_document_created(reply) and not _has_document_side_effect(side_effects):
            log_trace(
                "error",
                "chat",
                "Assistant claimed document creation without create_document tool call",
                conversation_id=str((trace_context or {}).get("conversation_id") or "") or None,
                detail=make_trace_envelope(
                    "tool_enforcement",
                    expected_tool="create_document",
                    reply=reply,
                    trace_context=trace_context,
                ),
            )
            pending_suggestion_messages.set([])
            corrective_messages = [
                *messages,
                {"role": "assistant", "content": reply},
                {
                    "role": "user",
                    "content": (
                        "System correction: you claimed a document was created, but no create_document tool was called. "
                        "If the user asked for a document, call create_document now. "
                        "Do not claim a document exists unless the tool succeeds."
                    ),
                },
            ]
            reply = await chat_with_agent(
                agent_id,
                session_key,
                corrective_messages,
                on_progress,
                trace_context=trace_context,
            )
            side_effects = _collect_pending_side_effects(pending_items=pending_suggestion_messages.get())
            if _reply_claims_document_created(reply) and not _has_document_side_effect(side_effects):
                reply = (
                    "I did not create the document. The document tool was not executed successfully, "
                    "so there is no new file in Documents."
                )
        switched_mutating_tool = _failed_mutating_tool_switched(
            failed_tools=failed_tools,
            completed_tools=completed_tools,
        )
        if switched_mutating_tool:
            failed_tool, replacement_tool = switched_mutating_tool
            log_trace(
                "error",
                "chat",
                "Assistant switched to a different mutating tool after tool failure",
                conversation_id=str((trace_context or {}).get("conversation_id") or "") or None,
                detail=make_trace_envelope(
                    "tool_enforcement",
                    failed_tool=failed_tool,
                    replacement_tool=replacement_tool,
                    reply=reply,
                    trace_context=trace_context,
                ),
            )
            pending_suggestion_messages.set([])
            current_failed_tools.set([])
            current_completed_tools.set([])
            corrective_messages = [
                *messages,
                {"role": "assistant", "content": reply},
                {
                    "role": "user",
                    "content": (
                        "System correction: your last mutating tool failed. "
                        f"Do not switch from {failed_tool} to a different mutating tool such as {replacement_tool} in the same turn. "
                        "Either retry the same tool if appropriate, ask the user for missing input, or explain the failure without creating other side effects."
                    ),
                },
            ]
            reply = await chat_with_agent(
                agent_id,
                session_key,
                corrective_messages,
                on_progress,
                trace_context=trace_context,
            )
            side_effects = _collect_pending_side_effects(pending_items=pending_suggestion_messages.get())
            failed_tools = list(current_failed_tools.get())
            completed_tools = list(current_completed_tools.get())
            if _failed_mutating_tool_switched(
                failed_tools=failed_tools,
                completed_tools=completed_tools,
            ):
                pending_suggestion_messages.set([])
                side_effects = []
                reply = (
                    f"The requested action failed when attempting {failed_tool}. "
                    "I did not perform a different side effect in its place."
                )
        synthesized_failure_reply = _synthesize_failed_tool_reply(failed_tools)
        if synthesized_failure_reply and not side_effects and _reply_is_only_tool_progress(reply):
            reply = synthesized_failure_reply
        return AgentResponse(reply=reply, side_effects=side_effects)
    finally:
        if fallback_token is not None:
            reset_fallback_request_context(fallback_token)
        current_user_message.reset(user_token)
        current_trace_context.reset(trace_token)
        current_failed_tools.reset(failed_tools_token)
        current_completed_tools.reset(completed_tools_token)
        pending_suggestion_messages.reset(token)
