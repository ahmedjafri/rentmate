from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backends.local_auth import resolve_account_id
from db.models import (
    Conversation,
    ConversationType,
    ExternalContact,
    Suggestion,
    Task,
    TaskNumberSequence,
)
from gql.types import CreateTaskInput, UpdateTaskInput


def _get_creator_id(sess: Session, property_id: str | None, unit_id: str | None) -> str:
    try:
        if property_id:
            res = sess.execute(text("SELECT creator_id FROM properties WHERE id = :id"), {"id": property_id}).fetchone()
            if res and res[0]:
                return res[0]
        if unit_id:
            res = sess.execute(text("SELECT creator_id FROM units WHERE id = :id"), {"id": unit_id}).fetchone()
            if res and res[0]:
                return res[0]
    except Exception:
        pass
    return 1


class TaskService:
    @staticmethod
    def create_task(sess: Session, input: CreateTaskInput) -> Task:
        creator_id = _get_creator_id(sess, input.property_id, input.unit_id)
        task = Task(
            creator_id=creator_id,
            title=input.title,
            task_status=input.task_status,
            task_mode=input.task_mode,
            source=input.source,
            category=input.category,
            urgency=input.urgency,
            priority=input.priority,
            confidential=input.confidential,
            property_id=input.property_id,
            unit_id=input.unit_id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        sess.add(task)
        sess.flush()

        # Assign task_number from the per-account sequence (monotonically
        # increasing, never reused even after task deletion).
        seq = sess.execute(
            select(TaskNumberSequence)
            .where(TaskNumberSequence.creator_id == task.creator_id)
        ).scalar_one_or_none()
        if seq is None:
            seq = TaskNumberSequence(creator_id=task.creator_id, last_number=0)
            sess.add(seq)
            sess.flush()
        seq.last_number += 1
        task.task_number = seq.last_number

        # Create the primary internal conversation thread for this task
        ai_convo = Conversation(
            subject=input.title,
            property_id=input.property_id,
            creator_id=resolve_account_id(),
            unit_id=input.unit_id,
            conversation_type=ConversationType.TASK_AI,
            is_group=False,
            is_archived=False,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        sess.add(ai_convo)
        sess.flush()
        task.ai_conversation_id = ai_convo.id

        return task

    @staticmethod
    def update_task_status(sess: Session, *, uid: str, status: str) -> Task:
        task = sess.execute(
            select(Task).where(Task.id == uid)
        ).scalar_one_or_none()
        if not task:
            raise ValueError(f"Task {uid} not found")
        task.task_status = status
        if status == "resolved" and not task.resolved_at:
            task.resolved_at = datetime.now(UTC)
        sess.flush()
        return task

    @staticmethod
    def update_task(sess: Session, input: UpdateTaskInput) -> Task:
        task = sess.execute(
            select(Task).where(Task.id == input.uid)
        ).scalar_one_or_none()
        if not task:
            raise ValueError(f"Task {input.uid} not found")
        if input.task_mode is not None:
            task.task_mode = input.task_mode
        if input.task_status is not None:
            task.task_status = input.task_status
            if input.task_status == "resolved" and not task.resolved_at:
                task.resolved_at = datetime.now(UTC)
        sess.flush()
        return task

    @staticmethod
    def delete_task(sess: Session, uid: str) -> bool:
        task = sess.execute(
            select(Task).where(Task.id == uid)
        ).scalar_one_or_none()
        if not task:
            raise ValueError(f"Task {uid} not found")
        ai_conv_id = task.ai_conversation_id
        # Delete associated suggestions
        for s in sess.execute(select(Suggestion).where(Suggestion.task_id == uid)).scalars().all():
            sess.delete(s)
        sess.flush()
        sess.delete(task)
        sess.flush()
        if ai_conv_id:
            convo = sess.get(Conversation, ai_conv_id)
            if convo:
                sess.delete(convo)   # cascades messages/participants
        sess.flush()
        return True

    @staticmethod
    def assign_vendor_to_task(sess: Session, *, task_id: str, vendor_id: str) -> Task:
        """Link a vendor to a task and record the assignment in the AI conversation.

        The caller (handler layer) is responsible for creating or finding the
        external conversation and setting task.external_conversation_id before
        calling this method.
        """
        task = sess.execute(
            select(Task).where(Task.id == task_id)
        ).scalar_one_or_none()
        if not task:
            raise ValueError(f"Task {task_id} not found")
        vendor = sess.execute(
            select(ExternalContact).where(ExternalContact.id == vendor_id)
        ).scalar_one_or_none()
        if not vendor:
            raise ValueError(f"Vendor {vendor_id} not found")
        # Store vendor info in the AI conversation's extra field
        ai_convo = sess.get(Conversation, task.ai_conversation_id) if task.ai_conversation_id else None
        if ai_convo:
            extra = dict(ai_convo.extra or {})
            extra["assigned_vendor_id"] = vendor_id
            extra["assigned_vendor_name"] = vendor.name
            ai_convo.extra = extra
            flag_modified(ai_convo, "extra")
        sess.flush()
        return task

