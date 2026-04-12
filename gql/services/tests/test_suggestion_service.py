import pytest

from backends.local_auth import reset_request_context, set_request_context
from db.enums import AgentSource, AutomationSource, SuggestionOption, Urgency
from db.models import Message, Suggestion, Task, User
from gql.services import suggestion_service
from gql.services.suggestion_service import coerce_action_payload
from gql.services.task_suggestions import SuggestionExecutor


def _request_scope(*, account_id: int, org_id: int):
    token = set_request_context(account_id=account_id, org_id=org_id)
    return token


def test_create_suggestion_creates_ai_conversation_and_context_message(db):
    suggestion = suggestion_service.create_suggestion(
        db,
        title="Lease expiring",
        ai_context="Jane Doe renews soon",
        category="leasing",
        urgency=2,
        source=AutomationSource(automation_key="lease-expiry"),
        options=[SuggestionOption(key="send", label="Create Task", action="send_and_create_task", variant="default")],
        action_payload={"vendor_id": "vendor-ext-1"},
        suggestion_type="leasing",
        risk_score=7,
    )

    assert suggestion.org_id == 1
    assert suggestion.creator_id == 1
    assert suggestion.source == "automation"
    assert suggestion.automation_key == "lease-expiry"
    assert suggestion.ai_conversation_id is not None
    ctx_msg = db.query(Message).filter_by(conversation_id=suggestion.ai_conversation_id).one()
    assert ctx_msg.body == "Jane Doe renews soon"


def test_create_suggestion_coerces_lowercase_urgency(db):
    suggestion = suggestion_service.create_suggestion(
        db,
        title="Lease expiring",
        ai_context="Jane Doe renews soon",
        source=AgentSource(),
        urgency="low",
    )

    assert suggestion.urgency == Urgency.LOW


def test_act_on_suggestion_tracks_action_and_rejects_repeat_or_unknown_action(db):
    suggestion = suggestion_service.create_suggestion(
        db,
        title="Broken sink",
        ai_context="Needs plumber",
        source=AgentSource(),
    )
    task = Task(org_id=1, creator_id=1, title="Follow up task")
    db.add(task)
    db.flush()

    accepted = suggestion_service.act_on_suggestion(
        db,
        suggestion.id,
        "send_and_create_task",
        task_id=str(task.id),
    )
    assert accepted.status == "accepted"
    assert accepted.task_id == task.id
    assert accepted.action_taken == "send_and_create_task"

    with pytest.raises(ValueError, match="already accepted"):
        suggestion_service.act_on_suggestion(db, suggestion.id, "reject_task")

    other = suggestion_service.create_suggestion(db, title="Other", ai_context="", source=AgentSource())
    with pytest.raises(ValueError, match="Unknown action"):
        suggestion_service.act_on_suggestion(db, other.id, "unknown")


def test_get_suggestions_filters_by_status_and_scope(db):
    pending = suggestion_service.create_suggestion(db, title="Pending", ai_context="", source=AgentSource())
    accepted = suggestion_service.create_suggestion(db, title="Accepted", ai_context="", source=AgentSource())
    suggestion_service.act_on_suggestion(db, accepted.id, "reject_task")

    pending_items = suggestion_service.get_suggestions(db, status="pending")
    all_items = suggestion_service.get_suggestions(db)

    assert [item.id for item in pending_items] == [pending.id]
    assert {item.id for item in all_items} == {pending.id, accepted.id}
    assert all(isinstance(item, Suggestion) for item in all_items)


def test_get_suggestions_excludes_other_org_rows(db):
    visible = suggestion_service.create_suggestion(
        db,
        title="Visible",
        ai_context="",
        source=AgentSource(),
    )
    foreign_creator = User(id=2, org_id=2, email="org2-admin@example.com", active=True)
    db.add(foreign_creator)
    db.flush()
    db.add(Suggestion(org_id=2, creator_id=2, title="Hidden", status="pending"))
    db.flush()

    visible_ids = [item.id for item in suggestion_service.get_suggestions(db)]

    assert visible_ids == [visible.id]


def test_act_on_suggestion_rejects_other_org_row(db):
    foreign_creator = User(id=2, org_id=2, email="org2-admin@example.com", active=True)
    db.add(foreign_creator)
    db.flush()
    foreign = Suggestion(org_id=2, creator_id=2, title="Hidden", status="pending")
    db.add(foreign)
    db.flush()

    with pytest.raises(ValueError, match=f"Suggestion {foreign.id} not found"):
        suggestion_service.act_on_suggestion(db, foreign.id, "reject_task")


def test_executor_for_suggestion_rejects_other_org_row(db):
    foreign_creator = User(id=2, org_id=2, email="org2-admin@example.com", active=True)
    db.add(foreign_creator)
    db.flush()

    token = _request_scope(account_id=2, org_id=2)
    try:
        foreign = suggestion_service.create_suggestion(
            db,
            title="Hidden",
            ai_context="",
            source=AgentSource(),
        )
    finally:
        reset_request_context(token)

    with pytest.raises(ValueError, match=f"Suggestion {foreign.id} not found"):
        SuggestionExecutor.for_suggestion(db, foreign.id)


def test_action_payload_is_coerced_to_typed_shape(db):
    suggestion = suggestion_service.create_suggestion(
        db,
        title="Need vendor",
        ai_context="Find vendor",
        source=AgentSource(),
        action_payload={"vendor_id": "vendor-ext-1", "vendor_name": "Bob", "draft_message": "hello"},
    )

    payload = coerce_action_payload(suggestion.action_payload)
    assert payload == {
        "action": "send_and_create_task",
        "vendor_id": "vendor-ext-1",
        "vendor_name": "Bob",
        "draft_message": "hello",
    }
