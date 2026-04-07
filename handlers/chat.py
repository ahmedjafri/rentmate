import asyncio
import hashlib as _hb_hashlib
import json as _json
import os
import threading as _hb_threading
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from db.enums import TaskSource
from db.lib import get_conversation_with_messages, record_sms_from_quo, route_inbound_to_tenant_chat
from db.models import Conversation, Message, MessageType, ParticipantType, Task
from gql.services import chat_service
from handlers.deps import get_db, require_user
from llm.context import build_task_context, load_account_context
from llm.registry import DATA_DIR, agent_registry

router = APIRouter()

# ─── In-flight chat registry ─────────────────────────────────────────────────
# Tracks agent chats that are still running so reconnecting clients can pick up
# the live progress stream.  Keyed by a request-scoped stream_id.

@dataclass
class _RunningChat:
    task: asyncio.Task
    subscribers: list = field(default_factory=list)   # list[asyncio.Queue]
    progress_log: list = field(default_factory=list)   # list[str] — replay buffer

_active_chats: Dict[str, _RunningChat] = {}

QUO_API_KEY = os.getenv("QUO_API_KEY", "")
PHONE_WHITELIST = [p.strip() for p in os.getenv("PHONE_WHITELIST", "").split(",") if p.strip()]


def is_in_whitelist(number: str) -> bool:
    return any(allowed in number for allowed in PHONE_WHITELIST)




async def chat_with_agent(
    agent_id: str,
    session_key: str,
    messages: list[dict],
    on_progress: Optional[Callable] = None,
) -> str:
    """Run the Hermes agent with the given messages and return its text reply."""
    from run_agent import AIAgent

    model = os.getenv("LLM_MODEL", "anthropic/claude-haiku-4-5-20251001")
    api_key = os.getenv("LLM_API_KEY", "")
    api_base = os.getenv("LLM_BASE_URL") or None

    # Map LiteLLM-style provider/model names to direct API endpoints
    provider = None
    actual_model = model
    if "/" in model and not api_base:
        provider_prefix, _, model_name = model.partition("/")
        _PROVIDER_BASES = {
            "deepseek": ("https://api.deepseek.com/v1", None),
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
    import queue as _queue
    progress_queue: _queue.Queue[str] = _queue.Queue()
    progress_events: list[str] = []

    # Pretty tool name mapping
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
    }

    # Extract task_id from session_key for tracing (e.g. "task:abc-123")
    _trace_task_id = session_key.split(":", 1)[1] if session_key.startswith("task:") else None
    _trace_source = "assess" if session_key.startswith("eval:") else ("chat" if not _trace_task_id else "chat")

    def _tool_progress(event_type: str, tool_name: str, preview: str | None, args: dict | None, **kwargs):
        """Hermes tool_progress_callback: (event_type, tool_name, preview, args, **kw)"""
        from llm.tracing import log_trace
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
        """Hermes step_callback: fires after each API call iteration."""
        # Skip — we already emit per-tool progress via _tool_progress.
        # The first iteration (no prev_tools) would duplicate "Thinking…"
        # which the SSE handler already emits.
        pass

    # Log what we're sending to the agent
    print(f"[hermes] model={actual_model} provider={provider} base_url={api_base}")
    print(f"[hermes] system_prompt={len(system_message)} chars, history={len(conversation_history)} msgs, user_message={len(user_message)} chars")

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
        verbose_logging=bool(os.getenv("HERMES_VERBOSE")),
    )
    # Force tool use enforcement — DeepSeek is not in Hermes's default list
    # and will simulate tool calls in text without this
    agent._tool_use_enforcement = True

    # Patch _build_api_kwargs to add tool_choice: "auto" for chat_completions.
    # Hermes omits this, but DeepSeek defaults to "none" without it.
    _orig_build = agent._build_api_kwargs
    def _patched_build_api_kwargs(messages):
        kwargs = _orig_build(messages)
        if agent.tools and "tools" in kwargs and "tool_choice" not in kwargs:
            kwargs["tool_choice"] = "auto"
        return kwargs
    agent._build_api_kwargs = _patched_build_api_kwargs

    async def _run_with_progress():
        import queue as _q
        loop = asyncio.get_event_loop()
        task = loop.run_in_executor(
            None,
            lambda: agent.run_conversation(
                user_message=user_message,
                system_message=system_message,
                conversation_history=conversation_history if conversation_history else None,
            ),
        )
        # Drain progress queue while agent runs
        while not task.done():
            try:
                msg = progress_queue.get_nowait()
                if msg and on_progress:
                    await on_progress(msg)
            except _q.Empty:
                pass
            await asyncio.sleep(0.1)
        # Drain remaining
        while not progress_queue.empty():
            msg = progress_queue.get_nowait()
            if msg and on_progress:
                await on_progress(msg)
        return task.result()

    result = await _run_with_progress()

    if isinstance(result, dict):
        print(f"[hermes] api_calls={result.get('api_calls', '?')} "
              f"completed={result.get('completed', '?')} "
              f"input_tokens={result.get('input_tokens', '?')} "
              f"output_tokens={result.get('output_tokens', '?')} "
              f"progress_events={len(progress_events)}")
        if progress_events:
            for evt in progress_events:
                print(f"[hermes]   progress: {evt}")
        reply = result.get("final_response", "")
        if not reply:
            # Fallback: check messages for last assistant content
            msgs = result.get("messages", [])
            for m in reversed(msgs):
                if m.get("role") == "assistant" and m.get("content"):
                    reply = m["content"]
                    break
        return reply
    return str(result)


