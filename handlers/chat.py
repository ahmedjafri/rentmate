import asyncio
import json as _json
import os
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from db.lib import get_conversation_with_messages, record_sms_from_dialpad, route_inbound_to_task
from db.models import Conversation, Message, ParticipantType
from handlers.deps import get_db, require_user
from llm.context import build_task_context, load_account_context
from llm.registry import agent_registry, DATA_DIR

router = APIRouter()

# ─── In-flight task registry ──────────────────────────────────────────────────
# Tracks agent tasks that are still running so reconnecting clients can pick up
# the live progress stream.  Keyed by task_id (conversation UUID).

@dataclass
class _RunningTask:
    task: asyncio.Task
    subscribers: list = field(default_factory=list)   # list[asyncio.Queue]
    progress_log: list = field(default_factory=list)   # list[str] — replay buffer

_active_tasks: Dict[str, _RunningTask] = {}

DIALPAD_API_KEY = os.getenv("DIALPAD_API_KEY", "")
PHONE_WHITELIST = [p.strip() for p in os.getenv("PHONE_WHITELIST", "").split(",") if p.strip()]


def is_in_whitelist(number: str) -> bool:
    return any(allowed in number for allowed in PHONE_WHITELIST)


def _read_and_clear_actions(task_id: str) -> list:
    """Read and remove pending actions for a specific task from the shared queue file."""
    from backends.local_auth import DEFAULT_USER_ID
    actions_file = DATA_DIR / DEFAULT_USER_ID / "pending_actions.jsonl"
    if not actions_file.exists():
        return []
    matched, remaining_lines = [], []
    for line in actions_file.read_text().strip().splitlines():
        try:
            action = _json.loads(line)
            if action.get("task_id") == task_id:
                matched.append(action)
            else:
                remaining_lines.append(line)
        except Exception:
            pass
    if remaining_lines:
        actions_file.write_text("\n".join(remaining_lines) + "\n")
    else:
        actions_file.unlink(missing_ok=True)
    return matched


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
    conversation_history: List[Dict[str, Any]] = Field(default_factory=list)


class TaskChatRequest(BaseModel):
    task_id: str
    message: str


class SuggestReplyRequest(BaseModel):
    conversationHistory: List[Dict[str, Any]] = Field(default_factory=list)
    headerTitle: str
    headerDescription: str


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
        route_inbound_to_task,
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
    full_conv = get_conversation_with_messages(db=db, conversation_id=conv.id)
    HISTORY_LIMIT = 20
    recent_msgs = sorted(full_conv.messages, key=lambda m: m.sent_at)
    # Exclude the message we just added (last one) for history, then take last 20
    history_msgs = recent_msgs[:-1][-HISTORY_LIMIT:]
    messages = [{"role": "system", "content": context}]
    for m in history_msgs:
        role = "assistant" if m.is_ai else "user"
        messages.append({"role": role, "content": m.body or ""})
    messages.append({"role": "user", "content": body})

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
        message_type="message",
        sender_name="RentMate",
        is_ai=True,
        sent_at=datetime.utcnow(),
    )
    db.add(ai_msg)
    db.commit()

    await send_via_channel(conv, response_text, inbound_meta=sender_meta)

    return {"status": "ok"}


@router.post("/suggest-reply")
async def suggest_reply(
    request: SuggestReplyRequest,
    db: Session = Depends(get_db),
):
    from backends.local_auth import DEFAULT_USER_ID
    context = (
        f"You are the property manager in these conversations.\n"
        f"Maintenance Request Context: {request.headerTitle}, {request.headerDescription}"
    )
    # Legacy format: [{sender, text}] → convert to [{role, content}]
    history = [
        {"role": "user", "content": f"{m.get('sender', '')}: {m.get('text', '')}"}
        for m in request.conversationHistory
    ]
    messages = [{"role": "system", "content": context}] + history
    agent_id = agent_registry.ensure_agent(DEFAULT_USER_ID, db)
    session_key = f"suggest:{str(_uuid.uuid4())}"
    try:
        response = await chat_with_agent(agent_id, session_key, messages)
        return {"suggestion": response}
    except Exception as e:
        print(f"Error processing suggestion request: {e}")
        return {"error": "Failed to generate suggestion"}, 500


