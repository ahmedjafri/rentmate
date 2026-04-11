import pytest

from db.enums import TaskCategory, TaskMode, TaskPriority, TaskSource, TaskStatus, Urgency
from db.models import Conversation, ConversationType, Suggestion, User
from gql.services.task_service import TaskService
from gql.types import CreateTaskInput, UpdateTaskInput


def _create_task(db):
    return TaskService.create_task(
        db,
        CreateTaskInput(
            title="Fix sink",
            source=TaskSource.MANUAL,
            task_status=TaskStatus.ACTIVE,
            task_mode=TaskMode.MANUAL,
            category=TaskCategory.MAINTENANCE,
            urgency=Urgency.HIGH,
            priority=TaskPriority.ROUTINE,
            confidential=False,
        ),
    )


def test_create_task_creates_backing_ai_conversation(db):
    task = _create_task(db)

    assert task.org_id == 1
    assert task.creator_id == 1
    assert task.ai_conversation_id is not None
    convo = db.get(Conversation, task.ai_conversation_id)
    assert convo.conversation_type == ConversationType.TASK_AI
    assert convo.subject == "Fix sink"


def test_update_task_status_sets_resolved_at_and_update_task_changes_mode(db):
    task = _create_task(db)

    updated = TaskService.update_task_status(db, uid=task.id, status=TaskStatus.RESOLVED)
    changed = TaskService.update_task(db, UpdateTaskInput(uid=task.id, task_mode=TaskMode.AUTONOMOUS))

    assert updated.task_status == TaskStatus.RESOLVED
    assert updated.resolved_at is not None
    assert changed.task_mode == TaskMode.AUTONOMOUS


def test_assign_vendor_and_delete_task_cleanup_related_rows(db):
    task = _create_task(db)
    vendor = User(
        org_id=1,
        creator_id=1,
        user_type="vendor",
        first_name="Assigned",
        last_name="Vendor",
        phone="+15550005555",
        role_label="Plumber",
    )
    suggestion = Suggestion(org_id=1, creator_id=1, task_id=task.id, status="pending")
    db.add_all([vendor, suggestion])
    db.flush()

    TaskService.assign_vendor_to_task(db, task_id=task.id, vendor_id=vendor.id)

    convo = db.get(Conversation, task.ai_conversation_id)
    assert convo.extra["assigned_vendor_id"] == vendor.id
    assert convo.extra["assigned_vendor_name"] == vendor.name

    assert TaskService.delete_task(db, task.id) is True
    assert db.get(type(task), task.id) is None
    assert db.get(Conversation, convo.id) is None
    assert db.get(Suggestion, suggestion.id) is None


def test_assign_vendor_rejects_unknown_vendor(db):
    task = _create_task(db)

    with pytest.raises(ValueError, match="Vendor 999 not found"):
        TaskService.assign_vendor_to_task(db, task_id=task.id, vendor_id=999)
