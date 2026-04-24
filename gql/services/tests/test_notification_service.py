import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from backends.local_auth import set_request_context
from db.models import (
    Conversation,
    ConversationParticipant,
    ConversationType,
    ParticipantType,
    Tenant,
    User,
)
from gql.services.notification_service import Notification, NotificationService


@pytest.fixture(autouse=True)
def _request_scope():
    set_request_context(account_id=1, org_id=1)
    yield


def _seed_tenant(db) -> tuple[Tenant, User]:
    tenant_user = User(
        id=77,
        org_id=1,
        creator_id=1,
        user_type="tenant",
        first_name="Riley",
        last_name="Renter",
        phone="+15555550100",
        active=True,
    )
    db.add(tenant_user)
    db.flush()
    tenant = Tenant(org_id=1, creator_id=1, user_id=tenant_user.id)
    db.add(tenant)
    db.flush()
    return tenant, tenant_user


def _seed_conversation(db, tenant_user: User) -> Conversation:
    convo = Conversation(
        org_id=1,
        creator_id=1,
        subject="Pipe leak",
        conversation_type=ConversationType.TENANT,
        is_group=False,
        is_archived=False,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(convo)
    db.flush()
    db.add(ConversationParticipant(
        org_id=1,
        creator_id=1,
        conversation_id=convo.id,
        user_id=tenant_user.id,
        participant_type=ParticipantType.TENANT,
        is_active=True,
    ))
    db.flush()
    return convo


def test_notify_sends_sms_with_blurb_messages_and_login_less_link(db):
    tenant, tenant_user = _seed_tenant(db)
    convo = _seed_conversation(db, tenant_user)

    sent: list[tuple[str, str]] = []

    async def _fake_send(to_phone: str, body: str) -> None:
        sent.append((to_phone, body))

    with patch.object(NotificationService, "_send_sms", AsyncMock(side_effect=_fake_send)):
        delivered = asyncio.run(NotificationService.notify(
            db,
            Notification(
                recipient_user_id=tenant_user.id,
                conversation_id=convo.id,
                blurb="RentMate update: Pipe leak",
                messages=["We have a plumber scheduled Thursday 10am."],
            ),
        ))

    assert delivered is True
    assert len(sent) == 1
    to_phone, body = sent[0]
    assert to_phone == tenant_user.phone
    assert "RentMate update: Pipe leak" in body
    assert "We have a plumber scheduled Thursday 10am." in body
    # Login-less portal link deep-links to this conversation.
    assert f"conv={convo.external_id}" in body
    assert "/t/" in body


def test_dispatch_falls_back_silently_when_recipient_has_no_phone(db):
    # Tenant without a phone — no delivery, no exception.
    tenant_user = User(
        id=78,
        org_id=1,
        creator_id=1,
        user_type="tenant",
        first_name="Sky",
        last_name="Noah",
        active=True,
    )
    db.add(tenant_user)
    db.flush()
    db.add(Tenant(org_id=1, creator_id=1, user_id=tenant_user.id))
    db.flush()
    convo = _seed_conversation(db, tenant_user)

    with patch.object(NotificationService, "_send_sms", AsyncMock()) as send:
        NotificationService.dispatch(
            db,
            Notification(
                recipient_user_id=tenant_user.id,
                conversation_id=convo.id,
                blurb="Anything",
                messages=["Anything"],
            ),
        )
    send.assert_not_called()