@router.post("/chat")
async def chat_endpoint(
    body: ChatRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    await require_user(request)

    context = load_account_context(db)

    # Build history: prior messages + current message at the end
    history = [
        {"role": m.get("role", "user"), "content": m.get("content", m.get("text", ""))}
        for m in body.conversation_history
    ]
    history.append({"role": "user", "content": body.message})

    from backends.local_auth import DEFAULT_USER_ID
    agent_id = agent_registry.ensure_agent(DEFAULT_USER_ID, db)
    conv_id = body.conversation_id or str(_uuid.uuid4())
    session_key = f"chat:{conv_id}"
    messages = [{"role": "system", "content": context}] + history
    try:
        reply = await chat_with_agent(agent_id, session_key, messages)
    except Exception as e:
        print(f"Chat agent error: {e}")
        raise HTTPException(status_code=502, detail="AI unavailable")

    return {"reply": reply, "conversation_id": conv_id}


@router.post("/chat/task")
async def task_chat_endpoint(
    body: TaskChatRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    await require_user(request)

    task = db.query(Conversation).filter_by(id=body.task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    context = build_task_context(db, body.task_id)
    msg_rows = sorted(
        [m for m in task.messages if m.message_type == "message"],
        key=lambda m: m.sent_at,
    )[-20:]
    history = [
        {"role": "assistant" if m.is_ai else "user", "content": m.body or ""}
        for m in msg_rows
    ]
    history.append({"role": "user", "content": body.message})

    from backends.local_auth import DEFAULT_USER_ID
    from handlers.deps import SessionLocal as _SessionLocal
    agent_id = agent_registry.ensure_agent(DEFAULT_USER_ID, db)
    session_key = f"task:{body.task_id}"
    messages_payload = [{"role": "system", "content": context}] + history
    task_id = body.task_id

    async def generate():
        queue: asyncio.Queue = asyncio.Queue()

        # Register in the active-task registry before starting so reconnects work
        # immediately.  Subscribe our queue first, then snapshot progress_log so
        # there are no gaps (asyncio is single-threaded; no await between these).
        running = _RunningTask(task=None)  # task set below
        running.subscribers.append(queue)
        _active_tasks[task_id] = running

        async def on_progress(text: str):
            running.progress_log.append(text)
            for sub in list(running.subscribers):
                await sub.put(text)

        async def run_and_persist() -> tuple[str, str]:
            """Run agent and persist result to DB.

            Runs as an independent asyncio task so the DB write completes even
            if the SSE generator is cancelled (client navigates away).
            """
            try:
                reply = await chat_with_agent(agent_id, session_key, messages_payload, on_progress)

                write_db = _SessionLocal()
                try:
                    now = datetime.utcnow()
                    # Persist thinking chain as an internal message before the reply
                    if running.progress_log:
                        thinking_body = "\n".join(running.progress_log)
                        write_db.add(Message(
                            id=str(_uuid.uuid4()),
                            conversation_id=task_id,
                            sender_type=ParticipantType.ACCOUNT_USER,
                            body=thinking_body,
                            message_type="internal",
                            sender_name="RentMate",
                            is_ai=True,
                            sent_at=now,
                        ))
                    ai_msg = Message(
                        id=str(_uuid.uuid4()),
                        conversation_id=task_id,
                        sender_type=ParticipantType.ACCOUNT_USER,
                        body=reply,
                        message_type="message",
                        sender_name="RentMate",
                        is_ai=True,
                        sent_at=now,
                    )
                    write_db.add(ai_msg)
                    conv = write_db.query(Conversation).filter_by(id=task_id).first()
                    if conv:
                        conv.updated_at = now
                    write_db.commit()
                    actions = _read_and_clear_actions(task_id)
                    return reply, ai_msg.id, actions
                except Exception as e:
                    write_db.rollback()
                    print(f"[task-chat] DB write failed: {e}")
                    return reply, str(_uuid.uuid4()), []
                finally:
                    write_db.close()
            finally:
                _active_tasks.pop(task_id, None)

        running.task = asyncio.create_task(run_and_persist())

        try:
            # Stream progress while the agent runs
            while not running.task.done():
                try:
                    text = await asyncio.wait_for(queue.get(), timeout=0.1)
                    yield f"data: {_json.dumps({'type': 'progress', 'text': text})}\n\n"
                except asyncio.TimeoutError:
                    pass

            # Drain remaining progress events
            while not queue.empty():
                text = queue.get_nowait()
                yield f"data: {_json.dumps({'type': 'progress', 'text': text})}\n\n"

            try:
                reply, msg_id, actions = running.task.result()
            except Exception:
                yield f"data: {_json.dumps({'type': 'error', 'message': 'AI unavailable'})}\n\n"
                return

            yield f"data: {_json.dumps({'type': 'done', 'reply': reply, 'message_id': msg_id, 'actions': actions})}\n\n"
        finally:
            if queue in running.subscribers:
                running.subscribers.remove(queue)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/chat/task/{task_id}/stream")
async def task_stream_reconnect(task_id: str, request: Request):
    """Reconnect to an in-flight agent task and receive its remaining SSE events.

    Returns an SSE stream.  If the task is not currently running the first event
    is ``{"type": "idle"}`` and the stream closes immediately.
    """
    await require_user(request)

    running = _active_tasks.get(task_id)

    if not running:
        async def idle():
            yield f"data: {_json.dumps({'type': 'idle'})}\n\n"
        return StreamingResponse(
            idle(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    sub: asyncio.Queue = asyncio.Queue()
    # Subscribe before snapshotting the buffer so there are no gaps.
    running.subscribers.append(sub)
    buffered = list(running.progress_log)

    async def generate():
        try:
            # Replay everything the client missed
            for text in buffered:
                yield f"data: {_json.dumps({'type': 'progress', 'text': text})}\n\n"

            # Stream live progress until the task finishes
            while not running.task.done():
                try:
                    text = await asyncio.wait_for(sub.get(), timeout=0.1)
                    yield f"data: {_json.dumps({'type': 'progress', 'text': text})}\n\n"
                except asyncio.TimeoutError:
                    pass

            # Drain any final events that arrived just as the task completed
            while not sub.empty():
                text = sub.get_nowait()
                yield f"data: {_json.dumps({'type': 'progress', 'text': text})}\n\n"

            try:
                reply, msg_id, actions = running.task.result()
                yield f"data: {_json.dumps({'type': 'done', 'reply': reply, 'message_id': msg_id, 'actions': actions})}\n\n"
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
