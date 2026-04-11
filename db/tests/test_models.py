from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from db.models import (
    Conversation,
    ConversationParticipant,
    Lease,
    Message,
    MessageReceipt,
    ParticipantType,
    Property,
    Tenant,
    Unit,
    User,
)


def _mk_shadow_user(db, *, first_name="Alice", last_name="Test", phone="+15550001234"):
    user = User(
        org_id=1,
        first_name=first_name,
        last_name=last_name,
        phone=phone,
        active=True,
    )
    db.add(user)
    db.flush()
    return user


def _mk_property_unit(db, label="Unit 101"):
    prop = Property(
        org_id=1,
        creator_id=1,
        name="Test Property",
        address_line1="123 Main St",
        city="Seattle",
        state="WA",
        postal_code="98101",
    )
    db.add(prop)
    db.flush()

    unit = Unit(
        org_id=1,
        creator_id=1,
        property_id=prop.id,
        label=label,
    )
    db.add(unit)
    db.flush()
    return prop, unit


def _mk_tenant(db, first="Alice", last="Test", phone="+15550001234"):
    shadow_user = _mk_shadow_user(db, first_name=first, last_name=last, phone=phone)
    tenant = Tenant(
        org_id=1,
        creator_id=1,
        user_id=shadow_user.id,
    )
    db.add(tenant)
    db.flush()
    return tenant


def test_create_property_and_unit(db):
    prop, unit = _mk_property_unit(db)

    assert prop.id is not None
    assert unit.property_id == prop.id
    assert unit.creator_id == 1


def test_create_tenant_and_lease(db):
    prop, unit = _mk_property_unit(db)
    tenant = _mk_tenant(db)

    lease = Lease(
        org_id=1,
        creator_id=1,
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
    now = datetime.now(UTC)

    conv = Conversation(
        org_id=1,
        creator_id=1,
        subject="Test conv",
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
        body="Hello!",
        sent_at=now,
    )
    db.add(msg)
    db.flush()

    receipt = MessageReceipt(
        org_id=1,
        conversation_id=conv.id,
        message_id=msg.id,
        conversation_participant_id=participant.id,
    )
    db.add(receipt)
    db.flush()

    loaded = db.execute(select(Message).where(Message.id == msg.id)).scalar_one()
    assert loaded.body == "Hello!"


def test_vendor_shadow_user(db):
    contact = User(
        org_id=1,
        creator_id=1,
        user_type="vendor",
        first_name="Bob",
        last_name="Plumber",
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
        org_id=1,
        creator_id=1,
        tenant_id=tenant.id,
        unit_id=unit.id,
        property_id=prop.id,
        start_date=date.today(),
        end_date=date.today() + timedelta(days=365),
        rent_amount=1800.0,
    )
    db.add(lease)
    db.flush()

    db.expire_all()
    loaded_tenant = db.get(Tenant, tenant.id)

    assert len(loaded_tenant.units) == 1
    assert loaded_tenant.units[0].id == unit.id
