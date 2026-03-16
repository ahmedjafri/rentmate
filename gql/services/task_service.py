from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session
from db.models import Conversation, Message, ParticipantType as PT
from gql.types import CreateTaskInput, AddTaskMessageInput, UpdateTaskInput


class TaskService:
    @staticmethod
    def create_task(sess: Session, input: CreateTaskInput) -> Conversation:
        task = Conversation(
            subject=input.title,
            is_task=True,
            task_status=input.task_status,
            task_mode=input.task_mode,
            source=input.source,
            category=input.category,
            urgency=input.urgency,
            priority=input.priority,
            confidential=input.confidential,
            property_id=input.property_id,
            unit_id=input.unit_id,
        )
        sess.add(task)
        sess.commit()
        sess.refresh(task)
        return task

    @staticmethod
    def update_task_status(sess: Session, uid: str, status: str) -> Conversation:
        task = sess.execute(
            select(Conversation).where(Conversation.id == uid, Conversation.is_task == True)  # noqa: E712
        ).scalar_one_or_none()
        if not task:
            raise ValueError(f"Task {uid} not found")
        task.task_status = status
        sess.commit()
        sess.refresh(task)
        return task

    @staticmethod
    def update_task(sess: Session, input: UpdateTaskInput) -> Conversation:
        task = sess.execute(
            select(Conversation).where(Conversation.id == input.uid, Conversation.is_task == True)  # noqa: E712
        ).scalar_one_or_none()
        if not task:
            raise ValueError(f"Task {input.uid} not found")
        if input.task_mode is not None:
            task.task_mode = input.task_mode
        if input.task_status is not None:
            task.task_status = input.task_status
        sess.commit()
        sess.refresh(task)
        return task

    @staticmethod
    def delete_task(sess: Session, uid: str) -> bool:
        task = sess.execute(
            select(Conversation).where(Conversation.id == uid, Conversation.is_task == True)  # noqa: E712
        ).scalar_one_or_none()
        if not task:
            raise ValueError(f"Task {uid} not found")
        sess.delete(task)
        sess.commit()
        return True

    @staticmethod
    def add_task_message(sess: Session, input: AddTaskMessageInput) -> Message:
        task = sess.execute(
            select(Conversation).where(Conversation.id == input.task_id, Conversation.is_task == True)  # noqa: E712
        ).scalar_one_or_none()
        if not task:
            raise ValueError(f"Task {input.task_id} not found")
        msg = Message(
            conversation_id=input.task_id,
            sender_type=PT.ACCOUNT_USER,
            body=input.body,
            message_type=input.message_type,
            sender_name=input.sender_name,
            is_ai=input.is_ai,
            is_system=False,
            sent_at=datetime.utcnow(),
        )
        sess.add(msg)
        task.last_message_at = datetime.utcnow()
        sess.commit()
        sess.refresh(msg)
        return msg
