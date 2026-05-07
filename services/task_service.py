from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from db.enums import TaskStatus, TaskStepStatus, parse_task_mode
from db.id_utils import normalize_optional_id
from db.models import (
    Conversation,
    ConversationType,
    Suggestion,
    Task,
    User,
)
from gql.types import CreateTaskInput, UpdateTaskInput
from integrations.local_auth import resolve_account_id, resolve_org_id
from services.number_allocator import NumberAllocator


class TaskProgressStep(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, use_enum_values=True)

    key: str
    label: str
    status: TaskStepStatus
    note: str | None = None


def dump_task_steps(steps: list[TaskProgressStep]) -> list[dict] | None:
    if not steps:
        return None
    return [step.model_dump(exclude_none=True) for step in steps]


class TaskService:
    @staticmethod
    def touch_task(task: Task) -> Task:
        task.updated_at = datetime.now(UTC)
        return task

    @staticmethod
    def create_task(sess: Session, input: CreateTaskInput) -> Task:
        creator_id = resolve_account_id()
        org_id = resolve_org_id()
        property_id = normalize_optional_id(input.property_id)
        unit_id = normalize_optional_id(input.unit_id)
        goal = (input.goal or "").strip()
        if not goal:
            raise ValueError("Task goal is required")
        next_id = NumberAllocator.allocate_next(sess, entity_type="task", org_id=org_id)
        task = Task(
            id=next_id,
            org_id=org_id,
            creator_id=creator_id,
            title=input.title,
            goal=goal,
            task_status=input.task_status,
            task_mode=parse_task_mode(input.task_mode),
            source=input.source,
            category=input.category,
            urgency=input.urgency,
            priority=input.priority,
            confidential=input.confidential,
            property_id=property_id,
            unit_id=unit_id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
        )
        sess.add(task)
        sess.flush()

        ai_convo = Conversation(
            org_id=org_id,
            subject=input.title,
            property_id=property_id,
            creator_id=creator_id,
            unit_id=unit_id,
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
    def update_task_status(sess: Session, *, uid: int, status: TaskStatus) -> Task:
        task = sess.execute(
            select(Task).where(
                Task.id == uid,
                Task.org_id == resolve_org_id(),
            )
        ).scalar_one_or_none()
        if not task:
            raise ValueError(f"Task {uid} not found")
        task.task_status = status
        if status == TaskStatus.RESOLVED and not task.resolved_at:
            task.resolved_at = datetime.now(UTC)
        TaskService.touch_task(task)
        sess.flush()
        return task

    @staticmethod
    def update_task_goal(sess: Session, *, uid: int, goal: str) -> Task:
        task = sess.execute(
            select(Task).where(
                Task.id == uid,
                Task.org_id == resolve_org_id(),
            )
        ).scalar_one_or_none()
        if not task:
            raise ValueError(f"Task {uid} not found")
        cleaned = (goal or "").strip()
        if not cleaned:
            raise ValueError("Task goal is required")
        task.goal = cleaned
        TaskService.touch_task(task)
        sess.flush()
        return task

    @staticmethod
    def update_task(sess: Session, input: UpdateTaskInput) -> Task:
        task = sess.execute(
            select(Task).where(
                Task.id == input.uid,
                Task.org_id == resolve_org_id(),
            )
        ).scalar_one_or_none()
        if not task:
            raise ValueError(f"Task {input.uid} not found")
        if input.task_mode is not None:
            task.task_mode = parse_task_mode(input.task_mode)
        if input.task_status is not None:
            task.task_status = input.task_status
            if input.task_status == TaskStatus.RESOLVED and not task.resolved_at:
                task.resolved_at = datetime.now(UTC)
        TaskService.touch_task(task)
        if input.category is not None:
            task.category = input.category
        if input.urgency is not None:
            task.urgency = input.urgency
        sess.flush()
        return task

    @staticmethod
    def mark_task_seen(sess: Session, *, uid: int) -> Task:
        task = sess.execute(
            select(Task).where(
                Task.id == uid,
                Task.org_id == resolve_org_id(),
            )
        ).scalar_one_or_none()
        if not task:
            raise ValueError(f"Task {uid} not found")
        task.last_seen_at = datetime.now(UTC)
        sess.flush()
        return task

    @staticmethod
    def delete_task(sess: Session, uid: int) -> bool:
        task = sess.execute(
            select(Task).where(
                Task.id == uid,
                Task.org_id == resolve_org_id(),
            )
        ).scalar_one_or_none()
        if not task:
            raise ValueError(f"Task {uid} not found")
        ai_conv_id = task.ai_conversation_id
        for s in sess.execute(
            select(Suggestion).where(
                Suggestion.task_id == uid,
                Suggestion.org_id == resolve_org_id(),
            )
        ).scalars().all():
            sess.delete(s)
        sess.flush()
        sess.delete(task)
        sess.flush()
        if ai_conv_id:
            convo = sess.get(Conversation, ai_conv_id)
            if convo:
                sess.delete(convo)
        sess.flush()
        return True

    @staticmethod
    def assign_vendor_to_task(sess: Session, *, task_id: int, vendor_id: int) -> Task:
        """Link a vendor to a task and record the assignment in the AI conversation.

        The caller (handler layer) is responsible for creating or finding the
        vendor's external conversation (with `parent_task_id` set) before
        calling this method.
        """
        task = sess.execute(
            select(Task).where(
                Task.id == task_id,
                Task.org_id == resolve_org_id(),
            )
        ).scalar_one_or_none()
        if not task:
            raise ValueError(f"Task {task_id} not found")
        vendor = sess.execute(
            select(User).where(
                User.id == vendor_id,
                User.org_id == resolve_org_id(),
                User.user_type == "vendor",
            )
        ).scalar_one_or_none()
        if not vendor:
            raise ValueError(f"Vendor {vendor_id} not found")
        ai_convo = sess.get(Conversation, task.ai_conversation_id) if task.ai_conversation_id else None
        if ai_convo:
            from services.chat_service import assign_conversation_vendor

            ai_convo.extra = assign_conversation_vendor(
                ai_convo.extra,
                vendor_id=vendor_id,
                vendor_name=vendor.name,
            )
            flag_modified(ai_convo, "extra")
        TaskService.touch_task(task)
        sess.flush()
        return task
