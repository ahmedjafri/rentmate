from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session
from db.models import Task, Conversation, ExternalContact, Message, ParticipantType as PT
from gql.types import CreateTaskInput, AddTaskMessageInput, UpdateTaskInput


class TaskService:
    @staticmethod
    def create_task(sess: Session, input: CreateTaskInput) -> Task:
        task = Task(
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
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        sess.add(task)
        sess.flush()
        # Create the primary internal conversation thread for this task
        convo = Conversation(
            task_id=task.id,
            subject=input.title,
            property_id=input.property_id,
            unit_id=input.unit_id,
        )
        sess.add(convo)
        sess.commit()
        sess.refresh(task)
        return task

    @staticmethod
    def update_task_status(sess: Session, uid: str, status: str) -> Task:
        task = sess.execute(
            select(Task).where(Task.id == uid)
        ).scalar_one_or_none()
        if not task:
            raise ValueError(f"Task {uid} not found")
        task.task_status = status
        sess.commit()
        sess.refresh(task)
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
        sess.commit()
        sess.refresh(task)
        return task

    @staticmethod
    def delete_task(sess: Session, uid: str) -> bool:
        task = sess.execute(
            select(Task).where(Task.id == uid)
        ).scalar_one_or_none()
        if not task:
            raise ValueError(f"Task {uid} not found")
        sess.delete(task)
        sess.commit()
        return True

    @staticmethod
    def assign_vendor_to_task(sess: Session, task_id: str, vendor_id: str) -> Task:
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
        # Store vendor assignment in the first linked conversation's extra field
        convo = sess.execute(
            select(Conversation).where(Conversation.task_id == task.id)
        ).scalars().first()
        if convo:
            extra = dict(convo.extra or {})
            extra["assigned_vendor_id"] = vendor_id
            extra["assigned_vendor_name"] = vendor.name
            convo.extra = extra
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(convo, "extra")
        sess.commit()
        sess.refresh(task)
        return task

    @staticmethod
    def add_task_message(sess: Session, input: AddTaskMessageInput) -> Message:
        task = sess.execute(
            select(Task).where(Task.id == input.task_id)
        ).scalar_one_or_none()
        if not task:
            raise ValueError(f"Task {input.task_id} not found")
        # Find the primary (first) conversation for this task
        convo = sess.execute(
            select(Conversation).where(Conversation.task_id == task.id)
        ).scalars().first()
        if not convo:
            raise ValueError(f"No conversation found for task {input.task_id}")
        msg = Message(
            conversation_id=convo.id,
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
