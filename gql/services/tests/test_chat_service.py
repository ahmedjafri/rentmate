from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from db.models import (
    Conversation,
    ConversationParticipant,
    ConversationType,
    Message,
    ParticipantType,
    Property,
    Task,
    Tenant,
    Unit,
    User,
)
from gql.services import chat_service


def _conversation(db, *, convo_type=ConversationType.USER_AI, extra=None):
    convo = Conversation(
        org_id=1,
        creator_id=1,
        subject="Chat with RentMate",
        conversation_type=convo_type,
        is_group=False,
        is_archived=False,
        extra=extra,
    )
    db.add(convo)
    db.flush()
    return convo


def test_get_or_create_conversation_reuses_external_uid_and_builds_history(db):
    convo = _conversation(db)
    now = datetime.now(UTC)
    db.add_all([
        Message(
            org_id=1,
            conversation_id=convo.id,
            sender_type=ParticipantType.ACCOUNT_USER,
            body="first",
            is_ai=False,
            sent_at=now - timedelta(minutes=2),
        ),
        Message(
            org_id=1,
            conversation_id=convo.id,
            sender_type=ParticipantType.ACCOUNT_USER,
            body="second",
            is_ai=True,
            sent_at=now - timedelta(minutes=1),
        ),
    ])
    db.commit()

    reused = chat_service.get_or_create_conversation(db, uid=convo.external_id)
    history = chat_service.build_agent_message_history(
        db,
        conv_id=convo.id,
        user_message="next",
        context="system context",
    )

    assert reused.id == convo.id
    assert history == [
        {"role": "system", "content": "system context"},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "second"},
        {"role": "user", "content": "next"},
    ]


def test_persist_and_send_message_helpers_update_conversation_state(db):
    convo = _conversation(db, extra={"ai_typing": True})
    task = Task(org_id=1, creator_id=1, title="Task", ai_conversation_id=convo.id)
    db.add(task)
    db.commit()

    chat_service.persist_user_message_only(db, conv_id=convo.id, body="hello")
    db.commit()
    chat_service.persist_user_ai_messages(db, conv_id=convo.id, user_message="question", ai_reply="answer")
    sent = chat_service.send_autonomous_message(db, conversation_id=convo.id, body="autonomous", task_id=task.id)
    direct = chat_service.send_message(db, conversation_id=convo.id, body="manual")
    db.commit()
    chat_service.clear_typing_indicator(db, convo.id)

    db.refresh(task)
    db.refresh(convo)
    assert sent.is_ai is True
    assert direct.body == "manual"
    assert task.last_message_at is not None
    assert "ai_typing" not in (convo.extra or {})


def test_should_ai_respond_and_external_conversation_helpers(db):
    vendor = User(
        org_id=1,
        creator_id=1,
        user_type="vendor",
        first_name="Pat",
        last_name="Vendor",
        email="pat@example.com",
        phone="+15550006666",
        role_label="Plumber",
    )
    shadow_user = User(org_id=1, email="tenant@example.com", active=True)
    prop = Property(
        id="prop-chat",
        org_id=1,
        creator_id=1,
        address_line1="321 State St",
        property_type="multi_family",
        source="manual",
    )
    unit = Unit(id="unit-chat", org_id=1, creator_id=1, property_id=prop.id, label="2A")
    db.add_all([vendor, shadow_user, prop, unit])
    db.flush()
    tenant = Tenant(org_id=1, creator_id=1, user_id=shadow_user.id)
    db.add(tenant)
    db.flush()

    with patch("gql.services.chat_service.fetch_conversations", return_value=["ok"]):
        assert chat_service.list_conversations(db, conversation_type="vendor") == ["ok"]

    vendor_convo = chat_service.get_or_create_external_conversation(
        db,
        conversation_type=ConversationType.VENDOR,
        subject="Vendor thread",
        property_id=prop.id,
        unit_id=unit.id,
        vendor_id=vendor.id,
        ai_typing=True,
    )
    tenant_convo = chat_service.get_or_create_external_conversation(
        db,
        conversation_type=ConversationType.TENANT,
        subject="Tenant thread",
        property_id=prop.id,
        unit_id=unit.id,
        tenant_id=tenant.id,
    )

    vendor_participant = db.query(ConversationParticipant).filter_by(conversation_id=vendor_convo.id).one()
    tenant_participant = db.query(ConversationParticipant).filter_by(conversation_id=tenant_convo.id).one()

    assert vendor_participant.participant_type == ParticipantType.EXTERNAL_CONTACT
    assert tenant_participant.user_id == tenant.user_id
    assert chat_service.should_ai_respond(_conversation(db)) is True
    assert chat_service.should_ai_respond(vendor_convo) is False


def test_external_conversations_use_shadow_user_ids_for_tenant_and_vendor(db):
    vendor = User(
        org_id=1,
        creator_id=1,
        user_type="vendor",
        first_name="Vera",
        last_name="Vendor",
        phone="+15551112222",
        active=True,
    )
    tenant_user = User(org_id=1, creator_id=1, user_type="tenant", first_name="Tina", last_name="Tenant", active=True)
    db.add_all([vendor, tenant_user])
    db.flush()
    tenant = Tenant(org_id=1, creator_id=1, user_id=tenant_user.id)
    db.add(tenant)
    db.flush()

    vendor_convo = chat_service.get_or_create_external_conversation(
        db,
        conversation_type=ConversationType.VENDOR,
        subject="Vendor",
        vendor_id=vendor.id,
    )
    tenant_convo = chat_service.get_or_create_external_conversation(
        db,
        conversation_type=ConversationType.TENANT,
        subject="Tenant",
        tenant_id=tenant.id,
    )

    vendor_participant = db.query(ConversationParticipant).filter_by(conversation_id=vendor_convo.id).one()
    tenant_participant = db.query(ConversationParticipant).filter_by(conversation_id=tenant_convo.id).one()

    assert vendor_participant.user_id == vendor.id
    assert vendor_participant.participant_type == ParticipantType.EXTERNAL_CONTACT
    assert tenant_participant.user_id == tenant_user.id
    assert tenant_participant.participant_type == ParticipantType.TENANT


def test_message_and_conversation_json_payloads_are_typed():
    meta = chat_service.dump_message_meta(
        draft_reply="Draft body",
        related_task_ids={"suggestion_id": 7},
        source="quo",
        direction="inbound",
    )
    extra = chat_service.assign_conversation_vendor(None, vendor_id=12, vendor_name="Pat Vendor")

    assert meta == {
        "source": "quo",
        "direction": "inbound",
        "draft_reply": "Draft body",
        "related_task_ids": {"suggestion_id": 7},
    }
    assert extra == {"assigned_vendor_id": 12, "assigned_vendor_name": "Pat Vendor"}
