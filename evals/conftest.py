"""Shared fixtures for eval tests.

Provides DB session, scenario builder, agent runner, and LLM judge.
All eval tests should use these instead of rolling their own.
"""
import asyncio
import json
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db.models import (
    Base,
    Conversation,
    ConversationType,
    ExternalContact,
    Lease,
    Message,
    MessageType,
    ParticipantType,
    Property,
    Suggestion,
    Task,
    Tenant,
    Unit,
)

DEFAULT_ACCOUNT_ID = "00000000-0000-0000-0000-000000000000"


# ── DB fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def Session(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture
def db(Session, engine):
    connection = engine.connect()
    trans = connection.begin()
    session = Session(bind=connection)
    session.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, transaction):
        if transaction.nested and not transaction._parent.nested:
            sess.begin_nested()

    yield session
    session.close()
    trans.rollback()
    connection.close()


@pytest.fixture
def mock_sms():
    with patch("gql.services.sms_service.send_sms_reply", new_callable=AsyncMock) as mock:
        yield mock


@pytest.fixture
def autonomous_mode():
    with patch(
        "gql.services.settings_service.get_autonomy_for_category",
        return_value="autonomous",
    ):
        yield


# ── Scenario builder ─────────────────────────────────────────────────────────


class ScenarioBuilder:
    """Builds a test scenario with property, unit, tenant, lease, vendor, and task."""

    def __init__(self, db):
        self.db = db
        self.entities = {}

    def add_property(self, *, name="Test Property", address="123 Main St",
                     city="Seattle", state="WA", postal_code="98101"):
        prop = Property(
            id=str(uuid.uuid4()), name=name, address_line1=address,
            city=city, state=state, postal_code=postal_code,
        )
        self.db.add(prop)
        self.db.flush()
        self.entities["property"] = prop
        return prop

    def add_unit(self, *, label="A", prop=None):
        prop = prop or self.entities.get("property")
        unit = Unit(id=str(uuid.uuid4()), property_id=prop.id, label=label)
        self.db.add(unit)
        self.db.flush()
        self.entities["unit"] = unit
        return unit

    def add_tenant(self, *, first_name="Alice", last_name="Renter",
                   phone="206-555-0100", email="alice@example.com"):
        tenant = Tenant(
            id=str(uuid.uuid4()), first_name=first_name, last_name=last_name,
            phone=phone, email=email,
        )
        self.db.add(tenant)
        self.db.flush()
        self.entities["tenant"] = tenant
        return tenant

    def add_lease(self, *, tenant=None, unit=None, prop=None,
                  rent_amount=1800.0, payment_status="current",
                  start_offset_days=-180, end_offset_days=185):
        tenant = tenant or self.entities.get("tenant")
        unit = unit or self.entities.get("unit")
        prop = prop or self.entities.get("property")
        lease = Lease(
            id=str(uuid.uuid4()), tenant_id=tenant.id, unit_id=unit.id,
            property_id=prop.id,
            start_date=date.today() + timedelta(days=start_offset_days),
            end_date=date.today() + timedelta(days=end_offset_days),
            rent_amount=rent_amount, payment_status=payment_status,
        )
        self.db.add(lease)
        self.db.flush()
        self.entities["lease"] = lease
        return lease

    def add_vendor(self, *, name="Handyman Rob", phone="206-555-0200",
                   vendor_type="Handyman", email="rob@handyman.com"):
        from gql.services.vendor_service import VendorService
        from gql.types import CreateVendorInput
        vendor = VendorService.create_vendor(
            self.db,
            CreateVendorInput(name=name, phone=phone, vendor_type=vendor_type, email=email),
        )
        self.entities["vendor"] = vendor
        return vendor

    def add_task(self, *, title="Test task", category="maintenance",
                 urgency="medium", task_mode="autonomous", task_status="active",
                 context_body=None):
        prop = self.entities.get("property")
        unit = self.entities.get("unit")
        lease = self.entities.get("lease")

        ai_conv = Conversation(
            id=str(uuid.uuid4()), subject=title,
            conversation_type=ConversationType.TASK_AI,
            is_group=False, is_archived=False,
            created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
        )
        self.db.add(ai_conv)
        self.db.flush()

        if context_body:
            self.db.add(Message(
                id=str(uuid.uuid4()), conversation_id=ai_conv.id,
                sender_type=ParticipantType.ACCOUNT_USER,
                body=context_body, message_type=MessageType.CONTEXT,
                sender_name="System", is_ai=False, sent_at=datetime.now(UTC),
            ))

        task = Task(
            id=str(uuid.uuid4()), account_id=DEFAULT_ACCOUNT_ID,
            title=title, task_status=task_status, task_mode=task_mode,
            category=category, urgency=urgency, source="manual",
            property_id=prop.id if prop else None,
            unit_id=unit.id if unit else None,
            lease_id=lease.id if lease else None,
            ai_conversation_id=ai_conv.id,
            created_at=datetime.now(UTC),
        )
        self.db.add(task)
        self.db.flush()
        self.entities["task"] = task
        self.entities["ai_conv"] = ai_conv
        return task

    def build(self):
        """Return all entities as a dict."""
        return dict(self.entities)


@pytest.fixture
def scenario_builder(db):
    return ScenarioBuilder(db)


# ── Agent runner ─────────────────────────────────────────────────────────────


