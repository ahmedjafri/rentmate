import asyncio
import concurrent.futures
import hashlib
import json
import os
import re
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Dict, Optional

from handlers.email_parser import ParsedEmail

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from litellm.litellm_core_utils.logging_worker import GLOBAL_LOGGING_WORKER
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from agent import client as agent_client
from agent.context import (
    build_task_context,
    build_task_context_data,
    load_account_context_data,
)
from agent.registry import agent_registry
from agent.side_effects import process_side_effects
from agent.tools import active_conversation_id, pending_suggestion_messages
from agent.tracing import log_trace, make_trace_envelope
from db.enums import SuggestionStatus, TaskSource
from db.lib import (
    get_or_create_user_ai_conversation,
    record_sms_from_quo,
    route_inbound_to_tenant_chat,
    spawn_task_from_conversation,
)
from db.models import (
    Conversation,
    ConversationParticipant,
    ConversationType,
    Document,
    Message,
    MessageType,
    ParticipantType,
    Property,
    Suggestion,
    Task,
    Tenant,
)
from db.session import SessionLocal
from handlers.deps import get_db, require_user
from integrations.local_auth import reset_request_context, resolve_account_id, set_request_context
from integrations.wire import sms_router
from services import chat_service, settings_service
from services.notification_service import NotificationRequest, NotificationService

router = APIRouter()


def _hosted_mode() -> bool:
    return os.getenv("HOSTED_MODE", "").lower() in {"1", "true", "yes"}


def _split_messages_for_trace(messages_payload: list[dict]) -> dict:
    if not messages_payload:
        return {"system": None, "history": [], "latest_user": None}
    system = messages_payload[0] if messages_payload and messages_payload[0].get("role") == "system" else None
    latest_user = None
    for message in reversed(messages_payload):
        if message.get("role") == "user":
            latest_user = message
            break
    history = messages_payload[1:] if system else messages_payload[:]
    return {
        "system": system,
        "history": history[:-1] if latest_user and history and history[-1] is latest_user else history,
        "latest_user": latest_user,
    }


def _build_llm_trace_detail(
    *,
    flow: str,
    session_key: str,
    messages_payload: list[dict],
    context_data: dict | None,
    task_id: str | None,
    conversation_id: str | None,
    reply: str | None = None,
    side_effects: list[dict] | None = None,
) -> dict:
    parts = _split_messages_for_trace(messages_payload)
    reasoning = {
        "available": False,
        "note": "No provider reasoning trace available for this response.",
    }
    return make_trace_envelope(
        "llm_exchange" if reply is not None else "llm_request",
        flow=flow,
        session_key=session_key,
        task_id=task_id,
        conversation_id=conversation_id,
        messages_payload=messages_payload,
        messages_breakdown=parts,
        context=context_data,
        retrieval=(context_data or {}).get("retrieval"),
        reply=reply,
        side_effects=side_effects or [],
        reasoning=reasoning,
    )


def _describe_agent_error(exc: Exception) -> str:
    """Turn an agent exception into a user-facing error message with actionable detail."""
    msg = str(exc).lower()

    if "connection" in msg or "connect" in msg:
        # Extract endpoint from the error when possible
        raw = str(exc)
        url_hint = ""
        if "http" in raw:
            m = re.search(r"(https?://[^\s'\"]+)", raw)
            if m:
                url_hint = f" ({m.group(1)})"
        return (
            f"Could not reach the AI model endpoint{url_hint}. "
            "Check that LLM_BASE_URL is correct and the server is running."
        )

    if "auth" in msg or "401" in msg or "api key" in msg or "api_key" in msg:
        return (
            "The AI model rejected the API key. "
            "Check that LLM_API_KEY is set correctly in Settings > AI Model."
        )

    if "rate" in msg and "limit" in msg or "429" in msg:
        return "The AI model is rate-limiting requests. Please wait a moment and try again."

    if "timeout" in msg:
        return "The AI model took too long to respond. Please try again."

    if "model" in msg and ("not exist" in msg or "not found" in msg or "does not exist" in msg or "invalid" in msg):
        return (
            "The AI provider does not recognize the model name. "
            "Check that LLM_MODEL is valid for your provider in Settings > AI Model."
        )

    if "404" in msg or "not found" in msg:
        return (
            "The AI model endpoint returned 404. "
            "Check that LLM_MODEL is a valid model name for your provider."
        )

    # Generic fallback — include the first 200 chars of the real error
    detail = str(exc)[:200]
    return f"AI model error: {detail}"


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

