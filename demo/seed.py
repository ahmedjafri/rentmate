"""Dev seed data — creates a realistic portfolio on first startup.

Guarded by a sentinel user (dev-seed@rentmate.local). Safe to call on every
boot; idempotent after the first run.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import (
    Conversation,
    ConversationParticipant,
    ConversationType,
    Message,
    MessageType,
    ParticipantType,
    Task,
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
    {"first_name": "Alex", "last_name": "Nakamura", "role_label": "Landscaper", "company": "Evergreen Yard Care", "phone": "+14255550206"},
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

# Each task may declare 0+ "conversations" threads. A thread is either with a
# tenant (entity_idx → TENANTS) or a vendor (vendor_idx → VENDORS). Multiple
# threads on one task render as separate tabs in the task chat panel, so we
# can exercise flows like "got two quotes from two landscapers".
TASKS = [
    {
        "title": "Leaking kitchen faucet — Unit 1A",
        "goal": "Fix the faucet drip with a trusted plumber this week and confirm the repair with Marcus.",
        # Final step is the outcome-verification one — matches the
        # ``confirm/verify ... works/fixed/repaired/completed`` shape that
        # ``ProposeTaskTool`` now requires. Without it the dashboard
        # progress bar would tick to 100% (3/3) before the actual repair
        # happens, which is the bug the gate prevents at propose-time.
        "steps": [
            {"key": "triage", "label": "Review the faucet issue details", "status": "done"},
            {"key": "schedule_vendor", "label": "Schedule Rivera Plumbing", "status": "done"},
            {"key": "confirm_tenant", "label": "Confirm access window with Marcus", "status": "active"},
            {"key": "verify_repair", "label": "Confirm the faucet repair is completed", "status": "pending"},
        ],
        "category": "MAINTENANCE", "urgency": "MEDIUM", "status": "ACTIVE",
        "prop_idx": 0, "unit_idx": 0,
        "conversations": [
            {
                "type": "tenant", "entity_idx": 0,
                "messages": [
                    ("tenant", "Hi, the kitchen faucet has been dripping for a couple days now. It's getting worse."),
                    ("ai", "Thanks for letting me know, Marcus. I'll get a plumber scheduled to take a look. Is there a day that works best for you?"),
                    ("tenant", "I work from home Tuesdays and Thursdays, either of those would be fine."),
                    ("ai", "Great, I'll aim for this Thursday. I'll confirm the time once the plumber gets back to me."),
                ],
            },
            {
                "type": "vendor", "vendor_idx": 0,
                "messages": [
                    ("ai", "Hi Mike — tenant at 1842 Meadow Lane Unit 1A reports the kitchen faucet is dripping and getting worse. Any chance you could swing by this Thursday?"),
                    ("vendor", "Thursday works. I'd estimate 45 min. $95 flat for the diagnosis + washer replacement if that's all it is. Cartridge swap would add parts."),
                    ("ai", "Sounds good — tenant Marcus is WFH Thursday. What time works?"),
                    ("vendor", "Let's say 10:30am. I'll text when I'm en route."),
                    ("ai", "Confirmed 10:30am Thursday. Tenant is expecting you. Thanks Mike."),
                ],
            },
        ],
    },
    {
        "title": "Rent payment question — Unit 102",
        "goal": "Get Ryan's new payment portal link to him and confirm next month's rent will clear without issue.",
        "steps": [
            {"key": "review_request", "label": "Review Ryan's portal-link request", "status": "done"},
            {"key": "share_link", "label": "Send the updated payment portal link", "status": "active"},
            {"key": "confirm_payment", "label": "Confirm next month's payment is completed", "status": "pending"},
        ],
        "category": "RENT", "urgency": "LOW", "status": "ACTIVE",
        "prop_idx": 1, "unit_idx": 1,
        "conversations": [
            {
                "type": "tenant", "entity_idx": 4,
                "messages": [
                    ("tenant", "Hey, I switched banks last month. Can I get the new payment portal link?"),
                    ("ai", "Hi Ryan! Sure thing — I'll send you the updated payment link shortly."),
                    ("tenant", "Thanks, no rush. Just want to make sure next month's payment goes through."),
                ],
            },
        ],
    },
    {
        "title": "Fix garbage disposal — Unit 2A",
        "goal": "Repair or replace the garbage disposal in Unit 2A and close the loop with Devon once it's working.",
        "steps": [
            {"key": "review_issue", "label": "Review Devon's disposal issue", "status": "done"},
            {"key": "schedule_plumber", "label": "Schedule Rivera Plumbing visit", "status": "done"},
            {"key": "complete_repair", "label": "Confirm the disposal works after repair", "status": "active"},
        ],
        "category": "MAINTENANCE", "urgency": "MEDIUM", "status": "ACTIVE",
        "prop_idx": 0, "unit_idx": 2,
        "conversations": [
            {
                "type": "tenant", "entity_idx": 2,
                "messages": [
                    ("tenant", "The garbage disposal is making a grinding sound and won't spin — think something's stuck or burned out."),
                    ("ai", "Got it Devon, thanks for the report. I'll line up a plumber to take a look. Any time you're usually out so they can access the unit?"),
                    ("tenant", "I WFH Weds, so afternoon works best."),
                    ("ai", "Perfect — I'll target Wednesday afternoon and confirm the window once the vendor responds."),
                    ("ai", "Mike (Rivera Plumbing) confirmed Wednesday 2pm. Plan to be home?"),
                    ("tenant", "Yep, I'll be here. Thanks!"),
                ],
            },
            {
                "type": "vendor", "vendor_idx": 0,
                "messages": [
                    ("ai", "Hi Mike, we have a broken garbage disposal at 1842 Meadow Lane, Unit 2A. Are you available this week?"),
                    ("vendor", "I can come by Wednesday afternoon, does 2pm work?"),
                    ("ai", "That works. The tenant Devon will be home. Thanks!"),
                ],
            },
        ],
    },
    {
        "title": "Annual HVAC inspection — Pinecrest",
        "goal": "Get all 5 units inspected before the end of the month and file the report.",
        "steps": [
            {"key": "schedule_inspection", "label": "Schedule HVAC inspection with Park Climate Systems", "status": "active"},
            {"key": "coordinate_access", "label": "Coordinate access for all Pinecrest units", "status": "pending"},
            {"key": "file_report", "label": "File the inspection report", "status": "pending"},
            {"key": "verify_inspection", "label": "Confirm HVAC inspection completed", "status": "pending"},
        ],
        "category": "MAINTENANCE", "urgency": "LOW", "status": "ACTIVE",
        "prop_idx": 1, "unit_idx": None,
        "conversations": [
            {
                "type": "vendor", "vendor_idx": 2,
                "messages": [
                    ("ai", "Hi James, it's time for the annual HVAC inspection at Pinecrest Apartments (3310 Pine Street). Can we schedule for next week?"),
                    ("vendor", "Sure, I can do Monday or Tuesday. I'll need access to all 5 units."),
                ],
            },
        ],
    },
    {
        "title": "Repaired bathroom fan — Studio B",
        "goal": "Confirm the bathroom fan works before the next tenant moves in.",
        "steps": [
            {"key": "repair_fan", "label": "Repair the bathroom fan", "status": "done"},
            {"key": "test_fan", "label": "Test fan operation", "status": "done"},
            {"key": "verify_fan", "label": "Confirm bathroom fan works for turnover", "status": "done"},
        ],
        "category": "MAINTENANCE", "urgency": "LOW", "status": "RESOLVED",
        "prop_idx": 2, "unit_idx": 1,
        "conversations": [],
    },
    {
        # Multi-vendor scenario — two landscapers bidding. Exercises the
        # "multiple external chats on one task" UI path.
        "title": "Landscape spring cleanup — getting quotes",
        "goal": "Get at least two landscaper quotes and pick one to do a spring cleanup at The Meadows by end of month.",
        "steps": [
            {"key": "collect_quotes", "label": "Collect landscaper quotes", "status": "done"},
            {"key": "compare_vendors", "label": "Compare bids and timing", "status": "active"},
            {"key": "book_cleanup", "label": "Book the selected landscaper", "status": "pending"},
            {"key": "verify_cleanup", "label": "Confirm spring cleanup completed", "status": "pending"},
        ],
        "category": "MAINTENANCE", "urgency": "LOW", "status": "ACTIVE",
        "prop_idx": 0, "unit_idx": None,
        "conversations": [
            {
                "type": "vendor", "vendor_idx": 4,
                "messages": [
                    ("ai", "Hi Sarah, can you quote spring cleanup at The Meadows (1842 Meadow Lane)? 4 units, common front + back lawn, shrub trim, and bed weeding."),
                    ("vendor", "Sure — for that scope I'd quote $680. Could do it the last week of this month."),
                    ("ai", "Thanks — I'm collecting one more quote and will circle back on scheduling."),
                ],
            },
            {
                "type": "vendor", "vendor_idx": 5,
                "messages": [
                    ("ai", "Hi Alex, looking for a quote on spring cleanup at The Meadows (1842 Meadow Lane). Front/back lawn mow, shrub trim, bed weeding — common areas only, 4-unit building."),
                    ("vendor", "$545 for the first cleanup, $60/visit if you want biweekly through summer. I have next Thursday open."),
                    ("ai", "Appreciate it. Holding the Thursday slot while I confirm with the owner."),
                ],
            },
        ],
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

    from services.number_allocator import NumberAllocator
    next_id = NumberAllocator.allocate_next(db, entity_type="task", org_id=org_id)

    task = Task(
        id=next_id,
        org_id=org_id, creator_id=creator_id,
        title=task_def["title"],
        goal=task_def.get("goal"),
        steps=task_def.get("steps"),
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

    for convo_def in task_def.get("conversations") or []:
        _create_external_conversation(
            db, creator_id=creator_id, org_id=org_id,
            task=task, prop_id=prop_id, unit_id=unit_id,
            convo_def=convo_def, tenants=tenants, vendors=vendors,
            now=now,
        )

    db.flush()
    return task


def _create_external_conversation(
    db, *, creator_id, org_id, task, prop_id, unit_id, convo_def,
    tenants, vendors, now,
):
    ctype = convo_def.get("type")
    if ctype == "tenant":
        idx = convo_def.get("entity_idx")
        if idx is None:
            return
        _, contact_user = tenants[idx]
        conversation_type = ConversationType.TENANT
        participant_type = ParticipantType.TENANT
    elif ctype == "vendor":
        idx = convo_def.get("vendor_idx")
        if idx is None:
            return
        contact_user = vendors[idx]
        conversation_type = ConversationType.VENDOR
        participant_type = ParticipantType.EXTERNAL_CONTACT
    else:
        return

    ext_convo = Conversation(
        org_id=org_id, creator_id=creator_id,
        subject=f"Chat with {contact_user.first_name} {contact_user.last_name}".strip(),
        property_id=prop_id, unit_id=unit_id,
        conversation_type=conversation_type,
        is_group=False, is_archived=False,
        parent_task_id=task.id,
        created_at=now, updated_at=now,
    )
    db.add(ext_convo)
    db.flush()
    participant = ConversationParticipant(
        org_id=org_id, creator_id=creator_id,
        conversation_id=ext_convo.id,
        user_id=contact_user.id,
        participant_type=participant_type,
        is_active=True,
    )
    db.add(participant)
    db.flush()
    messages = convo_def.get("messages") or []
    if messages:
        _add_messages(
            db, org_id, ext_convo.id, participant.id, messages,
            person_name=contact_user.first_name,
            person_type=participant_type,
        )


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
