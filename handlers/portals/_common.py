"""Shared helpers for tenant and vendor portal handlers."""
from __future__ import annotations

import threading
import time
import traceback
from typing import Iterable

from fastapi import HTTPException, Request
from pydantic import BaseModel

from db.models import Message, MessageType, Task


class SendMessageBody(BaseModel):
    body: str


def read_bearer_token(request: Request) -> str:
    """Extract a Bearer token from the Authorization header. 401 if missing."""
    auth = request.headers.get("Authorization", "")
    token = auth.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return token


_HIDDEN_MESSAGE_TYPES = (
    MessageType.INTERNAL,
    MessageType.APPROVAL,
    MessageType.SUGGESTION,
    MessageType.CONTEXT,
)


def serialize_message(m: Message) -> dict:
    return {
        "id": str(m.id),
        "body": m.body or "",
        "sender_name": m.sender_name or "",
        "sender_type": m.sender_type.value if m.sender_type else "account_user",
        "is_ai": m.is_ai,
        "sent_at": m.sent_at.isoformat() + "Z",
    }


def serialize_visible_messages(messages: Iterable[Message]) -> list[dict]:
    return [
        serialize_message(m)
        for m in messages
        if m.message_type not in _HIDDEN_MESSAGE_TYPES
    ]


def serialize_task_list_row(task: Task) -> dict:
    return {
        "id": str(task.id),
        "task_number": task.id,
        "title": task.title,
        "status": task.task_status,
        "category": task.category,
        "created_at": task.created_at.isoformat() + "Z",
    }


def _run_autoreply(task_id: str, hint: str) -> None:
    print(f"\033[33m[autoreply] Triggering for task {task_id}: {hint}\033[0m")
    time.sleep(1)  # let the request session close before accessing DB
    try:
        from handlers.chat import agent_task_autoreply
        result = agent_task_autoreply(task_id, hint=hint)
        print(f"\033[33m[autoreply] Result for task {task_id}: {'replied' if result else 'no response'}\033[0m")
    except Exception as e:
        print(f"\033[31m[autoreply] Failed for task {task_id}: {e}\033[0m")
        traceback.print_exc()


def trigger_task_autoreply(task_id: str, *, sender_name: str, body: str) -> None:
    """Fire an agent autoreply in a daemon thread after the request session closes."""
    hint = f"{sender_name} sent a message: {body.strip()[:100]}"
    threading.Thread(
        target=_run_autoreply, args=(task_id, hint), daemon=True,
    ).start()
