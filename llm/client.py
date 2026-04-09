"""RentMate agent client.

When ``RENTMATE_AGENT_URL`` is set, agent calls go to the hosted service.
Otherwise, falls back to the local agent.

``chat_with_agent`` is the core LLM execution function — it initializes
the AI agent, runs a conversation, and bridges progress events.
"""
import asyncio
import json
import os
import queue
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import httpx

from llm.registry import agent_registry
from llm.tracing import log_trace

AGENT_URL = os.getenv("RENTMATE_AGENT_URL")  # e.g. https://agent.rentmate.com


@dataclass
class AgentResponse:
    reply: str
    side_effects: list[dict] = field(default_factory=list)


# ─── Tool labels for progress display ────────────────────────────────────────

_TOOL_LABELS = {
    "lookup_vendors": "Searching vendors",
    "propose_task": "Proposing task",
    "close_task": "Closing task",
    "set_mode": "Changing mode",
    "attach_vendor": "Assigning vendor",
    "attach_entity": "Attaching to task",
    "message_person": "Sending message",
    "create_vendor": "Creating vendor",
    "update_steps": "Updating progress",
    "save_memory": "Saving note",
    "recall_memory": "Checking memory",
    "edit_memory": "Editing memory",
    "create_property": "Creating property",
    "create_tenant": "Creating tenant",
    "create_suggestion": "Creating suggestion",
    "read_document": "Reading document",
    "analyze_document": "Analyzing document",
    "update_onboarding": "Updating setup progress",
}


# ─── Local agent execution ───────────────────────────────────────────────────


async def chat_with_agent(
    agent_id: str,
    session_key: str,
    messages: list[dict],
    on_progress: Optional[Callable] = None,
) -> str:
    """Run the AI agent with the given messages and return its text reply."""
    from run_agent import AIAgent  # noqa: F401 — optional dep

    model = os.getenv("LLM_MODEL", "anthropic/claude-haiku-4-5-20251001")
    api_key = os.getenv("LLM_API_KEY", "")
    api_base = os.getenv("LLM_BASE_URL") or None

    # Map LiteLLM-style provider/model names to direct API endpoints
    provider = None
    actual_model = model
    if "/" in model and not api_base:
        provider_prefix, _, model_name = model.partition("/")
        _PROVIDER_BASES = {
            "deepseek": ("https://api.deepseek.com", None),
            "anthropic": ("https://api.anthropic.com/v1", "anthropic"),
        }
        if provider_prefix in _PROVIDER_BASES:
            api_base, provider = _PROVIDER_BASES[provider_prefix]
            actual_model = model_name

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

    # Queue for bridging progress from the sync agent thread to async SSE
    progress_queue: queue.Queue[str] = queue.Queue()
    progress_events: list[str] = []

    # Extract task_id from session_key for tracing (e.g. "task:abc-123")
    _trace_task_id = session_key.split(":", 1)[1] if session_key.startswith("task:") else None
    _trace_source = "assess" if session_key.startswith("eval:") else ("chat" if not _trace_task_id else "chat")

    def _tool_progress(event_type: str, tool_name: str, preview: str | None, args: dict | None, **kwargs):
        label = _TOOL_LABELS.get(tool_name, tool_name)
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
                elif tool_name == "attach_entity":
                    etype = args.get("entity_type", "")
                    hint = f" ({etype})" if etype else ""
                elif tool_name == "message_person":
                    etype = args.get("entity_type", "")
                    draft = args.get("draft_message", "")
                    hint = f" → {etype}"
                    if draft:
                        hint += f": {draft[:80]}"
                elif tool_name == "update_steps":
                    steps = args.get("steps")
                    if steps and isinstance(steps, list):
                        labels = [s.get("label", "") for s in steps[:3] if isinstance(s, dict)]
                        hint = f": {', '.join(l for l in labels if l)}" if labels else ""
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
                    hint = f" ({doc_id[:12]}…)" if doc_id else ""
                    if args.get("list_recent"):
                        hint = " (listing recent)"
                elif tool_name == "edit_memory":
                    et = args.get("entity_type", "")
                    hint = f" → {et}" if et else ""
                elif tool_name == "close_task":
                    hint = ""
                elif tool_name == "set_mode":
                    mode = args.get("mode", "")
                    hint = f" → {mode}" if mode else ""
            msg = f"{label}{hint}"
            progress_events.append(msg)
            progress_queue.put(msg)
            log_trace("tool_call", _trace_source, msg, task_id=_trace_task_id,
                      tool_name=tool_name, detail=args)
        elif event_type == "tool.completed":
            is_error = kwargs.get("is_error", False)
            if is_error:
                error_detail = kwargs.get("error", "") or kwargs.get("result", "")
                if isinstance(error_detail, str) and len(error_detail) > 120:
                    error_detail = error_detail[:120] + "…"
                msg = f"{label}: error" + (f" — {error_detail}" if error_detail else "")
                progress_events.append(msg)
                progress_queue.put(msg)
                log_trace("error", _trace_source, msg, task_id=_trace_task_id,
                          tool_name=tool_name, detail={"error": str(error_detail)})
            else:
                result = kwargs.get("result", "")
                if isinstance(result, str) and len(result) > 500:
                    result = result[:500] + "…"
                log_trace("tool_result", _trace_source, f"{label} completed",
                          task_id=_trace_task_id, tool_name=tool_name, detail={"result": result})

    def _step_callback(iteration: int, prev_tools: list | None, **kwargs):
        pass  # progress is emitted via _tool_progress

    print(f"[agent] model={actual_model} provider={provider} base_url={api_base}")
    print(f"[agent] system_prompt={len(system_message)} chars, history={len(conversation_history)} msgs, user_message={len(user_message)} chars")

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
        task = loop.run_in_executor(
            None,
            lambda: agent.run_conversation(
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

    result = await _run_with_progress()

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
) -> AgentResponse:
    """Call the agent and return its response.

    If ``RENTMATE_AGENT_URL`` is configured, sends an HTTP request to the
    hosted service.  Otherwise, falls back to the local agent.
    """
    if not AGENT_URL:
        return await _local_fallback(agent_id, session_key, messages, on_progress)

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
) -> AgentResponse:
    """Run the agent locally (dev mode)."""
    from llm.tools import pending_suggestion_messages

    token = pending_suggestion_messages.set([])
    try:
        reply = await chat_with_agent(agent_id, session_key, messages, on_progress)
        side_effects = []
        for pending in (pending_suggestion_messages.get() or []):
            side_effects.append({
                "type": "suggestion_message",
                **pending,
            })
        return AgentResponse(reply=reply, side_effects=side_effects)
    finally:
        pending_suggestion_messages.reset(token)