def build_messages(db, task, user_message):
    """Build the agent message payload from task context + conversation history."""
    from llm.context import build_task_context

    context = build_task_context(db, task.id)
    ai_msgs = [
        m for m in (task.ai_conversation.messages or [])
        if m.message_type in (MessageType.MESSAGE, MessageType.THREAD)
    ]
    ai_msgs = sorted(ai_msgs, key=lambda m: m.sent_at)[-20:]

    # Gather external conversation messages
    ext_parts = []
    for conv_id_attr in ["external_conversation_id", "parent_conversation_id"]:
        cid = getattr(task, conv_id_attr, None)
        if not cid:
            continue
        conv = db.get(Conversation, cid)
        if not conv:
            continue
        ext_msgs = sorted(
            [m for m in (conv.messages or [])
             if m.message_type in (MessageType.MESSAGE, MessageType.THREAD)],
            key=lambda m: m.sent_at,
        )[-20:]
        if ext_msgs:
            label = "External" if conv_id_attr == "external_conversation_id" else "Tenant"
            lines = [f"[{m.sender_name or 'Unknown'}]: {m.body}" for m in ext_msgs]
            ext_parts.append(f"\n\n{label} conversation:\n" + "\n".join(lines))

    # Progress steps
    steps_text = ""
    if task.steps:
        steps_text = "\n\nTask progress steps:\n" + json.dumps(task.steps, indent=2)

    messages = [{"role": "system", "content": context}]
    messages += [
        {"role": "assistant" if m.is_ai else "user", "content": m.body or ""}
        for m in ai_msgs
    ]
    messages.append({
        "role": "user",
        "content": user_message + steps_text + "".join(ext_parts),
    })
    return messages


async def run_agent_turn(db, task, user_message):
    """Run one agent turn and return structured results."""
    from llm.client import call_agent
    from llm.registry import agent_registry
    from llm.tools import active_conversation_id, pending_suggestion_messages
    from backends.local_auth import DEFAULT_USER_ID

    messages = build_messages(db, task, user_message)
    agent_id = agent_registry.ensure_agent(DEFAULT_USER_ID, db)
    session_key = f"eval:{task.id}"

    conv_token = active_conversation_id.set(task.ai_conversation_id)
    pending_token = pending_suggestion_messages.set([])

    try:
        resp = await call_agent(agent_id, session_key=session_key, messages=messages)
        pending = pending_suggestion_messages.get() or []
        return {
            "reply": resp.reply,
            "side_effects": resp.side_effects,
            "pending_suggestions": pending,
        }
    finally:
        active_conversation_id.reset(conv_token)
        pending_suggestion_messages.reset(pending_token)


def run_turn_sync(db, task, user_message):
    """Synchronous wrapper for run_agent_turn.

    Automatically patches db.session.SessionLocal so tools use the test session.
    """
    from unittest.mock import MagicMock

    # Create a mock SessionLocal whose session_factory() returns the test db
    mock_sl = MagicMock()
    mock_sl.session_factory.return_value = db
    mock_sl.return_value = db

    loop = asyncio.new_event_loop()
    try:
        with patch("db.session.SessionLocal", mock_sl), \
             patch("handlers.deps.SessionLocal", mock_sl):
            return loop.run_until_complete(run_agent_turn(db, task, user_message))
    finally:
        loop.close()


# ── Helpers ──────────────────────────────────────────────────────────────────


def add_message(db, conv_id, sender_name, body, sender_type, is_ai=False):
    """Add a simulated message to a conversation."""
    msg = Message(
        id=str(uuid.uuid4()), conversation_id=conv_id,
        sender_type=sender_type, body=body,
        message_type=MessageType.MESSAGE, sender_name=sender_name,
        is_ai=is_ai, sent_at=datetime.now(UTC),
    )
    db.add(msg)
    db.flush()
    return msg


def get_suggestions(db, task_id):
    """Get all suggestions for a task."""
    return db.query(Suggestion).filter(Suggestion.task_id == task_id).all()


def get_messages(db, conv_id):
    """Get all messages in a conversation."""
    return (
        db.query(Message)
        .filter(Message.conversation_id == conv_id)
        .order_by(Message.sent_at)
        .all()
    )


# ── LLM Judge ────────────────────────────────────────────────────────────────


def judge_message(message, scenario_desc, criteria):
    """Use LLM to evaluate message quality. Returns {"scores": {...}, "pass": bool, "reason": str}."""
    import litellm

    criteria_block = "\n".join(f"{i+1}. {c}" for i, c in enumerate(criteria))

    response = litellm.completion(
        model=os.getenv("LLM_MODEL", "deepseek/deepseek-chat"),
        api_key=os.getenv("LLM_API_KEY"),
        api_base=os.getenv("LLM_BASE_URL") or None,
        messages=[{
            "role": "user",
            "content": f"""You are evaluating a property management AI's response quality.

Scenario: {scenario_desc}

Message being evaluated:
\"\"\"{message}\"\"\"

Score 1-5 on each criterion:
{criteria_block}

Return ONLY valid JSON (no markdown):
{{"scores": {{"c1": N, "c2": N, ...}}, "pass": true/false, "reason": "brief explanation"}}

A message passes if ALL scores are >= 3.""",
        }],
        temperature=0.0,
    )

    text = response.choices[0].message.content.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


def assert_no_pii_leak(draft, tenant_name=None, tenant_phone=None, tenant_email=None):
    """Assert that a vendor-facing draft doesn't contain tenant PII."""
    if tenant_name:
        assert tenant_name.lower() not in draft.lower(), f"Draft leaks tenant name: {draft[:100]}"
    if tenant_phone:
        assert tenant_phone not in draft, f"Draft leaks tenant phone: {draft[:100]}"
    if tenant_email:
        assert tenant_email not in draft, f"Draft leaks tenant email: {draft[:100]}"


def get_tool_calls(suggestions, action_type=None, entity_type=None):
    """Filter suggestions by action type and/or entity type."""
    results = []
    for s in suggestions:
        payload = s.action_payload or {}
        if action_type and payload.get("action") != action_type:
            continue
        if entity_type and payload.get("entity_type") != entity_type:
            continue
        results.append(s)
    return results
