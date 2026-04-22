"""Heartbeat loop — scans autonomous tasks for unread external messages.

When an inbound message arrives on a task's external conversation and the
task is in autonomous mode, this loop triggers the agent to respond.
"""
import asyncio
import logging

from sqlalchemy import select as sa_select

from db.enums import TaskStatus
from db.models import Message, MessageType, ParticipantType, Task
from db.session import SessionLocal

logger = logging.getLogger("rentmate.heartbeat")

_POLL_SECONDS = 60


def _heartbeat_scan():
    """Scan for autonomous tasks needing attention and run heartbeat (runs in thread)."""
    from handlers.chat import agent_task_heartbeat
    db = SessionLocal.session_factory()
    try:
        tasks = db.execute(
            sa_select(Task).where(
                Task.task_mode == TaskMode.AUTONOMOUS.name,
                Task.task_status.in_([TaskStatus.ACTIVE, TaskStatus.SUGGESTED]),
            )
        ).scalars().all()

        triggered = 0
        for task in tasks:
            for conv_id in [task.external_conversation_id, task.parent_conversation_id]:
                if not conv_id:
                    continue
                last_msg = db.execute(
                    sa_select(Message)
                    .where(Message.conversation_id == conv_id,
                           Message.message_type == MessageType.MESSAGE)
                    .order_by(Message.sent_at.desc())
                    .limit(1)
                ).scalar_one_or_none()
                if not last_msg:
                    continue
                if last_msg.is_ai or last_msg.sender_type == ParticipantType.ACCOUNT_USER:
                    continue
                sender = last_msg.sender_name or "Someone"
                hint = f"{sender} sent a message that may need a response."
                try:
                    agent_task_heartbeat(str(task.id), hint=hint)
                    triggered += 1
                except Exception as exc:
                    logger.exception("Heartbeat failed for task %s: %s", task.id, exc)
                break

        if triggered:
            logger.info("Heartbeat loop: triggered %d task(s)", triggered)
    except Exception as exc:
        logger.exception("Heartbeat loop error: %s", exc)
    finally:
        db.close()


async def heartbeat_loop():
    """Background loop: run heartbeat scan in a thread every 60s."""
    while True:
        await asyncio.sleep(_POLL_SECONDS)
        try:
            await asyncio.to_thread(_heartbeat_scan)
        except Exception as exc:
            logger.exception("Heartbeat loop error: %s", exc)