def _normalize_phone(num: str) -> str:
    """Ensure phone number has +1 prefix for US numbers."""
    digits = ''.join(c for c in num if c.isdigit())
    if digits.startswith('1') and len(digits) == 11:
        return f"+{digits}"
    if len(digits) == 10:
        return f"+1{digits}"
    if num.startswith('+'):
        return num
    return f"+{digits}"


async def send_sms_reply(from_num: str, to_num: str, text: str, api_key: str | None = None):
    key = api_key or _get_quo_api_key()
    if not key:
        print("[sms] No Quo API key configured — skipping SMS")
        return
    to_num = _normalize_phone(to_num)
    if from_num:
        from_num = _normalize_phone(from_num)
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.openphone.com/v1/messages",
            headers={
                "Authorization": key,
                "Content-Type": "application/json",
            },
            json={
                "content": text,
                "from": from_num,
                "to": [to_num],
            },
        )
        print(f"[sms] Quo response: {response.status_code} {response.text[:200]}")


def _get_quo_api_key() -> str:
    """Get Quo (OpenPhone) API key from integrations config or env var."""
    from handlers.settings import load_integrations
    cfg = load_integrations().get("quo", {})
    return cfg.get("api_key") or QUO_API_KEY


def _get_quo_from_number() -> str:
    """Get the outbound phone number from Quo config."""
    from handlers.settings import load_integrations
    cfg = load_integrations().get("quo", {})
    return cfg.get("from_number") or ""


async def send_email_reply(conv, body: str, inbound_meta: dict):
    """Send an email reply via Gmail. Requires GmailClient to be configured."""
    try:
        from backends.gmail import GmailClient
        client = GmailClient()
        to_address = inbound_meta.get("from_address", "")
        subject = inbound_meta.get("subject", conv.subject or "Re: Your message")
        thread_id = inbound_meta.get("thread_id")
        await asyncio.to_thread(
            client.send_reply,
            to=to_address,
            subject=subject,
            body=body,
            thread_id=thread_id,
        )
    except Exception as e:
        print(f"[send_email_reply] Failed: {e}")


async def send_via_channel(conv, reply: str, inbound_meta: dict):
    """Dispatch an agent reply to the appropriate outbound channel."""
    if conv.channel_type == "sms":
        await send_sms_reply(
            from_num=inbound_meta.get("to_number", ""),
            to_num=inbound_meta.get("from_number", ""),
            text=reply,
        )
    elif conv.channel_type == "email":
        await send_email_reply(conv=conv, body=reply, inbound_meta=inbound_meta)
    # channel_type == None (manual/internal): no automated outbound


# ─── Pydantic models ──────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    task_id: Optional[str] = None


class AssessRequest(BaseModel):
    task_id: str


# ─── Routes ───────────────────────────────────────────────────────────────────

async def process_inbound_sms(db: Session, from_number: str, to_number: str, body: str):
    """Core inbound SMS handler shared by webhook and poller.

    Returns True if the message was processed, False if skipped.
    """
    from backends.wire import sms_router
    resolved = sms_router.resolve(db, from_number, to_number)
    if not resolved:
        print(f"[sms] Sender not resolved for from={from_number} to={to_number}")
        return False

    _account_id, entity, direction, entity_type = resolved

    if direction != "inbound":
        return False

    # ── Vendor inbound SMS ────────────────────────────────────────────
    if entity_type == "vendor":
        from db.models import ConversationType, Message as MsgModel, ParticipantType as PT

        vendor = entity
        conv = chat_service.get_or_create_external_conversation(
            db,
            conversation_type=ConversationType.VENDOR,
            subject=f"SMS with {vendor.name}",
            vendor_id=str(vendor.id),
        )
        now = datetime.now(UTC)
        db.add(MsgModel(
            id=str(_uuid.uuid4()),
            conversation_id=conv.id,
            sender_type=PT.EXTERNAL_CONTACT,
            sender_external_contact_id=str(vendor.id),
            body=body,
            message_type=MessageType.MESSAGE,
            sender_name=vendor.name,
            is_ai=False,
            is_system=False,
            sent_at=now,
        ))
        conv.updated_at = now
        db.commit()
        print(f"[sms] Vendor SMS from {vendor.name}: {body[:80]}")
        return True

    # ── Tenant inbound SMS ────────────────────────────────────────────
    tenant = entity

    if not is_in_whitelist(from_number):
        print(f"[sms] Number not in whitelist (skipping response), num={from_number}")
        record_sms_from_quo(db=db, from_number=from_number, to_number=to_number, body=body)
        return True

    sender_meta = {
        "source": "quo",
        "direction": "inbound",
        "from_number": from_number,
        "to_number": to_number,
    }

    conv, msg = await asyncio.to_thread(
        route_inbound_to_tenant_chat,
        db,
        tenant=tenant,
        body=body,
        channel_type="sms",
        sender_meta=sender_meta,
    )
    db.commit()

    from llm.client import call_agent
    from llm.side_effects import process_side_effects
    context = build_task_context(db, conv.id)
    messages = chat_service.build_agent_message_history(db, conv_id=conv.id, user_message=body, context=context, exclude_last=True)

    from backends.local_auth import resolve_account_id
    agent_id = agent_registry.ensure_agent(resolve_account_id(), db)
    session_key = f"sms:{conv.id}"

    agent_resp = await call_agent(agent_id, session_key=session_key, messages=messages)

    now = datetime.now(UTC)
    ai_msg = Message(
        id=str(_uuid.uuid4()),
        conversation_id=conv.id,
        sender_type=ParticipantType.ACCOUNT_USER,
        body=agent_resp.reply,
        message_type=MessageType.MESSAGE,
        sender_name="RentMate",
        is_ai=True,
        sent_at=now,
    )
    db.add(ai_msg)
    process_side_effects(db, side_effects=agent_resp.side_effects, conversation_id=conv.id, base_time=now)
    db.commit()

    await send_via_channel(conv, agent_resp.reply, inbound_meta=sender_meta)
    return True


