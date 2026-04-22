"""Dev seed data — creates a realistic portfolio on first startup.

Guarded by a sentinel user (dev-seed@rentmate.local). Safe to call on every
boot; idempotent after the first run.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from backends.local_auth import resolve_account_id, resolve_org_id
from db.models import (
    Conversation,
    ConversationParticipant,
    ConversationType,
    Message,
    MessageType,
    ParticipantType,
    Task,
    TaskNumberSequence,
    User,
)
from db.models.rental import Lease, Property, Tenant, Unit

_SENTINEL_EMAIL = "dev-seed@rentmate.local"

# ── data constants ──────────────────────────────────────────────────────

PROPERTIES = [
    {
        "name": "The Meadows",
        "address_line1": "1842 Meadow Lane",
        "city": "Bellevue", "state": "WA", "postal_code": "98004",
        "units": ["Unit 1A", "Unit 1B", "Unit 2A", "Unit 2B"],
    },
    {
        "name": "Pinecrest Apartments",
        "address_line1": "3310 Pine Street",
        "city": "Redmond", "state": "WA", "postal_code": "98052",
        "units": ["Unit 101", "Unit 102", "Unit 201", "Unit 202", "Unit 301"],
    },
    {
        "name": "Harbor View",
        "address_line1": "520 Harbor Blvd",
        "city": "Kirkland", "state": "WA", "postal_code": "98033",
        "units": ["Studio A", "Studio B", "1BR North", "1BR South"],
    },
]

TENANTS = [
    {"first_name": "Marcus", "last_name": "Johnson", "email": "marcus.johnson@example.com", "phone": "+14255550101"},
    {"first_name": "Priya", "last_name": "Patel", "email": "priya.patel@example.com", "phone": "+14255550102"},
    {"first_name": "Devon", "last_name": "Torres", "email": "devon.torres@example.com", "phone": "+14255550103"},
    {"first_name": "Aisha", "last_name": "Williams", "email": "aisha.w@example.com", "phone": "+14255550104"},
    {"first_name": "Ryan", "last_name": "Chen", "email": "ryan.chen@example.com", "phone": "+14255550105"},
    {"first_name": "Sofia", "last_name": "Martinez", "email": "sofia.m@example.com", "phone": "+14255550106"},
    {"first_name": "Tyler", "last_name": "Brooks", "email": "tbrooks@example.com", "phone": "+14255550107"},
    {"first_name": "Nadia", "last_name": "Kim", "email": "nadia.kim@example.com", "phone": "+14255550108"},
]

VENDORS = [
    {"first_name": "Mike", "last_name": "Rivera", "role_label": "Plumber", "company": "Rivera Plumbing", "phone": "+14255550201"},
    {"first_name": "Karen", "last_name": "Walsh", "role_label": "Electrician", "company": "Walsh Electric", "phone": "+14255550202"},
    {"first_name": "James", "last_name": "Park", "role_label": "HVAC", "company": "Park Climate Systems", "phone": "+14255550203"},
    {"first_name": "Luis", "last_name": "Gomez", "role_label": "Handyman", "company": None, "phone": "+14255550204"},
    {"first_name": "Sarah", "last_name": "Chen", "role_label": "Landscaper", "company": "Green Thumb Landscaping", "phone": "+14255550205"},
]

# (prop_idx, unit_idx, tenant_idx, start, end, rent)
LEASES = [
    (0, 0, 0, date(2024, 2, 1), date(2025, 1, 31), 1850.0),
    (0, 1, 1, date(2024, 4, 1), date(2025, 3, 31), 1850.0),
    (0, 2, 2, date(2023, 9, 1), date(2025, 8, 31), 1950.0),
    (1, 0, 3, date(2024, 1, 1), date(2024, 12, 31), 1600.0),  # expired
    (1, 1, 4, date(2024, 6, 1), date(2025, 5, 31), 1650.0),
    (1, 2, 5, date(2024, 3, 1), date(2026, 2, 28), 1700.0),
    (2, 0, 6, date(2024, 8, 1), date(2025, 7, 31), 1400.0),
    (2, 2, 7, date(2024, 5, 1), date(2025, 4, 30), 1750.0),
]

# ── task definitions with baked conversation messages ───────────────────

TASKS = [
    {
        "title": "Leaking kitchen faucet — Unit 1A",
        "category": "MAINTENANCE", "urgency": "MEDIUM", "status": "ACTIVE",
        "prop_idx": 0, "unit_idx": 0,
        "external_type": "tenant", "entity_idx": 0,
        "messages": [
            ("tenant", "Hi, the kitchen faucet has been dripping for a couple days now. It's getting worse."),
            ("ai", "Thanks for letting me know, Marcus. I'll get a plumber scheduled to take a look. Is there a day that works best for you?"),
            ("tenant", "I work from home Tuesdays and Thursdays, either of those would be fine."),
            ("ai", "Great, I'll aim for this Thursday. I'll confirm the time once the plumber gets back to me."),
        ],
    },
    {
        "title": "Rent payment question — Unit 102",
        "category": "FINANCIAL", "urgency": "LOW", "status": "ACTIVE",
        "prop_idx": 1, "unit_idx": 1,
        "external_type": "tenant", "entity_idx": 4,
        "messages": [
            ("tenant", "Hey, I switched banks last month. Can I get the new payment portal link?"),
            ("ai", "Hi Ryan! Sure thing — I'll send you the updated payment link shortly."),
            ("tenant", "Thanks, no rush. Just want to make sure next month's payment goes through."),
        ],
    },
    {
        "title": "Fix garbage disposal — Unit 2A",
        "category": "MAINTENANCE", "urgency": "MEDIUM", "status": "ACTIVE",
        "prop_idx": 0, "unit_idx": 2,
        "external_type": "vendor", "vendor_idx": 0,
        "messages": [
            ("ai", "Hi Mike, we have a broken garbage disposal at 1842 Meadow Lane, Unit 2A. Are you available this week?"),
            ("vendor", "I can come by Wednesday afternoon, does 2pm work?"),
            ("ai", "That works. The tenant Devon will be home. Thanks!"),
        ],
    },
    {
        "title": "Annual HVAC inspection — Pinecrest",
        "category": "MAINTENANCE", "urgency": "LOW", "status": "ACTIVE",
        "prop_idx": 1, "unit_idx": None,
        "external_type": "vendor", "vendor_idx": 2,
        "messages": [
            ("ai", "Hi James, it's time for the annual HVAC inspection at Pinecrest Apartments (3310 Pine Street). Can we schedule for next week?"),
            ("vendor", "Sure, I can do Monday or Tuesday. I'll need access to all 5 units."),
        ],
    },
    {
        "title": "Repaired bathroom fan — Studio B",
        "category": "MAINTENANCE", "urgency": "LOW", "status": "RESOLVED",
        "prop_idx": 2, "unit_idx": 1,
        "external_type": "tenant", "entity_idx": None,
        "messages": [
            ("ai", "Hi, just confirming the bathroom fan in Studio B has been repaired. Let us know if you notice any issues."),
        ],
    },
    {
        "title": "Landscape spring cleanup",
        "category": "MAINTENANCE", "urgency": "LOW", "status": "ACTIVE",
        "prop_idx": None, "unit_idx": None,
        "external_type": None,
        "messages": [],
    },
]


# ── helpers ─────────────────────────────────────────────────────────────

def _now():
    return datetime.now(UTC)


def _create_properties(db: Session, creator_id: int, org_id: int):
    props = []
    for pd in PROPERTIES:
        unit_labels = pd.pop("units", [])
        p = Property(org_id=org_id, creator_id=creator_id, country="USA", **pd)
        db.add(p)
        db.flush()
        units = []
        for label in unit_labels:
            u = Unit(org_id=org_id, creator_id=creator_id, property_id=p.id, label=label)
            db.add(u)
            units.append(u)
        db.flush()
        props.append((p, units))
        pd["units"] = unit_labels
    return props


def _create_tenants(db: Session, creator_id: int, org_id: int):
    tenants = []
    for td in TENANTS:
        shadow = User(
            org_id=org_id, creator_id=creator_id,
            user_type="tenant", active=True, created_at=_now(), **td,
        )
        db.add(shadow)
        db.flush()
        t = Tenant(org_id=org_id, creator_id=creator_id, user_id=shadow.id, created_at=_now())
        db.add(t)
        db.flush()
        tenants.append((t, shadow))
    return tenants


def _create_vendors(db: Session, creator_id: int, org_id: int):
    vendors = []
    for vd in VENDORS:
        v = User(
            org_id=org_id, creator_id=creator_id,
            user_type="vendor", active=True, created_at=_now(),
            company=vd.get("company"),
            first_name=vd["first_name"], last_name=vd["last_name"],
            role_label=vd["role_label"], phone=vd["phone"],
        )
        db.add(v)
        vendors.append(v)
    db.flush()
    return vendors


def _create_leases(db: Session, creator_id: int, org_id: int, props, tenants):
    for pi, ui, ti, start, end, rent in LEASES:
        prop, units = props[pi]
        unit = units[ui]
        tenant, _ = tenants[ti]
        db.add(Lease(
            org_id=org_id, creator_id=creator_id,
            tenant_id=tenant.id, unit_id=unit.id, property_id=prop.id,
            start_date=start, end_date=end, rent_amount=rent,
        ))
    db.flush()


def _create_task_with_conversation(db, creator_id, org_id, task_def, props, tenants, vendors):
    now = _now()
    prop_id = props[task_def["prop_idx"]][0].id if task_def.get("prop_idx") is not None else None
    unit_id = None
    if task_def.get("prop_idx") is not None and task_def.get("unit_idx") is not None:
        unit_id = props[task_def["prop_idx"]][1][task_def["unit_idx"]].id

    seq = db.get(TaskNumberSequence, org_id)
    if seq is None:
        seq = TaskNumberSequence(org_id=org_id, last_number=0)
        db.add(seq)
        db.flush()
    seq.last_number += 1

    task = Task(
        id=seq.last_number,
        org_id=org_id, creator_id=creator_id,
        title=task_def["title"],
        task_status=task_def["status"],
        source="MANUAL",
        category=task_def.get("category", "OTHER"),
        urgency=task_def.get("urgency", "LOW"),
        property_id=prop_id, unit_id=unit_id,
        created_at=now, updated_at=now,
        resolved_at=now if task_def["status"] == "RESOLVED" else None,
    )
    db.add(task)
    db.flush()

    ai_convo = Conversation(
        org_id=org_id, creator_id=creator_id,
        subject=task_def["title"],
        property_id=prop_id, unit_id=unit_id,
        conversation_type=ConversationType.TASK_AI,
        is_group=False, is_archived=False,
        created_at=now, updated_at=now,
    )
    db.add(ai_convo)
    db.flush()
    task.ai_conversation_id = ai_convo.id

    ext_type = task_def.get("external_type")
    if not ext_type or not task_def.get("messages"):
        db.flush()
        return task

    if ext_type == "tenant" and task_def.get("entity_idx") is not None:
        tenant, tenant_user = tenants[task_def["entity_idx"]]
        ext_convo = Conversation(
            org_id=org_id, creator_id=creator_id,
            subject=f"Chat with {tenant_user.first_name} {tenant_user.last_name}",
            property_id=prop_id, unit_id=unit_id,
            conversation_type=ConversationType.TENANT,
            is_group=False, is_archived=False,
            created_at=now, updated_at=now,
        )
        db.add(ext_convo)
        db.flush()
        participant = ConversationParticipant(
            org_id=org_id, creator_id=creator_id,
            conversation_id=ext_convo.id,
            user_id=tenant_user.id,
            participant_type=ParticipantType.TENANT,
            is_active=True,
        )
        db.add(participant)
        db.flush()
        task.external_conversation_id = ext_convo.id
        _add_messages(db, org_id, ext_convo.id, participant.id, task_def["messages"],
                      person_name=tenant_user.first_name,
                      person_type=ParticipantType.TENANT)

    elif ext_type == "vendor" and task_def.get("vendor_idx") is not None:
        vendor_user = vendors[task_def["vendor_idx"]]
        ext_convo = Conversation(
            org_id=org_id, creator_id=creator_id,
            subject=f"Chat with {vendor_user.first_name} {vendor_user.last_name}",
            property_id=prop_id, unit_id=unit_id,
            conversation_type=ConversationType.VENDOR,
            is_group=False, is_archived=False,
            created_at=now, updated_at=now,
        )
        db.add(ext_convo)
        db.flush()
        participant = ConversationParticipant(
            org_id=org_id, creator_id=creator_id,
            conversation_id=ext_convo.id,
            user_id=vendor_user.id,
            participant_type=ParticipantType.EXTERNAL_CONTACT,
            is_active=True,
        )
        db.add(participant)
        db.flush()
        task.external_conversation_id = ext_convo.id
        _add_messages(db, org_id, ext_convo.id, participant.id, task_def["messages"],
                      person_name=vendor_user.first_name,
                      person_type=ParticipantType.EXTERNAL_CONTACT)

    db.flush()
    return task


def _add_messages(db, org_id, convo_id, participant_id, messages, *, person_name, person_type):
    base_time = _now() - timedelta(hours=len(messages))
    for i, (role, body) in enumerate(messages):
        is_ai = role == "ai"
        msg = Message(
            org_id=org_id,
            conversation_id=convo_id,
            sender_type=ParticipantType.ACCOUNT_USER if is_ai else person_type,
            sender_id=None if is_ai else participant_id,
            sender_name="RentMate" if is_ai else person_name,
            body=body,
            message_type=MessageType.MESSAGE,
            is_ai=is_ai,
            sent_at=base_time + timedelta(minutes=i * 12),
        )
        db.add(msg)
    db.flush()


# ── public entry point ──────────────────────────────────────────────────

def seed_if_needed(db: Session) -> bool:
    """Seed dev data if not already present. Returns True if seeded."""
    if db.query(User).filter_by(email=_SENTINEL_EMAIL).first():
        return False

    creator = db.execute(select(User).order_by(User.created_at)).scalars().first()
    if not creator:
        return False

    cid = creator.id
    oid = creator.org_id

    props = _create_properties(db, cid, oid)
    tenants = _create_tenants(db, cid, oid)
    vendors = _create_vendors(db, cid, oid)
    _create_leases(db, cid, oid, props, tenants)

    for task_def in TASKS:
        _create_task_with_conversation(db, cid, oid, task_def, props, tenants, vendors)

    db.add(User(
        org_id=oid, creator_id=cid,
        user_type="vendor", email=_SENTINEL_EMAIL,
        first_name="Dev", last_name="Seed",
        active=False, created_at=_now(),
    ))
    db.flush()
    return True
