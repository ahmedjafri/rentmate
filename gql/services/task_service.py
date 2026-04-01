from datetime import UTC, datetime
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from db.models import Task, Conversation, ExternalContact, Message, ParticipantType as PT, ConversationType
from gql.types import CreateTaskInput, UpdateTaskInput


def _get_account_id(sess: Session, property_id: str | None, unit_id: str | None) -> str:
    from sqlalchemy import text
    try:
        if property_id:
            res = sess.execute(text("SELECT account_id FROM properties WHERE id = :id"), {"id": property_id}).fetchone()
            if res and res[0]:
                return res[0]
        if unit_id:
            res = sess.execute(text("SELECT account_id FROM units WHERE id = :id"), {"id": unit_id}).fetchone()
            if res and res[0]:
                return res[0]
    except Exception:
        pass
    return "00000000-0000-0000-0000-000000000001"


class TaskService:
    @staticmethod
    def create_task(sess: Session, input: CreateTaskInput) -> Task:
        account_id = _get_account_id(sess, input.property_id, input.unit_id)
        task = Task(
            account_id=account_id,
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

        # Assign task_number per account
        max_num = sess.execute(
            select(func.coalesce(func.max(Task.task_number), 0))
            .where(Task.account_id == task.account_id)
        ).scalar()
        task.task_number = max_num + 1

        # Create the primary internal conversation thread for this task
        ai_convo = Conversation(
            subject=input.title,
            property_id=input.property_id,
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
    def update_task_status(sess: Session, uid: str, status: str) -> Task:
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
        sess.delete(task)
        sess.flush()
        if ai_conv_id:
            convo = sess.get(Conversation, ai_conv_id)
            if convo:
                sess.delete(convo)   # cascades messages/participants
        sess.flush()
        return True

    @staticmethod
    def assign_vendor_to_task(sess: Session, task_id: str, vendor_id: str) -> Task:
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
        # Block pending rentmate vendors from being assigned
        extra = vendor.extra or {}
        if extra.get("contact_method") == "rentmate" and extra.get("invite_status") == "pending":
            raise ValueError(f"Vendor {vendor.name} has not accepted their invite yet")
        # Store vendor info in the AI conversation's extra field
        ai_convo = sess.get(Conversation, task.ai_conversation_id) if task.ai_conversation_id else None
        if ai_convo:
            extra = dict(ai_convo.extra or {})
            extra["assigned_vendor_id"] = vendor_id
            extra["assigned_vendor_name"] = vendor.name
            ai_convo.extra = extra
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(ai_convo, "extra")
        sess.flush()
        return task

