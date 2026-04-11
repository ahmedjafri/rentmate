from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from db.lib import (
    _address_similarity,
    _normalize_address,
    find_candidate_properties,
    list_conversations,
    record_sms_from_quo,
)
from db.models import (
    Conversation,
    ConversationParticipant,
    Message,
    ParticipantType,
    Property,
    Tenant,
    User,
)


def _mk_tenant(db):
    shadow_user = User(
        org_id=1,
        first_name="Alice",
        last_name="Tenant",
        phone="+15550002222",
        active=True,
    )
    db.add(shadow_user)
    db.flush()

    tenant = Tenant(
        org_id=1,
        creator_id=1,
        user_id=shadow_user.id,
    )
    db.add(tenant)
    db.flush()
    return tenant


def _mk_conversation_with_message(db):
    tenant = _mk_tenant(db)
    now = datetime.now(UTC)

    conv = Conversation(
        org_id=1,
        creator_id=1,
        subject="Conversation with tenant",
        created_at=now,
        updated_at=now,
    )
    db.add(conv)
    db.flush()

    participant = ConversationParticipant(
        org_id=1,
        creator_id=1,
        conversation_id=conv.id,
        user_id=tenant.user_id,
        participant_type=ParticipantType.TENANT,
        is_active=True,
        joined_at=now,
    )
    db.add(participant)
    db.flush()

    msg = Message(
        org_id=1,
        conversation_id=conv.id,
        sender_type=ParticipantType.TENANT,
        sender_id=participant.id,
        body="Hello world",
        sent_at=now,
    )
    db.add(msg)
    db.flush()

    return conv, participant, msg


def test_normalize_address_strips_us_suffix_and_standardizes_tokens():
    assert _normalize_address("123 Main Street, Seattle, WA 98101 USA") == "123 MAIN ST SEATTLE WA 98101"


def test_address_similarity_scores_partial_overlap():
    a = _normalize_address("123 Main Street Seattle WA 98101")
    b = _normalize_address("123 Main St Seattle WA")

    assert _address_similarity(a, b) > 0.7


def test_find_candidate_properties_returns_ranked_matches(db):
    db.add_all(
        [
            Property(
                org_id=1,
                creator_id=1,
                name="Main",
                address_line1="123 Main Street",
                city="Seattle",
                state="WA",
                postal_code="98101",
            ),
            Property(
                org_id=1,
                creator_id=1,
                name="Other",
                address_line1="999 Pine Street",
                city="Seattle",
                state="WA",
                postal_code="98109",
            ),
        ]
    )
    db.flush()

    matches = find_candidate_properties(db, address="123 Main St, Seattle WA 98101")

    assert len(matches) == 1
    assert matches[0]["name"] == "Main"
    assert matches[0]["score"] == 1.0


def test_list_conversations_returns_newest_first(db):
    older = datetime.now(UTC) - timedelta(hours=1)
    newer = datetime.now(UTC)

    conv1, _, _ = _mk_conversation_with_message(db)
    conv1.updated_at = older

    conv2, _, msg2 = _mk_conversation_with_message(db)
    conv2.subject = "Newest"
    conv2.updated_at = newer
    msg2.body = "Newest message"
    db.flush()

    convs = list_conversations(db=db)
    assert [c.id for c in convs[:2]] == [conv2.id, conv1.id]
    assert convs[0].subject == "Newest"


def test_record_sms_from_quo_routes_inbound_messages(db):
    tenant = SimpleNamespace(id=7, first_name="Test", last_name="Tenant")
    conv = SimpleNamespace(id="conv-1")
    msg = SimpleNamespace(id="msg-1", conversation_id="conv-1")
    mock_router = MagicMock()
    mock_router.resolve.return_value = ("default-account", tenant, "inbound")

    with (
        patch("backends.wire.sms_router", mock_router),
        patch("db.lib.route_inbound_to_task", return_value=(conv, msg)) as route_mock,
    ):
        result_msg, result_conv = record_sms_from_quo(
            db=db,
            from_number="+15550005555",
            to_number="+15559990000",
            body="Inbound test",
        )

    assert result_msg is msg
    assert result_conv is conv
    route_mock.assert_called_once()


def test_record_sms_from_quo_records_outbound_messages_without_routing(db):
    tenant = SimpleNamespace(id=7, first_name="Test", last_name="Tenant")
    conv = SimpleNamespace(id="conv-1")
    msg = SimpleNamespace(id="msg-1", body="Outbound test")
    mock_router = MagicMock()
    mock_router.resolve.return_value = ("default-account", tenant, "outbound")

    with (
        patch("backends.wire.sms_router", mock_router),
        patch("db.lib.get_or_create_conversation_for_tenant", return_value=conv) as conv_mock,
        patch("db.lib.add_message", return_value=msg) as add_message_mock,
    ):
        result_msg, result_conv = record_sms_from_quo(
            db=db,
            from_number="+15559990000",
            to_number="+15550005555",
            body="Outbound test",
        )

    assert result_msg is msg
    assert result_conv is None
    conv_mock.assert_called_once_with(db=db, tenant=tenant)
    add_message_mock.assert_called_once()
    assert add_message_mock.call_args.kwargs["sender_type"] is ParticipantType.ACCOUNT_USER


def test_record_sms_from_quo_returns_none_when_router_cannot_resolve(db):
    mock_router = MagicMock()
    mock_router.resolve.return_value = None

    with patch("backends.wire.sms_router", mock_router):
        result = record_sms_from_quo(
            db=db,
            from_number="+15550005555",
            to_number="+15559990000",
            body="Unknown sender",
        )

    assert result == (None, None)
