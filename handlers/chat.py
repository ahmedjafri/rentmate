import asyncio
import json as _json
import os
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
from db.lib import get_conversation_with_messages, record_sms_from_dialpad, route_inbound_to_tenant_chat
from db.models import Conversation, Message, MessageType, ParticipantType, Task
from handlers.deps import get_db, require_user
from llm.context import build_task_context, load_account_context
from llm.registry import agent_registry, DATA_DIR
from gql.services import chat_service

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

DIALPAD_API_KEY = os.getenv("DIALPAD_API_KEY", "")
PHONE_WHITELIST = [p.strip() for p in os.getenv("PHONE_WHITELIST", "").split(",") if p.strip()]


def is_in_whitelist(number: str) -> bool:
    return any(allowed in number for allowed in PHONE_WHITELIST)




async def chat_with_agent(
    agent_id: str,
    session_key: str,
    messages: list[dict],
    on_progress: Optional[Callable] = None,
) -> str:
    loop = agent_registry.get_loop()
    if loop is None:
        raise RuntimeError("NanoBot agent not ready")

    # Most recent user message
    user_content = next(
        (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
    )
    # Inject system/task context when present
    sys_content = next((m["content"] for m in messages if m.get("role") == "system"), None)
    if sys_content:
        user_content = f"<context>\n{sys_content}\n</context>\n\n{user_content}"

    return await loop.process_direct(
        content=user_content,
        session_key=session_key,
        channel="rentmate",
        chat_id=session_key,
        on_progress=on_progress,
    )


async def send_sms_reply(from_num: str, to_num: str, text: str):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://dialpad.com/api/v2/sms?apikey={DIALPAD_API_KEY}",
            headers={"accept": "application/json", "content-type": "application/json"},
            json={
                "infer_country_code": False,
                "channel_hashtag": None,
                "from_number": from_num,
                "media": None,
                "sender_group_id": None,
                "sender_group_type": None,
                "text": text,
                "to_numbers": [to_num],
                "user_id": None,
            },
        )
        print(response.text)


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


# ─── Routes ───────────────────────────────────────────────────────────────────

@router.post("/dialpad-webhook")
async def handle_message(
    request: Request,
    db: Session = Depends(get_db),
):
    data = await request.json()

    from_number = data["from_number"]
    if len(data["to_number"]) > 1:
        print(
            "[Dialpad] More than 1 recipient (skipping), "
            f"nums={data['to_number']}, msg={data.get('text', '')!r}")
        return {"status": "ok"}

    to_number: str = data["to_number"][0]
    body = data["text"]

    # Resolve tenant + direction
    from backends.wire import sms_router
    resolved = sms_router.resolve(db, from_number, to_number)
    if not resolved:
        print(f"[Dialpad] Tenant not resolved for from={from_number} to={to_number}")
        return {"status": "ok"}

    _account_id, tenant, direction = resolved

    if direction != "inbound":
        return {"status": "ok"}

    if not is_in_whitelist(from_number):
        print(
            "[Dialpad] Number not in whitelist (skipping response), "
            f"num={from_number}, msg={body!r}")
        # Still record the message but don't trigger agent
        record_sms_from_dialpad(db=db, from_number=from_number, to_number=to_number, body=body)
        return {"status": "ok"}

    sender_meta = {
        "source": "dialpad",
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

    # Build agent context and history
    from llm.context import build_task_context
    context = build_task_context(db, conv.id)
    messages = chat_service.build_agent_message_history(db, conv.id, body, context, exclude_last=True)

    from backends.local_auth import DEFAULT_USER_ID
    agent_id = agent_registry.ensure_agent(DEFAULT_USER_ID, db)
    session_key = f"sms:{conv.id}"

    response_text = await chat_with_agent(agent_id, session_key, messages)

    # Persist AI reply
    from db.lib import add_message as _add_message
    from db.models import ParticipantType as _PT
    import uuid as _uuid2
    ai_msg = Message(
        id=str(_uuid2.uuid4()),
        conversation_id=conv.id,
        sender_type=_PT.ACCOUNT_USER,
        body=response_text,
        message_type=MessageType.MESSAGE,
        sender_name="RentMate",
        is_ai=True,
        sent_at=datetime.now(UTC),
    )
    db.add(ai_msg)
    db.commit()

    await send_via_channel(conv, response_text, inbound_meta=sender_meta)

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
    from backends.local_auth import DEFAULT_USER_ID
    from handlers.deps import SessionLocal as _SL

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
            if m.message_type in (MessageType.MESSAGE, MessageType.THREAD)
        ]
        msg_rows = sorted(all_msgs, key=lambda m: m.sent_at)[-20:]
        messages_payload = [{"role": "system", "content": context}]
        messages_payload += [
            {"role": "assistant" if m.is_ai else "user", "content": m.body or ""}
            for m in msg_rows
        ]
        messages_payload.append({"role": "user", "content": body.message})
    else:
        messages_payload = chat_service.build_agent_message_history(db, conv_id, body.message, context)

    agent_id = agent_registry.ensure_agent(DEFAULT_USER_ID, db)
    session_key = f"task:{body.task_id}" if body.task_id else f"chat:{conv_id}"
    stream_id = str(_uuid.uuid4())
    user_message = body.message

    # ── SSE generator ─────────────────────────────────────────────────────
    async def generate():
        queue: asyncio.Queue = asyncio.Queue()

        running = _RunningChat(task=None)
        running.subscribers.append(queue)
        _active_chats[stream_id] = running

        async def on_progress(text: str):
            running.progress_log.append(text)
            for sub in list(running.subscribers):
                await sub.put(text)

        async def run_and_persist() -> tuple[str, str]:
            from llm.tools import active_conversation_id
            token = active_conversation_id.set(conv_id)
            try:
                await on_progress("Thinking\u2026")
                reply = await chat_with_agent(agent_id, session_key, messages_payload, on_progress)

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
                        body=reply,
                        message_type=MessageType.THREAD,
                        sender_name="RentMate",
                        is_ai=True,
                        sent_at=now,
                    )
                    write_db.add(ai_msg)
                    # Persist user message
                    write_db.add(Message(
                        id=str(_uuid.uuid4()),
                        conversation_id=conv_id,
                        sender_type=ParticipantType.ACCOUNT_USER,
                        body=user_message,
                        message_type=MessageType.MESSAGE,
                        sender_name="You",
                        is_ai=False,
                        sent_at=now,
                    ))
                    db_conv = write_db.query(Conversation).filter_by(id=conv_id).first()
                    if db_conv:
                        db_conv.updated_at = now
                    write_db.commit()
                    return reply, ai_msg.id
                except Exception as e:
                    write_db.rollback()
                    print(f"[chat] DB write failed: {e}")
                    return reply, str(_uuid.uuid4())
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
                reply, msg_id = running.task.result()
            except Exception:
                yield f"data: {_json.dumps({'type': 'error', 'message': 'AI unavailable'})}\n\n"
                return

            done_payload: dict = {'type': 'done', 'reply': reply, 'message_id': msg_id, 'conversation_id': conv_id}
            yield f"data: {_json.dumps(done_payload)}\n\n"
        finally:
            if queue in running.subscribers:
                running.subscribers.remove(queue)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
