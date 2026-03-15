# test_db_lib.py

import pytest

from db.lib import (
    get_or_create_tenant_by_phone,
    get_or_create_conversation_for_tenant,
    add_message,
    list_conversations,
    get_conversation_with_messages,
)
from db.models import (
    Tenant,
    ConversationParticipant,
    ParticipantType,
    Message,
    MessageReceipt,
)
from db.utils import normalize_phone


# ---------------------------
# Tests
# ---------------------------

def test_get_or_create_tenant_by_phone_creates_and_reuses(db):
    phone = "+15550001111"

    t1 = get_or_create_tenant_by_phone(
        db=db,
        phone=phone,
        first_name="John",
        last_name="Doe",
    )
    assert isinstance(t1, Tenant)
    assert t1.phone == phone

    # Should reuse
    t2 = get_or_create_tenant_by_phone(
        db=db,
        phone=phone,
        first_name="Jane",
        last_name="Smith",
    )

    assert t2.id == t1.id
    assert db.query(Tenant).filter_by(phone=phone).count() == 1


def test_get_or_create_conversation_for_tenant_creates_and_reuses(db):
    tenant = get_or_create_tenant_by_phone(
        db=db,
        phone="+15550002222",
        first_name="Alice",
        last_name="Renter",
    )

    conv1 = get_or_create_conversation_for_tenant(
        db=db,
        tenant=tenant,
        subject="Initial conversation",
    )

    assert conv1.participants
    assert len(conv1.participants) == 1

    # Reuse
    conv2 = get_or_create_conversation_for_tenant(
        db=db,
        tenant=tenant,
    )

    assert conv2.id == conv1.id


def test_add_message_creates_message_and_receipts(db):
    tenant = get_or_create_tenant_by_phone(
        db=db,
        phone="+15550003333",
        first_name="Bob",
        last_name="Tenant",
    )

    conv = get_or_create_conversation_for_tenant(db=db, tenant=tenant)

    # Add another active participant
    extra_participant = ConversationParticipant(
        conversation_id=conv.id,
        participant_type=ParticipantType.EXTERNAL_CONTACT,
        is_active=True,
    )
    db.add(extra_participant)
    db.flush()

    msg = add_message(
        db=db,
        conversation=conv,
        sender_type=ParticipantType.TENANT,
        body="Hello world",
        sender_tenant=tenant,
        meta={"x": 1},
    )

    db_msg = db.query(Message).filter_by(id=msg.id).one()
    assert db_msg.body == "Hello world"

    receipts = db.query(MessageReceipt).filter_by(message_id=msg.id).all()
    active_p = [p for p in conv.participants if p.is_active]
    assert len(receipts) == len(active_p)


def test_list_conversations_and_get_with_messages(db):
    tenant = get_or_create_tenant_by_phone(
        db=db,
        phone="+15550004444",
        first_name="Carol",
        last_name="Tenant",
    )

    conv = get_or_create_conversation_for_tenant(db=db, tenant=tenant)

    add_message(
        db=db,
        conversation=conv,
        sender_type=ParticipantType.TENANT,
        body="First msg",
        sender_tenant=tenant,
    )
    add_message(
        db=db,
        conversation=conv,
        sender_type=ParticipantType.TENANT,
        body="Second msg",
        sender_tenant=tenant,
    )

    convs = list_conversations(db=db)
    assert any(c.id == conv.id for c in convs)

    loaded = get_conversation_with_messages(db=db, conversation_id=conv.id)

    assert loaded is not None
    assert len(loaded.messages) == 2
    assert [m.body for m in loaded.messages] == ["First msg", "Second msg"]


def test_record_sms_from_dialpad(db):
    from unittest.mock import patch, MagicMock
    from db.lib import record_sms_from_dialpad

    from_number = normalize_phone("+15550005555")
    to_number = normalize_phone("+15559990000")

    tenant = Tenant(
        first_name="Test",
        last_name="Tenant",
        email=None,
        phone=from_number,
    )
    db.add(tenant)
    db.flush()

    # Mock the sms_router to return our tenant
    mock_router = MagicMock()
    mock_router.resolve.return_value = ("default-account", tenant, "inbound")

    with patch("backends.wire.sms_router", mock_router):
        body = "Inbound test"
        msg, conv = record_sms_from_dialpad(
            db=db,
            from_number=from_number,
            to_number=to_number,
            body=body,
        )

    assert msg is not None
    assert msg.body == body
    assert msg.meta["source"] == "dialpad"
    assert msg.meta["direction"] == "inbound"
    assert msg.sender_tenant_id == tenant.id

    # Inbound SMS should route to a task conversation
    assert conv is not None
    assert conv.is_task is True
    assert conv.task_status == "active"
    assert conv.channel_type == "sms"

    receipts = db.query(MessageReceipt).filter_by(message_id=msg.id).all()
    assert len(receipts) >= 1
