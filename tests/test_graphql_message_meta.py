from datetime import UTC, datetime

from db.models import (
    Conversation,
    ConversationParticipant,
    ConversationType,
    Message,
    MessageReceipt,
    MessageType,
    ParticipantType,
    Tenant,
    User,
)
from gql.schema import schema
from integrations.local_auth import reset_request_context, set_request_context

FAKE_USER = {"id": 1, "uid": "user-external-123", "email": "admin@test.com"}


def _gql_context(db):
    return {"db_session": db, "user": FAKE_USER}


def test_conversation_messages_returns_suggestion_fields_for_reviewable_reply(db):
    token = set_request_context(account_id=1, org_id=1)
    try:
        user = User(
            org_id=1,
            creator_id=1,
            first_name="Bob",
            last_name="Ferguson",
            email="bob@example.com",
            active=True,
        )
        db.add(user)
        db.flush()

        tenant = Tenant(
            org_id=1,
            creator_id=1,
            user_id=user.id,
            created_at=datetime.now(UTC),
        )
        db.add(tenant)
        db.flush()

        conv = Conversation(
            org_id=1,
            creator_id=1,
            subject="Conversation with Bob Ferguson",
            is_group=False,
            is_archived=False,
            conversation_type=ConversationType.TENANT,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.add(conv)
        db.flush()

        db.add(ConversationParticipant(
            org_id=1,
            creator_id=1,
            conversation_id=conv.id,
            user_id=tenant.user_id,
            participant_type=ParticipantType.TENANT,
            is_active=True,
        ))
        db.add(Message(
            org_id=1,
            conversation_id=conv.id,
            sender_type=ParticipantType.ACCOUNT_USER,
            sender_name="RentMate",
            body="Suggested reply for Bob Ferguson.",
            message_type=MessageType.SUGGESTION,
            is_ai=True,
            meta={
                "source": "dev_sim",
                "direction": "inbound",
                "draft_reply": "Thanks for reaching out. I'll check and follow up shortly.",
                "related_task_ids": {"suggestion_id": 7},
            },
            sent_at=datetime.now(UTC),
        ))
        db.commit()

        result = schema.execute_sync(
            """
            query ConversationMessages($uid: String!) {
              conversationMessages(uid: $uid) {
                uid
                body
                messageType
                draftReply
                suggestionId
                senderName
                senderType
              }
            }
            """,
            variable_values={"uid": str(conv.external_id)},
            context_value=_gql_context(db),
        )

        assert result.errors is None, result.errors
        messages = result.data["conversationMessages"]
        assert len(messages) == 1
        assert messages[0]["body"] == "Suggested reply for Bob Ferguson."
        assert messages[0]["messageType"] == "SUGGESTION"
        assert messages[0]["draftReply"] == "Thanks for reaching out. I'll check and follow up shortly."
        assert messages[0]["suggestionId"] == "7"
        assert messages[0]["senderName"] == "RentMate"
    finally:
        reset_request_context(token)


def test_conversation_summary_returns_unread_external_message_count(db):
    manager_participant, tenant_participant, conv = _conversation_with_manager_and_tenant(db)
    msg = Message(
        org_id=1,
        conversation_id=conv.id,
        sender_type=ParticipantType.TENANT,
        sender_id=tenant_participant.id,
        sender_name="Bob Ferguson",
        body="Can you take a look?",
        message_type=MessageType.MESSAGE,
        is_system=False,
        sent_at=datetime.now(UTC),
    )
    db.add(msg)
    db.flush()
    db.add(MessageReceipt(
        org_id=1,
        conversation_id=conv.id,
        message_id=msg.id,
        conversation_participant_id=manager_participant.id,
        delivered_at=datetime.now(UTC),
        read_at=None,
    ))
    db.flush()

    result = schema.execute_sync(
        """
        query Conversation($uid: String!) {
          conversation(uid: $uid) {
            uid
            unreadCount
          }
        }
        """,
        variable_values={"uid": str(conv.external_id)},
        context_value=_gql_context(db),
    )

    assert result.errors is None, result.errors
    assert result.data["conversation"]["unreadCount"] == 1


def test_mark_conversation_seen_clears_unread_external_messages(db):
    manager_participant, tenant_participant, conv = _conversation_with_manager_and_tenant(db)
    msg = Message(
        org_id=1,
        conversation_id=conv.id,
        sender_type=ParticipantType.TENANT,
        sender_id=tenant_participant.id,
        sender_name="Bob Ferguson",
        body="Can you take a look?",
        message_type=MessageType.MESSAGE,
        is_system=False,
        sent_at=datetime.now(UTC),
    )
    db.add(msg)
    db.flush()
    receipt = MessageReceipt(
        org_id=1,
        conversation_id=conv.id,
        message_id=msg.id,
        conversation_participant_id=manager_participant.id,
        delivered_at=datetime.now(UTC),
        read_at=None,
    )
    db.add(receipt)
    db.flush()

    result = schema.execute_sync(
        """
        mutation MarkConversationSeen($uid: String!) {
          markConversationSeen(uid: $uid) {
            uid
            unreadCount
          }
        }
        """,
        variable_values={"uid": str(conv.external_id)},
        context_value=_gql_context(db),
    )

    assert result.errors is None, result.errors
    assert result.data["markConversationSeen"]["unreadCount"] == 0
    db.refresh(receipt)
    assert receipt.read_at is not None


def test_legacy_conversation_without_manager_participant_still_reports_unread(db):
    _, tenant_participant, conv = _conversation_with_manager_and_tenant(db, include_manager=False)
    msg = Message(
        org_id=1,
        conversation_id=conv.id,
        sender_type=ParticipantType.TENANT,
        sender_id=tenant_participant.id,
        sender_name="Bob Ferguson",
        body="Legacy unread message",
        message_type=MessageType.MESSAGE,
        is_system=False,
        sent_at=datetime.now(UTC),
    )
    db.add(msg)
    db.flush()

    result = schema.execute_sync(
        """
        query Conversation($uid: String!) {
          conversation(uid: $uid) {
            uid
            unreadCount
          }
        }
        """,
        variable_values={"uid": str(conv.external_id)},
        context_value=_gql_context(db),
    )

    assert result.errors is None, result.errors
    assert result.data["conversation"]["unreadCount"] == 1


def test_mark_seen_repairs_legacy_conversation_without_manager_receipts(db):
    _, tenant_participant, conv = _conversation_with_manager_and_tenant(db, include_manager=False)
    msg = Message(
        org_id=1,
        conversation_id=conv.id,
        sender_type=ParticipantType.TENANT,
        sender_id=tenant_participant.id,
        sender_name="Bob Ferguson",
        body="Legacy unread message",
        message_type=MessageType.MESSAGE,
        is_system=False,
        sent_at=datetime.now(UTC),
    )
    db.add(msg)
    db.flush()

    result = schema.execute_sync(
        """
        mutation MarkConversationSeen($uid: String!) {
          markConversationSeen(uid: $uid) {
            uid
            unreadCount
          }
        }
        """,
        variable_values={"uid": str(conv.external_id)},
        context_value=_gql_context(db),
    )

    assert result.errors is None, result.errors
    assert result.data["markConversationSeen"]["unreadCount"] == 0
    receipt = db.query(MessageReceipt).filter_by(message_id=msg.id).one()
    assert receipt.read_at is not None


def _conversation_with_manager_and_tenant(db, *, include_manager=True):
    tenant_user = User(
        org_id=1,
        creator_id=1,
        first_name="Bob",
        last_name="Ferguson",
        email="bob@example.com",
        active=True,
    )
    db.add(tenant_user)
    db.flush()
    tenant = Tenant(
        org_id=1,
        creator_id=1,
        user_id=tenant_user.id,
        created_at=datetime.now(UTC),
    )
    db.add(tenant)
    db.flush()
    conv = Conversation(
        org_id=1,
        creator_id=1,
        subject="Conversation with Bob Ferguson",
        is_group=False,
        is_archived=False,
        conversation_type=ConversationType.TENANT,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(conv)
    db.flush()
    manager_participant = None
    if include_manager:
        manager_participant = ConversationParticipant(
            org_id=1,
            creator_id=1,
            conversation_id=conv.id,
            user_id=1,
            participant_type=ParticipantType.ACCOUNT_USER,
            is_active=True,
        )
    tenant_participant = ConversationParticipant(
        org_id=1,
        creator_id=1,
        conversation_id=conv.id,
        user_id=tenant.user_id,
        participant_type=ParticipantType.TENANT,
        is_active=True,
    )
    participants = [tenant_participant]
    if manager_participant is not None:
        participants.append(manager_participant)
    db.add_all(participants)
    db.flush()
    return manager_participant, tenant_participant, conv