@router.post("/quo-webhook")
async def handle_message(
    request: Request,
    db: Session = Depends(get_db),
):
    """Handle inbound SMS from Quo (OpenPhone) webhook (message.received event)."""
    data = await request.json()

    event_type = data.get("type", "")
    payload = data.get("data", data.get("object", data))

    if event_type and event_type != "message.received":
        return {"status": "ok"}

    from_number = payload.get("from", "")
    to_list = payload.get("to", [])
    body = payload.get("body") or payload.get("content") or payload.get("text", "")

    if not from_number or not to_list:
        print(f"[Quo] Missing from/to in payload: {list(payload.keys())}")
        return {"status": "ok"}

    to_number = to_list[0] if isinstance(to_list, list) else to_list

    await process_inbound_sms(db, from_number, to_number, body)
    return {"status": "ok"}



@router.post("/chat/send")
async def chat_endpoint(
    body: ChatRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Unified chat endpoint.  Works for both session chats and task chats.

    Pass ``task_id`` to chat in the context of a task (uses the task's AI
    conversation).  Otherwise provide ``conversation_id`` for a session chat,
    or omit both to auto-create a user_ai conversation.
    """
    await require_user(request)
    from handlers.deps import SessionLocal
    _SL = SessionLocal.session_factory

    # ── Resolve conversation + context ────────────────────────────────────
    task_obj: Task | None = None
    if body.task_id:
        task_obj = db.query(Task).filter_by(id=body.task_id).first()
        if not task_obj:
            raise HTTPException(status_code=404, detail="Task not found")
        conv = task_obj.ai_conversation
        if not conv:
            raise HTTPException(status_code=404, detail="Task has no AI conversation")
        context = build_task_context(db, body.task_id)
    else:
        from db.lib import get_or_create_user_ai_conversation
        context = load_account_context(db)
        if body.conversation_id:
            conv = chat_service.get_or_create_conversation(db, body.conversation_id)
        else:
            conv = get_or_create_user_ai_conversation(db, account_id="default", user_id="default")

    conv_id = conv.id
    db.commit()

    # If this conversation has human participants (tenant/external), stay silent.
    if not chat_service.should_ai_respond(conv):
        async def _no_ai():
            write_db = _SL()
            try:
                chat_service.persist_user_message_only(write_db, conv_id, body.message)
                write_db.commit()
            except Exception as e:
                write_db.rollback()
                print(f"[chat] DB write failed (no-ai path): {e}")
            finally:
                write_db.close()
            yield f"data: {_json.dumps({'type': 'done', 'reply': None, 'conversation_id': conv_id})}\n\n"

        return StreamingResponse(
            _no_ai(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── Build message history ─────────────────────────────────────────────
    if task_obj:
        all_msgs = [
            m for m in (conv.messages or [])
            if m.message_type in (MessageType.MESSAGE, MessageType.THREAD)  # include legacy THREAD
        ]
        msg_rows = sorted(all_msgs, key=lambda m: m.sent_at)[-20:]
        messages_payload = [{"role": "system", "content": context}]
        messages_payload += [
            {"role": "assistant" if m.is_ai else "user", "content": m.body or ""}
            for m in msg_rows
        ]
        messages_payload.append({"role": "user", "content": body.message})
    else:
        messages_payload = chat_service.build_agent_message_history(db, conv_id=conv_id, user_message=body.message, context=context)

    from backends.local_auth import resolve_account_id
    agent_id = agent_registry.ensure_agent(resolve_account_id(), db)
    session_key = f"task:{body.task_id}" if body.task_id else f"chat:{conv_id}"
    stream_id = str(_uuid.uuid4())
    user_message = body.message

    # ── SSE generator ─────────────────────────────────────────────────────
    async def generate():
        queue: asyncio.Queue = asyncio.Queue()

        running = _RunningChat(task=None)
        running.subscribers.append(queue)
        _active_chats[stream_id] = running

        async def on_progress(text: str, tool_hint: str | None = None, **_kwargs):
            entry = f"[{tool_hint}] {text}" if tool_hint else text
            running.progress_log.append(entry)
            for sub in list(running.subscribers):
                await sub.put(entry)

        async def run_and_persist() -> tuple[str, str, list[dict]]:
            from llm.client import call_agent
            from llm.side_effects import process_side_effects
            from llm.tools import active_conversation_id
            token = active_conversation_id.set(conv_id)
            try:
                # Persist user message BEFORE running the agent so it gets an
                # earlier timestamp than any messages the agent creates.
                pre_db = _SL()
                try:
                    pre_db.add(Message(
                        id=str(_uuid.uuid4()),
                        conversation_id=conv_id,
                        sender_type=ParticipantType.ACCOUNT_USER,
                        body=user_message,
                        message_type=MessageType.MESSAGE,
                        sender_name="You",
                        is_ai=False,
                        sent_at=datetime.now(UTC),
                    ))
                    pre_db.commit()
                except Exception:
                    pre_db.rollback()
                finally:
                    pre_db.close()

                await on_progress("Thinking\u2026")
                agent_resp = await call_agent(agent_id, session_key=session_key, messages=messages_payload, on_progress=on_progress)

                write_db = _SL()
                try:
                    now = datetime.now(UTC)
                    # Persist thinking chain as an internal message (only if
                    # there are actual tool-call traces beyond "Thinking…")
                    trace_lines = [l for l in running.progress_log if l != "Thinking\u2026"]
                    if trace_lines:
                        write_db.add(Message(
                            id=str(_uuid.uuid4()),
                            conversation_id=conv_id,
                            sender_type=ParticipantType.ACCOUNT_USER,
                            body="\n".join(trace_lines),
                            message_type=MessageType.INTERNAL,
                            sender_name="RentMate",
                            is_ai=True,
                            sent_at=now,
                        ))
                    # Persist AI reply
                    ai_msg = Message(
                        id=str(_uuid.uuid4()),
                        conversation_id=conv_id,
                        sender_type=ParticipantType.ACCOUNT_USER,
                        body=agent_resp.reply,
                        message_type=MessageType.MESSAGE,
                        sender_name="RentMate",
                        is_ai=True,
                        sent_at=now,
                    )
                    write_db.add(ai_msg)
                    # Materialize side-effects (suggestions, vendor creation)
                    # after the AI reply so they appear below it.
                    flushed_suggestions = process_side_effects(
                        write_db, side_effects=agent_resp.side_effects, conversation_id=conv_id, base_time=now,
                    )
                    db_conv = write_db.query(Conversation).filter_by(id=conv_id).first()
                    if db_conv:
                        db_conv.updated_at = now
                    write_db.commit()
                    print(f"[chat] Persisted AI reply ({len(agent_resp.reply)} chars) to {conv_id}")
                    from llm.tracing import log_trace as _lt
                    _lt("llm_reply", "chat", agent_resp.reply[:200],
                        task_id=body.task_id, conversation_id=conv_id)
                    return agent_resp.reply, ai_msg.id, flushed_suggestions
                except Exception as e:
                    write_db.rollback()
                    print(f"[chat] DB write failed: {e}")
                    import traceback
                    traceback.print_exc()
                    return agent_resp.reply, str(_uuid.uuid4()), []
                finally:
                    write_db.close()
            finally:
                active_conversation_id.reset(token)
                _active_chats.pop(stream_id, None)

        running.task = asyncio.create_task(run_and_persist())

        try:
            # Emit stream_id so the client can reconnect
            yield f"data: {_json.dumps({'type': 'stream_id', 'stream_id': stream_id})}\n\n"

            while not running.task.done():
                try:
                    text = await asyncio.wait_for(queue.get(), timeout=0.1)
                    yield f"data: {_json.dumps({'type': 'progress', 'text': text})}\n\n"
                except asyncio.TimeoutError:
                    pass

            while not queue.empty():
                text = queue.get_nowait()
                yield f"data: {_json.dumps({'type': 'progress', 'text': text})}\n\n"

            try:
                reply, msg_id, suggestion_msgs = running.task.result()
            except Exception as exc:
                print(f"[chat] SSE task failed: {exc!r}")
                yield f"data: {_json.dumps({'type': 'error', 'message': 'AI unavailable'})}\n\n"
                return

            done_payload: dict = {'type': 'done', 'reply': reply, 'message_id': msg_id, 'conversation_id': conv_id}
            if suggestion_msgs:
                done_payload['suggestion_messages'] = suggestion_msgs
            print(f"[chat] SSE done: reply={len(reply or '')} chars, msg_id={msg_id}")
            yield f"data: {_json.dumps(done_payload)}\n\n"
        finally:
            if queue in running.subscribers:
                running.subscribers.remove(queue)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


NO_RESPONSE_SENTINEL = "[NO_RESPONSE]"


# ─── Agent heartbeat ─────────────────────────────────────────────────────────

_heartbeat_locks: dict[str, _hb_threading.Lock] = {}
_heartbeat_locks_lock = _hb_threading.Lock()
_heartbeat_state: dict[str, str] = {}  # task_id → context hash from last run


def _compute_heartbeat_hash(task) -> str:
    """Hash task state + latest message timestamps using a short-lived session."""
    from sqlalchemy import text

    from handlers.deps import SessionLocal
    db = SessionLocal.session_factory()
    try:
        parts = []
        # Task scalar fields that affect context
        row = db.execute(text(
            "SELECT task_status, task_mode, steps, context FROM tasks WHERE id = :id"
        ), {"id": task.id}).first()
        if row:
            parts.append(f"{row[0]}|{row[1]}|{row[2] or ''}|{row[3] or ''}")
        # Latest message timestamp from each linked conversation
        for conv_id in [task.ai_conversation_id, task.external_conversation_id, task.parent_conversation_id]:
            if conv_id:
                ts = db.execute(text(
                    "SELECT MAX(sent_at) FROM messages WHERE conversation_id = :cid"
                ), {"cid": conv_id}).scalar()
                if ts:
                    parts.append(f"{conv_id}:{ts}")
        # Pending suggestion count
        count = db.execute(text(
            "SELECT COUNT(*) FROM suggestions WHERE task_id = :tid AND status = 'pending'"
        ), {"tid": task.id}).scalar()
        parts.append(f"suggestions:{count}")
        return _hb_hashlib.md5("|".join(parts).encode()).hexdigest()
    finally:
        db.close()


def agent_task_heartbeat(task_id: str, hint: str | None = None) -> str | None:
    """Run the agent against a task and let it respond/act.

    This is the core primitive for driving autonomous tasks forward.
    Called when external messages arrive or periodically by the heartbeat loop.

    Returns the agent reply text, or None if no response was needed.
    """
    # Per-task lock prevents concurrent heartbeats for the same task
    with _heartbeat_locks_lock:
        if task_id not in _heartbeat_locks:
            _heartbeat_locks[task_id] = _hb_threading.Lock()
        lock = _heartbeat_locks[task_id]

    if not lock.acquire(blocking=False):
        return None  # another heartbeat is already running for this task

    try:
        return _agent_task_heartbeat_inner(task_id, hint)
    finally:
        lock.release()


def _agent_task_heartbeat_inner(task_id: str, hint: str | None = None) -> str | None:
    import asyncio as _hb_asyncio
    import json as _hb_json

    from backends.local_auth import DEFAULT_USER_ID
    from handlers.deps import SessionLocal
    from llm.client import call_agent
    from llm.registry import agent_registry
    from llm.side_effects import process_side_effects
    from llm.tools import active_conversation_id, pending_suggestion_messages
    from llm.tracing import log_trace

    db = SessionLocal.session_factory()
    try:
        task = db.query(Task).filter_by(id=task_id).first()
        if not task:
            return None
        conv = task.ai_conversation
        if not conv:
            return None

        # Change detection: skip if nothing changed since last heartbeat
        current_hash = _compute_heartbeat_hash(task)
        if _heartbeat_state.get(task_id) == current_hash:
            print(f"\033[33m[heartbeat] Skipping task {task_id} — no changes since last run\033[0m")
            log_trace("heartbeat", "heartbeat", "Skipped — no context changes", task_id=task_id)
            return None

        conv_id = conv.id
        ext_conv_id = task.external_conversation_id
        parent_conv_id = task.parent_conversation_id

        # Set typing indicator on external conversation
        if ext_conv_id:
            ext_conv = db.query(Conversation).filter_by(id=ext_conv_id).first()
            if ext_conv:
                from sqlalchemy.orm.attributes import flag_modified
                extra = dict(ext_conv.extra or {})
                extra["ai_typing"] = True
                ext_conv.extra = extra
                flag_modified(ext_conv, "extra")

        # Build context + message history
        context = build_task_context(db, task_id)

        # Gather progress steps
        steps_text = ""
        if task.steps:
            steps_text = "\n\nTask progress steps:\n" + _hb_json.dumps(task.steps, indent=2)

        # Gather external conversation messages
        ext_msgs_text = ""
        if ext_conv_id:
            ext_conv_obj = db.query(Conversation).filter_by(id=ext_conv_id).first()
            if ext_conv_obj:
                ext_msgs = sorted(
                    [m for m in (ext_conv_obj.messages or [])
                     if m.message_type in (MessageType.MESSAGE, MessageType.THREAD)],
                    key=lambda m: m.sent_at,
                )[-20:]
                if ext_msgs:
                    lines = [f"[{m.sender_name or 'Unknown'}]: {m.body}" for m in ext_msgs]
                    ext_msgs_text = "\n\nExternal conversation (with vendor/tenant):\n" + "\n".join(lines)

        # Gather tenant conversation messages
        tenant_msgs_text = ""
        if task.parent_conversation_id and task.parent_conversation_id != ext_conv_id:
            parent_conv = db.query(Conversation).filter_by(id=task.parent_conversation_id).first()
            if parent_conv:
                t_msgs = sorted(
                    [m for m in (parent_conv.messages or [])
                     if m.message_type in (MessageType.MESSAGE, MessageType.THREAD)],
                    key=lambda m: m.sent_at,
                )[-20:]
                if t_msgs:
                    lines = [f"[{m.sender_name or 'Unknown'}]: {m.body}" for m in t_msgs]
                    tenant_msgs_text = "\n\nTenant conversation:\n" + "\n".join(lines)

        # Build AI conversation history
        all_msgs = [
            m for m in (conv.messages or [])
            if m.message_type in (MessageType.MESSAGE, MessageType.THREAD)
        ]
        msg_rows = sorted(all_msgs, key=lambda m: m.sent_at)[-20:]
        messages_payload = [{"role": "system", "content": context}]
        messages_payload += [
            {"role": "assistant" if m.is_ai else "user", "content": m.body or ""}
            for m in msg_rows
        ]

        # The hint (or default) is the "user" message that triggers the agent
        default_hint = "Check this task for anything that needs attention."
        user_msg = (hint or default_hint) + steps_text + ext_msgs_text + tenant_msgs_text
        messages_payload.append({"role": "user", "content": user_msg})

        db.commit()  # flush typing indicator + detach ORM objects

        # Run agent
        agent_id = agent_registry.ensure_agent(DEFAULT_USER_ID, db)
        session_key = f"task:{task_id}"

        # Run agent in a dedicated thread with its own event loop so we
        # don't conflict with the main uvloop (heartbeat_loop calls us from
        # within the running async loop).
        import concurrent.futures
        _agent_result = [None, None]  # [resp, pending]

        def _run_in_thread():
            _conv_token = active_conversation_id.set(conv_id)
            _pending_token = pending_suggestion_messages.set([])
            _loop = _hb_asyncio.new_event_loop()
            try:
                _agent_result[0] = _loop.run_until_complete(
                    call_agent(agent_id, session_key=session_key, messages=messages_payload)
                )
                _agent_result[1] = pending_suggestion_messages.get() or []
            finally:
                _loop.close()
                active_conversation_id.reset(_conv_token)
                pending_suggestion_messages.reset(_pending_token)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(_run_in_thread).result(timeout=300)

        resp = _agent_result[0]
        pending = _agent_result[1] or []
        if not resp:
            return None

        # Check if agent said no response needed
        if resp.reply.strip().startswith(NO_RESPONSE_SENTINEL):
            # Clear typing indicator
            if ext_conv_id:
                chat_service.clear_typing_indicator(db, ext_conv_id)
            _heartbeat_state[task_id] = current_hash
            log_trace("heartbeat", "heartbeat", f"No response needed for task {task_id}",
                      task_id=task_id)
            return None

        # Persist results
        write_db = SessionLocal.session_factory()
        try:
            now = datetime.now(UTC)

            # AI reply to AI conversation
            ai_msg = Message(
                id=str(_uuid.uuid4()),
                conversation_id=conv_id,
                sender_type=ParticipantType.ACCOUNT_USER,
                body=resp.reply,
                message_type=MessageType.MESSAGE,
                sender_name="RentMate",
                is_ai=True,
                sent_at=now,
            )
            write_db.add(ai_msg)

            # Side effects (suggestion messages)
            side_effects = resp.side_effects + [
                {"type": "suggestion_message", **p} for p in pending
            ]
            flushed = process_side_effects(write_db, side_effects=side_effects, conversation_id=conv_id, base_time=now)

            db_conv = write_db.query(Conversation).filter_by(id=conv_id).first()
            if db_conv:
                db_conv.updated_at = now
            write_db.commit()

            # Recompute hash after agent run so next heartbeat knows the state
            try:
                class _Ref:
                    pass
                _ref = _Ref()
                _ref.id, _ref.ai_conversation_id = task_id, conv_id
                _ref.external_conversation_id, _ref.parent_conversation_id = ext_conv_id, parent_conv_id
                _heartbeat_state[task_id] = _compute_heartbeat_hash(_ref)
            except Exception:
                pass

            log_trace("heartbeat", "heartbeat",
                      f"Agent replied ({len(resp.reply)} chars): {resp.reply[:100]}",
                      task_id=task_id, conversation_id=conv_id)

            return resp.reply
        except Exception as e:
            write_db.rollback()
            print(f"\033[31m[heartbeat] DB write failed for task {task_id}: {e}\033[0m")
            import traceback
            traceback.print_exc()
            return resp.reply
        finally:
            write_db.close()
            # Clear typing indicator
            if ext_conv_id:
                try:
                    chat_service.clear_typing_indicator(db, ext_conv_id)
                except Exception:
                    pass
    except Exception as e:
        print(f"\033[31m[heartbeat] Failed for task {task_id}: {e}\033[0m")
        import traceback
        traceback.print_exc()
        log_trace("error", "heartbeat", f"Heartbeat failed: {e}", task_id=task_id)
        return None
    finally:
        db.close()

ASSESS_PROMPT = (
    "Autonomous mode was just enabled for this task. "
    "Review the full conversation — including messages from external participants "
    "(vendors, tenants) shown below — and the task progress steps. "
    "If there is anything actionable: an unanswered question from a vendor or tenant, "
    "scheduling information to act on, a next step in the progress plan to advance, "
    "or information to provide — go ahead and respond normally. "
    "Use the task progress steps as your guide for what to do next. "
    "If the conversation is truly handled and nothing further is needed, "
    f"reply with exactly: {NO_RESPONSE_SENTINEL}"
)


@router.post("/chat/assess")
async def assess_task_endpoint(
    body: AssessRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Assess a task conversation and respond only if warranted.

    Called when autonomous mode is toggled on. The agent reviews the existing
    conversation and decides whether to respond. If no response is needed,
    nothing is persisted and the client receives ``reply: null``.
    """
    await require_user(request)
    from handlers.deps import SessionLocal
    _SL = SessionLocal.session_factory

    task_obj = db.query(Task).filter_by(id=body.task_id).first()
    if not task_obj:
        raise HTTPException(status_code=404, detail="Task not found")
    conv = task_obj.ai_conversation
    if not conv:
        raise HTTPException(status_code=404, detail="Task has no AI conversation")

    context = build_task_context(db, body.task_id)
    conv_id = conv.id
    ext_conv_id = task_obj.external_conversation_id

    # Set typing indicator on the external conversation so the vendor portal
    # can show it while the agent is thinking.
    if ext_conv_id:
        ext_conv = db.query(Conversation).filter_by(id=ext_conv_id).first()
        if ext_conv:
            from sqlalchemy.orm.attributes import flag_modified
            extra = dict(ext_conv.extra or {})
            extra["ai_typing"] = True
            ext_conv.extra = extra
            flag_modified(ext_conv, "extra")
    # Gather progress steps and external conversation for the assess prompt
    import json as _assess_json
    steps_text = ""
    if task_obj.steps:
        steps_text = "\n\nTask progress steps:\n" + _assess_json.dumps(task_obj.steps, indent=2)

    ext_msgs_text = ""
    if ext_conv_id:
        ext_conv_obj = db.query(Conversation).filter_by(id=ext_conv_id).first()
        if ext_conv_obj:
            ext_msgs = sorted(
                [m for m in (ext_conv_obj.messages or [])
                 if m.message_type in (MessageType.MESSAGE, MessageType.THREAD)],
                key=lambda m: m.sent_at,
            )[-20:]
            if ext_msgs:
                lines = []
                for m in ext_msgs:
                    sender = m.sender_name or ("AI" if m.is_ai else "Unknown")
                    lines.append(f"[{sender}]: {m.body}")
                ext_msgs_text = (
                    "\n\nExternal conversation (with vendor/tenant):\n"
                    + "\n".join(lines)
                )

    # Build message history BEFORE commit/session close — eagerly extract
    # all ORM data into plain dicts so the async generator doesn't need the session.
    all_msgs = [
        m for m in (conv.messages or [])
        if m.message_type in (MessageType.MESSAGE, MessageType.THREAD)
    ]
    msg_rows = sorted(all_msgs, key=lambda m: m.sent_at)[-20:]
    messages_payload = [{"role": "system", "content": context}]
    messages_payload += [
        {"role": "assistant" if m.is_ai else "user", "content": m.body or ""}
        for m in msg_rows
    ]

    db.commit()
    messages_payload.append({
        "role": "user",
        "content": ASSESS_PROMPT + steps_text + ext_msgs_text,
    })

    from backends.local_auth import resolve_account_id
    agent_id = agent_registry.ensure_agent(resolve_account_id(), db)
    session_key = f"task:{body.task_id}"
    stream_id = str(_uuid.uuid4())

    async def generate():
        queue: asyncio.Queue = asyncio.Queue()
        running = _RunningChat(task=None)
        running.subscribers.append(queue)
        _active_chats[stream_id] = running

        async def on_progress(text: str, tool_hint: str | None = None, **_kwargs):
            entry = f"[{tool_hint}] {text}" if tool_hint else text
            running.progress_log.append(entry)
            for sub in list(running.subscribers):
                await sub.put(entry)

        async def run_and_persist() -> tuple[str | None, str | None, list[dict]]:
            from llm.client import call_agent
            from llm.side_effects import process_side_effects
            from llm.tools import active_conversation_id
            token = active_conversation_id.set(conv_id)
            try:
                # NOTE: We do NOT persist a user message — the assess prompt is
                # an internal trigger, not a real user message.
                await on_progress("Thinking\u2026")
                agent_resp = await call_agent(agent_id, session_key=session_key, messages=messages_payload, on_progress=on_progress)

                # If agent says no response needed, skip persistence entirely
                if agent_resp.reply.strip().startswith(NO_RESPONSE_SENTINEL):
                    return None, None, []

                write_db = _SL()
                try:
                    now = datetime.now(UTC)
                    # Persist thinking traces to the AI conversation (internal)
                    trace_lines = [l for l in running.progress_log if l != "Thinking\u2026"]
                    if trace_lines:
                        write_db.add(Message(
                            id=str(_uuid.uuid4()),
                            conversation_id=conv_id,
                            sender_type=ParticipantType.ACCOUNT_USER,
                            body="\n".join(trace_lines),
                            message_type=MessageType.INTERNAL,
                            sender_name="RentMate",
                            is_ai=True,
                            sent_at=now,
                        ))
                    # Persist the reply to the AI conversation. External
                    # messages are sent through the message_person tool /
                    # suggestion flow, not directly from the assess endpoint.
                    ai_msg = Message(
                        id=str(_uuid.uuid4()),
                        conversation_id=conv_id,
                        sender_type=ParticipantType.ACCOUNT_USER,
                        body=agent_resp.reply,
                        message_type=MessageType.MESSAGE,
                        sender_name="RentMate",
                        is_ai=True,
                        sent_at=now,
                    )
                    write_db.add(ai_msg)
                    flushed_suggestions = process_side_effects(
                        write_db, side_effects=agent_resp.side_effects, conversation_id=conv_id, base_time=now,
                    )
                    db_conv = write_db.query(Conversation).filter_by(id=conv_id).first()
                    if db_conv:
                        db_conv.updated_at = now
                    write_db.commit()
                    print(f"[assess] Persisted AI reply ({len(agent_resp.reply)} chars) to AI conv {conv_id}")
                    from llm.tracing import log_trace as _lt2
                    _lt2("llm_reply", "assess", agent_resp.reply[:200],
                         task_id=body.task_id, conversation_id=conv_id)
                    return agent_resp.reply, ai_msg.id, flushed_suggestions
                except Exception as e:
                    write_db.rollback()
                    print(f"[assess] DB write failed: {e}")
                    import traceback
                    traceback.print_exc()
                    return agent_resp.reply, str(_uuid.uuid4()), []
                finally:
                    write_db.close()
            finally:
                active_conversation_id.reset(token)
                _active_chats.pop(stream_id, None)
                # Clear typing indicator on external conversation
                if ext_conv_id:
                    clear_db = _SL()
                    try:
                        chat_service.clear_typing_indicator(clear_db, ext_conv_id)
                    finally:
                        clear_db.close()

        running.task = asyncio.create_task(run_and_persist())

        try:
            yield f"data: {_json.dumps({'type': 'stream_id', 'stream_id': stream_id})}\n\n"

            while not running.task.done():
                try:
                    text = await asyncio.wait_for(queue.get(), timeout=0.1)
                    yield f"data: {_json.dumps({'type': 'progress', 'text': text})}\n\n"
                except asyncio.TimeoutError:
                    pass

            while not queue.empty():
                text = queue.get_nowait()
                yield f"data: {_json.dumps({'type': 'progress', 'text': text})}\n\n"

            try:
                reply, msg_id, suggestion_msgs = running.task.result()
            except Exception as exc:
                print(f"[assess] SSE task failed: {exc!r}")
                yield f"data: {_json.dumps({'type': 'error', 'message': 'AI unavailable'})}\n\n"
                return

            done_payload: dict = {'type': 'done', 'reply': reply, 'message_id': msg_id, 'conversation_id': conv_id}
            if suggestion_msgs:
                done_payload['suggestion_messages'] = suggestion_msgs
            yield f"data: {_json.dumps(done_payload)}\n\n"
        finally:
            if queue in running.subscribers:
                running.subscribers.remove(queue)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/chat/task-context/{task_id}")
async def get_task_context(
    task_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Return the full context string the agent receives for a task."""
    await require_user(request)
    return {"context": build_task_context(db, task_id)}


@router.get("/chat/conversations")
async def list_chat_conversations(
    request: Request,
    db: Session = Depends(get_db),
):
    await require_user(request)
    convs = chat_service.list_conversations(db, "user_ai", limit=50)
    return [
        {
            "id": c.id,
            "title": c.subject or "Chat with RentMate",
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            "last_message": c.messages[-1].body[:100] if c.messages else None,
        }
        for c in convs
    ]


@router.post("/chat/new")
async def create_new_chat(
    request: Request,
    db: Session = Depends(get_db),
):
    await require_user(request)
    from db.lib import get_or_create_user_ai_conversation
    conv = get_or_create_user_ai_conversation(db, account_id="default", user_id="default", session_key=None)
    db.commit()
    return {"id": conv.id, "title": conv.subject}


class SpawnTaskRequest(BaseModel):
    parent_conversation_id: str
    objective: str
    category: Optional[str] = None
    urgency: Optional[str] = None
    priority: Optional[str] = None
    task_mode: str = "autonomous"
    source: str = TaskSource.MANUAL


@router.post("/chat/task/spawn")
async def spawn_task_endpoint(
    body: SpawnTaskRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    await require_user(request)
    from db.lib import spawn_task_from_conversation
    task = spawn_task_from_conversation(
        db,
        parent_conversation_id=body.parent_conversation_id,
        objective=body.objective,
        category=body.category,
        urgency=body.urgency,
        priority=body.priority,
        task_mode=body.task_mode,
        source=body.source,
    )
    db.commit()
    return {
        "uid": task.id,
        "title": task.title,
        "parent_conversation_id": task.parent_conversation_id,
        "task_status": task.task_status,
        "task_mode": task.task_mode,
        "category": task.category,
        "urgency": task.urgency,
    }


@router.get("/chat/stream/{stream_id}")
async def chat_stream_reconnect(stream_id: str, request: Request):
    """Reconnect to an in-flight chat and receive its remaining SSE events.

    Returns an SSE stream.  If the chat is not currently running the first event
    is ``{"type": "idle"}`` and the stream closes immediately.
    """
    await require_user(request)

    running = _active_chats.get(stream_id)

    if not running:
        async def idle():
            yield f"data: {_json.dumps({'type': 'idle'})}\n\n"
        return StreamingResponse(
            idle(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    sub: asyncio.Queue = asyncio.Queue()
    running.subscribers.append(sub)
    buffered = list(running.progress_log)

    async def generate():
        try:
            for text in buffered:
                yield f"data: {_json.dumps({'type': 'progress', 'text': text})}\n\n"

            while not running.task.done():
                try:
                    text = await asyncio.wait_for(sub.get(), timeout=0.1)
                    yield f"data: {_json.dumps({'type': 'progress', 'text': text})}\n\n"
                except asyncio.TimeoutError:
                    pass

            while not sub.empty():
                text = sub.get_nowait()
                yield f"data: {_json.dumps({'type': 'progress', 'text': text})}\n\n"

            try:
                reply, msg_id = running.task.result()
                done_payload: dict = {'type': 'done', 'reply': reply, 'message_id': msg_id}
                if actions:
                    done_payload['actions'] = actions
                yield f"data: {_json.dumps(done_payload)}\n\n"
            except Exception:
                yield f"data: {_json.dumps({'type': 'error', 'message': 'AI unavailable'})}\n\n"
        finally:
            if sub in running.subscribers:
                running.subscribers.remove(sub)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
