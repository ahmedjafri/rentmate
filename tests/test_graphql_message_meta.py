from datetime import UTC, datetime

from backends.local_auth import reset_request_context, set_request_context
from db.models import Conversation, ConversationParticipant, ConversationType, Message, MessageType, ParticipantType, Tenant, User
from gql.schema import schema


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
