from contextlib import contextmanager

import pytest

from backends.local_auth import reset_request_context, set_request_context
from db.enums import TaskCategory, TaskMode, TaskPriority, TaskSource, TaskStatus, Urgency
from db.models import Conversation, ConversationType, Suggestion, User
from gql.services.number_allocator import NumberAllocator
from gql.services.task_service import TaskService
from gql.types import CreateTaskInput, UpdateTaskInput


def _create_task(db):
    return TaskService.create_task(
        db,
        CreateTaskInput(
            title="Fix sink",
            goal="Fix the sink and confirm the repair is complete.",
            source=TaskSource.MANUAL,
            task_status=TaskStatus.ACTIVE,
            task_mode=TaskMode.MANUAL,
            category=TaskCategory.MAINTENANCE,
            urgency=Urgency.HIGH,
            priority=TaskPriority.ROUTINE,
            confidential=False,
        ),
    )


@contextmanager
def _request_scope(*, account_id: int, org_id: int):
    token = set_request_context(account_id=account_id, org_id=org_id)
    try:
        yield
    finally:
        reset_request_context(token)


def test_create_task_creates_backing_ai_conversation(db):
    task = _create_task(db)

    assert task.org_id == 1
    assert task.creator_id == 1
    assert task.ai_conversation_id is not None
    convo = db.get(Conversation, task.ai_conversation_id)
    assert convo.conversation_type == ConversationType.TASK_AI
    assert convo.subject == "Fix sink"
    assert task.last_seen_at is not None


def test_create_task_normalizes_blank_optional_ids(db):
    task = TaskService.create_task(
        db,
        CreateTaskInput(
            title="Fix sink",
            goal="Fix the sink and confirm the repair is complete.",
            source=TaskSource.MANUAL,
            property_id="   ",
            unit_id="",
        ),
    )

    convo = db.get(Conversation, task.ai_conversation_id)
    assert task.property_id is None
    assert task.unit_id is None
    assert convo is not None
    assert convo.property_id is None
    assert convo.unit_id is None


def test_create_task_requires_non_blank_goal(db):
    with pytest.raises(ValueError, match="Task goal is required"):
        TaskService.create_task(
            db,
            CreateTaskInput(
                title="Fix sink",
                goal="   ",
                source=TaskSource.MANUAL,
            ),
        )


def test_update_task_status_sets_resolved_at_and_update_task_changes_mode(db):
    task = _create_task(db)

    updated = TaskService.update_task_status(db, uid=task.id, status=TaskStatus.RESOLVED)
    changed = TaskService.update_task(db, UpdateTaskInput(uid=task.id, task_mode=TaskMode.AUTONOMOUS))

    assert updated.task_status == TaskStatus.RESOLVED
    assert updated.resolved_at is not None
    assert changed.task_mode == TaskMode.AUTONOMOUS
    assert changed.updated_at is not None


def test_mark_task_seen_updates_last_seen_at(db):
    task = _create_task(db)
    previous_seen_at = task.last_seen_at

    changed = TaskService.mark_task_seen(db, uid=task.id)

    assert changed.last_seen_at is not None
    assert previous_seen_at is None or changed.last_seen_at >= previous_seen_at


def test_create_task_normalizes_lowercase_task_mode(db):
    task = TaskService.create_task(
        db,
        CreateTaskInput(
            title="Fix sink",
            goal="Fix the sink and confirm the repair is complete.",
            source=TaskSource.MANUAL,
            task_mode="autonomous",
        ),
    )

    assert task.task_mode == TaskMode.AUTONOMOUS


def test_update_task_normalizes_lowercase_task_mode(db):
    task = _create_task(db)

    changed = TaskService.update_task(db, UpdateTaskInput(uid=task.id, task_mode="waiting_approval"))

    assert changed.task_mode == TaskMode.WAITING_APPROVAL


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
    suggestion_id = NumberAllocator.allocate_next(db, entity_type="suggestion", org_id=1)
    suggestion = Suggestion(id=suggestion_id, org_id=1, creator_id=1, task_id=task.id, status="pending")
    db.add_all([vendor, suggestion])
    db.flush()

    TaskService.assign_vendor_to_task(db, task_id=task.id, vendor_id=vendor.id)

    convo = db.get(Conversation, task.ai_conversation_id)
    assert convo.extra["assigned_vendor_id"] == vendor.id
    assert convo.extra["assigned_vendor_name"] == vendor.name

    assert TaskService.delete_task(db, task.id) is True
    assert db.get(type(task), (task.org_id, task.id)) is None
    assert db.get(Conversation, convo.id) is None
    assert db.get(Suggestion, (suggestion.org_id, suggestion.id)) is None


def test_assign_vendor_rejects_unknown_vendor(db):
    task = _create_task(db)

    with pytest.raises(ValueError, match="Vendor 999 not found"):
        TaskService.assign_vendor_to_task(db, task_id=task.id, vendor_id=999)


def test_update_task_status_rejects_task_from_other_org(db):
    foreign_user = User(id=2, org_id=2, email="other-org@example.com", active=True)
    db.add(foreign_user)
    db.flush()

    with _request_scope(account_id=2, org_id=2):
        foreign_task = TaskService.create_task(
            db,
            CreateTaskInput(title="Foreign task", goal="Complete the foreign task.", source=TaskSource.MANUAL),
        )

    with pytest.raises(ValueError, match=f"Task {foreign_task.id} not found"):
        TaskService.update_task_status(db, uid=foreign_task.id, status=TaskStatus.RESOLVED)


def test_assign_vendor_rejects_vendor_from_other_org(db):
    task = _create_task(db)
    foreign_vendor = User(
        id=2,
        org_id=2,
        creator_id=2,
        user_type="vendor",
        first_name="Other",
        last_name="Vendor",
        phone="+15550009999",
        role_label="Plumber",
        active=True,
    )
    foreign_creator = User(id=3, org_id=2, email="org2-admin@example.com", active=True)
    db.add_all([foreign_creator, foreign_vendor])
    db.flush()

    with pytest.raises(ValueError, match=f"Vendor {foreign_vendor.id} not found"):
        TaskService.assign_vendor_to_task(db, task_id=task.id, vendor_id=foreign_vendor.id)
