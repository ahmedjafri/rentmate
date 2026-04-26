"""Reply scanner loop — scans autonomous tasks for unread external messages.

When an inbound message arrives on a task's external conversation and the
task is in autonomous mode, this loop triggers the agent to respond.
"""
import asyncio
import logging

from sqlalchemy import select as sa_select

from db.enums import TaskMode, TaskStatus
from db.models import Message, MessageType, ParticipantType, Task
from db.session import SessionLocal

logger = logging.getLogger("rentmate.reply_scanner")

_POLL_SECONDS = 60


def _reply_scan():
    """Scan for autonomous tasks needing attention and run the reply scanner."""
    from handlers.chat import agent_task_autoreply
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
            conv_ids: list[int] = [c.id for c in task.external_conversations]
            if task.parent_conversation_id:
                conv_ids.append(task.parent_conversation_id)
            for conv_id in conv_ids:
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
                    agent_task_autoreply(str(task.id), hint=hint)
                    triggered += 1
                except Exception as exc:
                    logger.exception("Reply scanner failed for task %s: %s", task.id, exc)
                break

        if triggered:
            logger.info("Reply scanner loop: triggered %d task(s)", triggered)
    except Exception as exc:
        logger.exception("Reply scanner loop error: %s", exc)
    finally:
        db.close()


async def reply_scanner_loop():
    """Background loop: run the reply scan in a thread every 60s."""
    while True:
        await asyncio.sleep(_POLL_SECONDS)
        try:
            await asyncio.to_thread(_reply_scan)
        except Exception as exc:
            logger.exception("Reply scanner loop error: %s", exc)
