import pytest

from db.enums import AgentSource, AutomationSource, SuggestionOption
from db.models import Message, Suggestion
from gql.services import suggestion_service
from gql.services.suggestion_service import coerce_action_payload


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


def test_act_on_suggestion_tracks_action_and_rejects_repeat_or_unknown_action(db):
    suggestion = suggestion_service.create_suggestion(
        db,
        title="Broken sink",
        ai_context="Needs plumber",
        source=AgentSource(),
    )

    accepted = suggestion_service.act_on_suggestion(db, suggestion.id, "send_and_create_task", task_id="42")
    assert accepted.status == "accepted"
    assert accepted.task_id == 42
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
