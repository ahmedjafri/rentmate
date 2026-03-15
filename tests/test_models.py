# tests/test_models.py
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from db.models import (
    Property,
    Unit,
    Tenant,
    Lease,
    Conversation,
    ConversationParticipant,
    Message,
    MessageReceipt,
    ExternalContact,
    ParticipantType,
)


def _mk_property_unit(db, label="Unit 101"):
    prop = Property(
        name="Test Property",
        address_line1="123 Main St",
        city="Seattle",
        state="WA",
        postal_code="98101",
    )
    db.add(prop)
    db.flush()

    unit = Unit(property_id=prop.id, label=label)
    db.add(unit)
    db.flush()
    return prop, unit


def _mk_tenant(db, first="Alice", last="Test", phone="+15550001234"):
    t = Tenant(first_name=first, last_name=last, phone=phone)
    db.add(t)
    db.flush()
    return t


def test_create_property_and_unit(db):
    prop, unit = _mk_property_unit(db)
    assert prop.id is not None
    assert unit.property_id == prop.id


def test_create_tenant_and_lease(db):
    prop, unit = _mk_property_unit(db)
    tenant = _mk_tenant(db)

    lease = Lease(
        tenant_id=tenant.id,
        unit_id=unit.id,
        property_id=prop.id,
        start_date=date.today(),
        end_date=date.today() + timedelta(days=365),
        rent_amount=1500.0,
    )
    db.add(lease)
    db.flush()

    assert lease.id is not None
    assert lease.tenant_id == tenant.id
    assert lease.unit_id == unit.id


def test_conversation_and_messaging(db):
    tenant = _mk_tenant(db)

    conv = Conversation(subject="Test conv")
    db.add(conv)
    db.flush()

    participant = ConversationParticipant(
        conversation_id=conv.id,
        participant_type=ParticipantType.TENANT,
        tenant_id=tenant.id,
        is_active=True,
    )
    db.add(participant)
    db.flush()

    msg = Message(
        conversation_id=conv.id,
        sender_type=ParticipantType.TENANT,
        sender_tenant_id=tenant.id,
        body="Hello!",
    )
    db.add(msg)
    db.flush()

    receipt = MessageReceipt(
        message_id=msg.id,
        conversation_participant_id=participant.id,
    )
    db.add(receipt)
    db.flush()

    loaded = db.execute(select(Message).where(Message.id == msg.id)).scalar_one()
    assert loaded.body == "Hello!"


def test_external_contact(db):
    contact = ExternalContact(
        name="Bob Plumber",
        phone="+15550009999",
        role_label="plumber",
    )
    db.add(contact)
    db.flush()
    assert contact.id is not None


def test_tenant_units_property(db):
    prop, unit = _mk_property_unit(db)
    tenant = _mk_tenant(db)

    lease = Lease(
        tenant_id=tenant.id,
        unit_id=unit.id,
        property_id=prop.id,
        start_date=date.today(),
        end_date=date.today() + timedelta(days=365),
        rent_amount=1800.0,
    )
    db.add(lease)
    db.flush()

    # Reload with relationships
    db.expire_all()
    t = db.get(Tenant, tenant.id)
    assert len(t.units) == 1
    assert t.units[0].id == unit.id