# ─── Pydantic models ──────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    task_id: Optional[int | str] = None

class AssessRequest(BaseModel):
    task_id: str

# ─── Routes ───────────────────────────────────────────────────────────────────

async def process_inbound_sms(db: Session, from_number: str, to_number: str, body: str):
    """Core inbound SMS handler shared by webhook and poller.

    Returns True if the message was processed, False if skipped.
    """
    resolved = sms_router.resolve(db, from_number=from_number, to_number=to_number)
    if not resolved:
        print(f"[sms] Sender not resolved for from={from_number} to={to_number}")
        return False

    _creator_id, entity, direction, entity_type = resolved

    # Set account context so entity creation resolves creator_id correctly
    set_request_context(account_id=_creator_id, org_id=getattr(entity, "org_id", None))

    if direction != "inbound":
        return False

    # ── Vendor inbound SMS ────────────────────────────────────────────
    if entity_type == "vendor":

        vendor = entity
        conv = chat_service.get_or_create_external_conversation(
            db,
            conversation_type=ConversationType.VENDOR,
            subject=f"SMS with {vendor.name}",
            vendor_id=vendor.id,
        )
        participant = db.query(ConversationParticipant).filter_by(
            conversation_id=conv.id,
            user_id=vendor.id,
            participant_type=ParticipantType.EXTERNAL_CONTACT,
        ).first()
        now = datetime.now(UTC)
        db.add(Message(
            conversation_id=conv.id,
            sender_type=ParticipantType.EXTERNAL_CONTACT,
            sender_id=participant.id if participant else None,
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

    context = build_task_context(db, conv.id, query=body)
    messages = chat_service.build_agent_message_history(db, conv_id=conv.id, user_message=body, context=context, exclude_last=True)

    agent_id = agent_registry.ensure_agent(resolve_account_id(), db)
    session_key = f"sms:{conv.id}"

    agent_resp = await agent_client.call_agent(agent_id, session_key=session_key, messages=messages)

    now = datetime.now(UTC)
    sent_directly, _ = chat_service.persist_policy_gated_tenant_reply(
        db,
        conversation_id=conv.id,
        tenant=tenant,
        reply=agent_resp.reply,
        side_effects=agent_resp.side_effects,
        risk_level="medium",
        sent_at=now,
    )
    db.commit()

    if sent_directly and tenant and tenant.user_id:
        await NotificationService.notify(
            db,
            NotificationRequest(
                recipient_user_id=tenant.user_id,
                conversation_id=conv.id,
                title="RentMate replied",
                messages=[agent_resp.reply],
                kind="conversation_update",
                task_id=conv.parent_task_id,
            ),
        )
    return True

async def process_inbound_email(db: Session, parsed: "ParsedEmail", *, auto_spawn_task: bool = True) -> bool:
    """Store an inbound email and trigger the agent if a tenant was resolved.

    This is the email equivalent of process_inbound_sms() above.  It bridges
    the async webhook handler (handlers/email_inbound.py) and the sync DB
    layer (services/email_service.py:ingest_email).

    The agent receives a hint that tells it an email arrived.  It then reads
    the full email body stored in the MIRRORED_CHAT conversation, classifies
    the request (maintenance issue, rent payment question, lease query, general
    question, or new potential tenant), and handles it autonomously using the
    same task tools and vendor/tenant context it already has.

    Returns True if the email was ingested, False if it was a duplicate or
    the incoming message was otherwise skipped.
    """
    from services.email_service import ingest_email, resolve_email_sender
    from db.models.account import User

    # Determine the correct account context (creator_id, org_id) before ingesting.
    # For known tenants, tenant.creator_id is the property manager who owns that tenant.
    # For unknown senders, fall back to the first account user in the DB so that
    # created conversations are visible to the logged-in property manager.
    # This mirrors the SMS path where sms_router.resolve() returns creator_id directly.
    def _resolve_account_context():
        entity, entity_type = resolve_email_sender(
            db, email=parsed.from_email, display_name=parsed.from_name
        )
        if entity is not None:
            c_id = getattr(entity, "creator_id", None)
            o_id = getattr(entity, "org_id", None)
            if c_id and o_id:
                return c_id, o_id
        # Unknown sender or entity missing creator context — use the primary account user.
        account_user = (
            db.query(User)
            .filter(User.user_type == "account")
            .order_by(User.id)
            .first()
        )
        if account_user:
            return account_user.id, account_user.org_id
        return 1, 1  # absolute fallback for empty databases

    creator_id, org_id = await asyncio.to_thread(_resolve_account_context)
    set_request_context(account_id=creator_id, org_id=org_id)

    # Run the sync DB work in a thread so we don't block the async event loop
    # (same pattern as route_inbound_to_tenant_chat call in process_inbound_sms).
    conv, msg, task_spawned = await asyncio.to_thread(
        ingest_email, db, parsed=parsed, auto_spawn_task=auto_spawn_task
    )

    if conv is None:
        # Duplicate or fully unresolvable — nothing to do.
        return False

    db.commit()

    if task_spawned and conv.parent_task_id:
        # Fire the agent autoreply in the background so the webhook returns
        # immediately while the LLM does its work.  The hint tells the agent
        # what channel triggered it so it can tailor its classification prompt.
        hint = (
            f"New email received from {parsed.from_email}: {parsed.subject}. "
            "Read the mirrored email conversation, classify the request "
            "(maintenance, rent payment, lease, general question, or new tenant inquiry), "
            "and handle it appropriately using the available task tools."
        )
        asyncio.create_task(
            asyncio.to_thread(agent_task_autoreply, str(conv.parent_task_id), hint)
        )

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
    _SL = SessionLocal.session_factory

    # ── Resolve conversation + context ────────────────────────────────────
    task_obj: Task | None = None
    _is_onboarding_start = False
    if body.task_id:
        task_obj = db.query(Task).filter_by(id=body.task_id).first()
        if not task_obj:
            raise HTTPException(status_code=404, detail="Task not found")
        conv = task_obj.ai_conversation
        if not conv:
            raise HTTPException(status_code=404, detail="Task has no AI conversation")
        context_data = build_task_context_data(db, body.task_id, query=body.message)
    else:
        context_data = load_account_context_data(db, query=body.message)
        # ── Onboarding start detection (must happen before conversation
        #    lookup so we can tag the conversation with the right subject) ──
        _is_onboarding_start = body.message.strip() == "[onboarding:start]"
        if body.conversation_id:
            conv = chat_service.get_or_create_conversation(db, uid=body.conversation_id)
        else:
            session_key = "Onboarding" if _is_onboarding_start else None
            conv = get_or_create_user_ai_conversation(db, creator_id="default", user_id="default", session_key=session_key)
        if _is_onboarding_start:
            # If the onboarding conversation already has messages, the AI has
            # already greeted the user — just return the conversation so the
            # frontend can load the existing history.
            conv_id = conv.id
            public_conv_id = str(getattr(conv, "external_id", None) or conv_id)
            if conv.messages:
                db.commit()

                async def _resume_onboarding():
                    yield f"data: {json.dumps({'type': 'done', 'reply': '', 'message_id': '', 'conversation_id': public_conv_id})}\n\n"

                return StreamingResponse(
                    _resume_onboarding(),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )
            body.message = (
                "The user just opened the app for the first time. "
                "Send a warm welcome and present the onboarding options."
            )
    context = context_data["text"]

    conv_id = conv.id
    public_conv_id = str(getattr(conv, "external_id", None) or conv_id)
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
            yield f"data: {json.dumps({'type': 'done', 'reply': None, 'conversation_id': public_conv_id})}\n\n"

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
        messages_payload = [{
            "role": "system",
            "content": chat_service.build_agent_system_context(conversation=conv, context=context),
        }]
        messages_payload += chat_service.model_history_messages(msg_rows)
        messages_payload.append({"role": "user", "content": body.message})
    else:
        messages_payload = chat_service.build_agent_message_history(db, conv_id=conv_id, user_message=body.message, context=context)

    # ── Guard: LLM must be configured ───────────────────────────────────
    if not settings_service.is_llm_configured():
        if _hosted_mode():
            no_llm_reply = (
                "AI is currently unavailable for this hosted workspace. "
                "Model configuration is managed globally, so there is nothing you need to set in Settings."
            )
        else:
            no_llm_reply = (
                "I'm not connected to an AI model yet, so I can't respond. "
                "Head to **Settings → AI Model** to add your API key and choose a model."
            )

        async def _no_llm():
            yield f"data: {json.dumps({'type': 'done', 'reply': no_llm_reply, 'conversation_id': public_conv_id})}\n\n"

        return StreamingResponse(
            _no_llm(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    agent_id = agent_registry.ensure_agent(resolve_account_id(), db)
    session_key = f"task:{body.task_id}" if body.task_id else f"chat:{conv_id}"
    stream_id = str(uuid.uuid4())
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
            token = active_conversation_id.set(conv_id)
            try:
                # Persist user message BEFORE running the agent so it gets an
                # earlier timestamp than any messages the agent creates.
                # Skip for onboarding start — the synthetic prompt isn't a real user message.
                if not _is_onboarding_start:
                    pre_db = _SL()
                    try:
                        pre_db.add(Message(
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
                log_trace(
                    "llm_request",
                    "chat",
                    f"Prepared {len(messages_payload)} messages for model call",
                    detail=_build_llm_trace_detail(
                        flow="chat",
                        session_key=session_key,
                        messages_payload=messages_payload,
                        context_data=context_data,
                        task_id=body.task_id,
                        conversation_id=conv_id,
                    ),
                )
                agent_resp = await agent_client.call_agent(
                    agent_id,
                    session_key=session_key,
                    messages=messages_payload,
                    on_progress=on_progress,
                    trace_context=_build_llm_trace_detail(
                        flow="chat",
                        session_key=session_key,
                        messages_payload=messages_payload,
                        context_data=context_data,
                        task_id=body.task_id,
                        conversation_id=conv_id,
                    ),
                )

                write_db = _SL()
                try:
                    now = datetime.now(UTC)
                    # Persist thinking chain as an internal message (only if
                    # there are actual tool-call traces beyond "Thinking…")
                    trace_lines = [l for l in running.progress_log if l != "Thinking\u2026"]
                    if trace_lines:
                        write_db.add(Message(
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
                        conversation_id=conv_id,
                        sender_type=ParticipantType.ACCOUNT_USER,
                        body=agent_resp.reply,
                        message_type=MessageType.MESSAGE,
                        sender_name="RentMate",
                        is_ai=True,
                        sent_at=now,
                    )
                    write_db.add(ai_msg)
                    # Materialize side-effects (suggestions, entity cards, vendor creation)
                    # after the AI reply so they appear below it.
                    flushed_effect_messages = process_side_effects(
                        write_db, side_effects=agent_resp.side_effects, conversation_id=conv_id, base_time=now,
                    )
                    db_conv = write_db.query(Conversation).filter_by(id=conv_id).first()
                    if db_conv:
                        db_conv.updated_at = now
                    write_db.commit()
                    print(f"[chat] Persisted AI reply ({len(agent_resp.reply)} chars) to {conv_id}")
                    log_trace(
                        "llm_reply",
                        "chat",
                        agent_resp.reply[:200],
                        detail=_build_llm_trace_detail(
                            flow="chat",
                            session_key=session_key,
                            messages_payload=messages_payload,
                            context_data=context_data,
                            task_id=body.task_id,
                            conversation_id=conv_id,
                            reply=agent_resp.reply,
                            side_effects=agent_resp.side_effects,
                        ),
                    )
                    return agent_resp.reply, ai_msg.id, flushed_effect_messages
                except Exception as e:
                    write_db.rollback()
                    print(f"[chat] DB write failed: {e}")
                    traceback.print_exc()
                    return agent_resp.reply, str(uuid.uuid4()), []
                finally:
                    write_db.close()
            finally:
                active_conversation_id.reset(token)
                _active_chats.pop(stream_id, None)

        running.task = asyncio.create_task(run_and_persist())

        try:
            # Emit stream_id so the client can reconnect
            yield f"data: {json.dumps({'type': 'stream_id', 'stream_id': stream_id})}\n\n"

            while not running.task.done():
                try:
                    text = await asyncio.wait_for(queue.get(), timeout=0.1)
                    yield f"data: {json.dumps({'type': 'progress', 'text': text})}\n\n"
                except asyncio.TimeoutError:
                    pass

            while not queue.empty():
                text = queue.get_nowait()
                yield f"data: {json.dumps({'type': 'progress', 'text': text})}\n\n"

            try:
                reply, msg_id, effect_msgs = running.task.result()
            except Exception as exc:
                print(f"[chat] SSE task failed: {exc!r}")
                detail = _describe_agent_error(exc)
                yield f"data: {json.dumps({'type': 'error', 'message': detail})}\n\n"
                return

            done_payload: dict = {'type': 'done', 'reply': reply, 'message_id': msg_id, 'conversation_id': public_conv_id}
            if effect_msgs:
                done_payload['effect_messages'] = effect_msgs
            # Include onboarding state so frontend can update progress without re-fetching
            try:
                _ob_db = _SL()
                try:
                    _ob_state = settings_service.get_onboarding_state(_ob_db)
                    if _ob_state:
                        done_payload['onboarding'] = _ob_state
                finally:
                    _ob_db.close()
            except Exception:
                pass
            print(f"[chat] SSE done: reply={len(reply or '')} chars, msg_id={msg_id}")
            yield f"data: {json.dumps(done_payload)}\n\n"
        finally:
            if queue in running.subscribers:
                running.subscribers.remove(queue)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

NO_RESPONSE_SENTINEL = "[NO_RESPONSE]"

# ─── Agent autoreply ─────────────────────────────────────────────────────────

_autoreply_locks: dict[str, threading.Lock] = {}
_autoreply_locks_lock = threading.Lock()
_autoreply_state: dict[str, str] = {}  # task_id → context hash from last run


def _stop_litellm_logging_worker_for_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Stop LiteLLM's background logger before closing a private loop.

    LiteLLM starts a process-global logging worker on the current event loop
    when async completions run. Autoreply uses a short-lived private loop in a
    worker thread, so we must stop that worker before closing the loop.
    """
    worker_task = getattr(GLOBAL_LOGGING_WORKER, "_worker_task", None)
    if worker_task is None or worker_task.done():
        return
    try:
        worker_loop = worker_task.get_loop()
    except RuntimeError:
        return
    if worker_loop is not loop:
        return

    loop.run_until_complete(GLOBAL_LOGGING_WORKER.stop())
    GLOBAL_LOGGING_WORKER._queue = None


def _compute_autoreply_hash(task) -> str:
    """Hash task state + latest message timestamps using a short-lived session."""

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
        conv_ids: list[int] = [task.ai_conversation_id, task.parent_conversation_id]
        conv_ids.extend(c.id for c in task.external_conversations)
        for conv_id in conv_ids:
            if conv_id:
                ts = db.execute(text(
                    "SELECT MAX(sent_at) FROM messages WHERE conversation_id = :cid"
                ), {"cid": conv_id}).scalar()
                if ts:
                    parts.append(f"{conv_id}:{ts}")
        # Pending suggestion count. Use the ORM so SQLAlchemy serializes
        # SuggestionStatus.PENDING with the same casing as the DB enum
        # (uppercase 'PENDING'); the previous raw SQL hard-coded the
        # lowercase Python value and crashed on the real Postgres enum.
        count = db.execute(
            select(func.count()).select_from(Suggestion).where(
                Suggestion.task_id == task.id,
                Suggestion.status == SuggestionStatus.PENDING,
            )
        ).scalar()
        parts.append(f"suggestions:{count}")
        return hashlib.md5("|".join(parts).encode()).hexdigest()
    finally:
        db.close()

def agent_task_autoreply(task_id: str, hint: str | None = None) -> str | None:
    """Run the agent against a task and let it respond/act.

    This is the core primitive for driving autonomous tasks forward.
    Called when external messages arrive or periodically by the reply scanner.

    Returns the agent reply text, or None if no response was needed.
    """
    # Per-task lock prevents concurrent autoreplies for the same task
    with _autoreply_locks_lock:
        if task_id not in _autoreply_locks:
            _autoreply_locks[task_id] = threading.Lock()
        lock = _autoreply_locks[task_id]

    if not lock.acquire(blocking=False):
        return None  # another autoreply is already running for this task

    try:
        return _agent_task_autoreply_inner(task_id, hint)
    finally:
        lock.release()

def _agent_task_autoreply_inner(task_id: str, hint: str | None = None) -> str | None:
    db = SessionLocal.session_factory()
    request_context_token = None
    try:
        task = db.query(Task).filter_by(id=task_id).first()
        if not task:
            return None
        request_context_token = set_request_context(
            account_id=task.creator_id,
            org_id=getattr(task, "org_id", None),
        )
        conv = task.ai_conversation
        if not conv:
            return None

        # Background callers (reply_scanner, portal autoreply daemon
        # threads, demo simulator) never went through HTTP auth, so
        # the request context is empty and any DB query downstream
        # that calls ``resolve_account_id`` would raise. Derive the
        # context from the task we just loaded and pin it for the
        # rest of this autoreply.
        ctx_token = set_request_context(
            account_id=task.creator_id,
            org_id=task.org_id,
        )

        # Change detection: skip if nothing changed since the last reply scan
        current_hash = _compute_autoreply_hash(task)
        if _autoreply_state.get(task_id) == current_hash:
            print(f"\033[33m[reply_scanner] Skipping task {task_id} — no changes since last run\033[0m")
            log_trace("reply_scan", "reply_scanner", "Skipped — no context changes")
            return None

        conv_id = conv.id
        ext_conv = task.latest_external_conversation
        ext_conv_id = ext_conv.id if ext_conv else None

        # Set typing indicator on external conversation
        if ext_conv_id:
            ext_conv = db.query(Conversation).filter_by(id=ext_conv_id).first()
            if ext_conv:
                ext_conv.extra = chat_service.set_conversation_ai_typing(ext_conv.extra, ai_typing=True)
                flag_modified(ext_conv, "extra")

        # Build context + message history
        default_hint = "Check this task for anything that needs attention."
        context = build_task_context(db, task_id, query=hint or default_hint)

        # Gather progress steps
        steps_text = ""
        if task.steps:
            steps_text = "\n\nTask progress steps:\n" + json.dumps(task.steps, indent=2)

        # Build AI conversation history
        all_msgs = [
            m for m in (conv.messages or [])
            if m.message_type in (MessageType.MESSAGE, MessageType.THREAD)
        ]
        msg_rows = sorted(all_msgs, key=lambda m: m.sent_at)[-20:]
        messages_payload = [{"role": "system", "content": context}]
        messages_payload += chat_service.model_history_messages(msg_rows)

        # The hint (or default) is the "user" message that triggers the agent
        user_msg = (hint or default_hint) + steps_text
        messages_payload.append({"role": "user", "content": user_msg})

        db.commit()  # flush typing indicator + detach ORM objects

        # Run agent
        agent_id = agent_registry.ensure_agent(str(resolve_account_id()), db)
        session_key = f"task:{task_id}"

        # Run agent in a dedicated thread with its own event loop so we
        # don't conflict with the main uvloop (reply_scanner_loop calls us from
        # within the running async loop).
        _agent_result = [None, None]  # [resp, pending]

        def _run_in_thread():
            _request_context_token = set_request_context(
                account_id=task.creator_id,
                org_id=getattr(task, "org_id", None),
            )
            _conv_token = active_conversation_id.set(conv_id)
            _pending_token = pending_suggestion_messages.set([])
            _loop = asyncio.new_event_loop()
            try:
                _agent_result[0] = _loop.run_until_complete(
                    agent_client.call_agent(agent_id, session_key=session_key, messages=messages_payload)
                )
                _agent_result[1] = pending_suggestion_messages.get() or []
            finally:
                _stop_litellm_logging_worker_for_loop(_loop)
                _loop.close()
                active_conversation_id.reset(_conv_token)
                pending_suggestion_messages.reset(_pending_token)
                reset_request_context(_request_context_token)

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
            _autoreply_state[task_id] = current_hash
            log_trace("reply_scan", "reply_scanner", f"No response needed for task {task_id}")
            return None

        # Persist results
        write_db = SessionLocal.session_factory()
        try:
            now = datetime.now(UTC)

            # AI reply to AI conversation
            ai_msg = Message(
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

            # Recompute hash after agent run so next autoreply knows the state
            try:
                fresh = write_db.query(Task).filter_by(id=task_id).first()
                if fresh:
                    _autoreply_state[task_id] = _compute_autoreply_hash(fresh)
            except Exception:
                pass

            log_trace("reply_scan", "reply_scanner",
                      f"Agent replied ({len(resp.reply)} chars): {resp.reply[:100]}")

            return resp.reply
        except Exception as e:
            write_db.rollback()
            print(f"\033[31m[reply_scanner] DB write failed for task {task_id}: {e}\033[0m")
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
        print(f"\033[31m[reply_scanner] Failed for task {task_id}: {e}\033[0m")
        traceback.print_exc()
        log_trace("error", "reply_scanner", f"Reply scan failed: {e}")
        return None
    finally:
        if request_context_token is not None:
            reset_request_context(request_context_token)
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
    _SL = SessionLocal.session_factory

    task_obj = db.query(Task).filter_by(id=body.task_id).first()
    if not task_obj:
        raise HTTPException(status_code=404, detail="Task not found")
    conv = task_obj.ai_conversation
    if not conv:
        raise HTTPException(status_code=404, detail="Task has no AI conversation")

    context_data = build_task_context_data(db, body.task_id, query=body.message)
    context = context_data["text"]
    conv_id = conv.id
    ext_conv = task_obj.latest_external_conversation
    ext_conv_id = ext_conv.id if ext_conv else None

    # Set typing indicator on the external conversation so the vendor portal
    # can show it while the agent is thinking.
    if ext_conv:
        ext_conv.extra = chat_service.set_conversation_ai_typing(ext_conv.extra, ai_typing=True)
        flag_modified(ext_conv, "extra")
    # Gather progress steps and external conversation for the assess prompt
    steps_text = ""
    if task_obj.steps:
        steps_text = "\n\nTask progress steps:\n" + _assessjson.dumps(task_obj.steps, indent=2)

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
    messages_payload += chat_service.model_history_messages(msg_rows)

    db.commit()
    messages_payload.append({
        "role": "user",
        "content": ASSESS_PROMPT + steps_text + ext_msgs_text,
    })

    agent_id = agent_registry.ensure_agent(resolve_account_id(), db)
    session_key = f"task:{body.task_id}"
    stream_id = str(uuid.uuid4())

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
            token = active_conversation_id.set(conv_id)
            try:
                # NOTE: We do NOT persist a user message — the assess prompt is
                # an internal trigger, not a real user message.
                await on_progress("Thinking\u2026")
                log_trace(
                    "llm_request",
                    "assess",
                    f"Prepared {len(messages_payload)} messages for model call",
                    detail=_build_llm_trace_detail(
                        flow="assess",
                        session_key=session_key,
                        messages_payload=messages_payload,
                        context_data=context_data,
                        task_id=body.task_id,
                        conversation_id=conv_id,
                    ),
                )
                agent_resp = await agent_client.call_agent(
                    agent_id,
                    session_key=session_key,
                    messages=messages_payload,
                    on_progress=on_progress,
                    trace_context=_build_llm_trace_detail(
                        flow="assess",
                        session_key=session_key,
                        messages_payload=messages_payload,
                        context_data=context_data,
                        task_id=body.task_id,
                        conversation_id=conv_id,
                    ),
                )

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
                        conversation_id=conv_id,
                        sender_type=ParticipantType.ACCOUNT_USER,
                        body=agent_resp.reply,
                        message_type=MessageType.MESSAGE,
                        sender_name="RentMate",
                        is_ai=True,
                        sent_at=now,
                    )
                    write_db.add(ai_msg)
                    flushed_effect_messages = process_side_effects(
                        write_db, side_effects=agent_resp.side_effects, conversation_id=conv_id, base_time=now,
                    )
                    db_conv = write_db.query(Conversation).filter_by(id=conv_id).first()
                    if db_conv:
                        db_conv.updated_at = now
                    write_db.commit()
                    print(f"[assess] Persisted AI reply ({len(agent_resp.reply)} chars) to AI conv {conv_id}")
                    log_trace(
                        "llm_reply",
                        "assess",
                        agent_resp.reply[:200],
                        detail=_build_llm_trace_detail(
                            flow="assess",
                            session_key=session_key,
                            messages_payload=messages_payload,
                            context_data=context_data,
                            task_id=body.task_id,
                            conversation_id=conv_id,
                            reply=agent_resp.reply,
                            side_effects=agent_resp.side_effects,
                        ),
                    )
                    return agent_resp.reply, ai_msg.id, flushed_effect_messages
                except Exception as e:
                    write_db.rollback()
                    print(f"[assess] DB write failed: {e}")
                    traceback.print_exc()
                    return agent_resp.reply, str(uuid.uuid4()), []
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
            yield f"data: {json.dumps({'type': 'stream_id', 'stream_id': stream_id})}\n\n"

            while not running.task.done():
                try:
                    text = await asyncio.wait_for(queue.get(), timeout=0.1)
                    yield f"data: {json.dumps({'type': 'progress', 'text': text})}\n\n"
                except asyncio.TimeoutError:
                    pass

            while not queue.empty():
                text = queue.get_nowait()
                yield f"data: {json.dumps({'type': 'progress', 'text': text})}\n\n"

            try:
                reply, msg_id, effect_msgs = running.task.result()
            except Exception as exc:
                print(f"[assess] SSE task failed: {exc!r}")
                yield f"data: {json.dumps({'type': 'error', 'message': 'AI unavailable'})}\n\n"
                return

            done_payload: dict = {'type': 'done', 'reply': reply, 'message_id': msg_id, 'conversation_id': conv_id}
            if effect_msgs:
                done_payload['effect_messages'] = effect_msgs
            yield f"data: {json.dumps(done_payload)}\n\n"
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
    conv = get_or_create_user_ai_conversation(db, creator_id="default", user_id="default", session_key=None)
    db.commit()
    return {"id": conv.id, "title": conv.subject}

class SpawnTaskRequest(BaseModel):
    parent_conversation_id: str
    objective: str
    category: Optional[str] = None
    urgency: Optional[str] = None
    priority: Optional[str] = None
    task_mode: str = "AUTONOMOUS"
    source: str = TaskSource.MANUAL

@router.post("/chat/task/spawn")
async def spawn_task_endpoint(
    body: SpawnTaskRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    await require_user(request)
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

# ─── Onboarding state ────────────────────────────────────────────────────────

@router.get("/onboarding/state")
async def get_onboarding_state_endpoint(request: Request, db: Session = Depends(get_db)):
    """Return current onboarding state, or null if the account already has data."""
    await require_user(request)

    llm_configured = True if _hosted_mode() else settings_service.is_llm_configured()
    state = settings_service.get_onboarding_state(db)
    if state is not None:
        # Backfill configure_llm step for existing onboarding states
        if "configure_llm" not in state.get("steps", {}):
            state["steps"]["configure_llm"] = "done" if llm_configured else "pending"
        elif _hosted_mode():
            state["steps"]["configure_llm"] = "done"
        return {"onboarding": state, "llm_configured": llm_configured}
    # No state yet — initialize only if the account is truly empty
    prop_count = db.query(Property).count()
    tenant_count = db.query(Tenant).count()
    doc_count = db.query(Document).count()
    if prop_count == 0 and tenant_count == 0 and doc_count == 0:
        state = settings_service.init_onboarding(db)
        if _hosted_mode():
            state["steps"]["configure_llm"] = "done"
        db.commit()
        log_trace("onboarding", "chat", "Onboarding initialized")
        return {"onboarding": state, "llm_configured": llm_configured}
    return {"onboarding": None, "llm_configured": llm_configured}


@router.post("/onboarding/dismiss")
async def dismiss_onboarding_endpoint(request: Request, db: Session = Depends(get_db)):
    """Dismiss onboarding permanently."""
    await require_user(request)

    state = settings_service.dismiss_onboarding(db)
    db.commit()
    log_trace("onboarding", "chat", "Onboarding dismissed", detail=state)
    return {"onboarding": state}


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
            yield f"data: {json.dumps({'type': 'idle'})}\n\n"
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
                yield f"data: {json.dumps({'type': 'progress', 'text': text})}\n\n"

            while not running.task.done():
                try:
                    text = await asyncio.wait_for(sub.get(), timeout=0.1)
                    yield f"data: {json.dumps({'type': 'progress', 'text': text})}\n\n"
                except asyncio.TimeoutError:
                    pass

            while not sub.empty():
                text = sub.get_nowait()
                yield f"data: {json.dumps({'type': 'progress', 'text': text})}\n\n"

            try:
                reply, msg_id = running.task.result()
                done_payload: dict = {'type': 'done', 'reply': reply, 'message_id': msg_id}
                if actions:
                    done_payload['actions'] = actions
                yield f"data: {json.dumps(done_payload)}\n\n"
            except Exception:
                yield f"data: {json.dumps({'type': 'error', 'message': 'AI unavailable'})}\n\n"
        finally:
            if sub in running.subscribers:
                running.subscribers.remove(sub)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
