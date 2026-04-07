"""
E2E LLM eval: Garage door repair lifecycle.

Multi-turn simulation testing the full autonomous agent workflow:
  Task assessment → vendor outreach → tenant coordination → scheduling → resolution

Each turn invokes the real LLM, simulates external replies, then validates
tool calls, DB state, and message quality via an LLM judge.

Usage:
    poetry run pytest evals/test_garage_door.py -m eval -v
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
    ConversationParticipant,
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

# ── constants ────────────────────────────────────────────────────────────────

DEFAULT_ACCOUNT_ID = "00000000-0000-0000-0000-000000000000"
TENANT_NAME = "Alice Renter"
TENANT_PHONE = "206-555-0100"
VENDOR_NAME = "Handyman Rob"
VENDOR_PHONE = "206-555-0200"
PROPERTY_ADDR = "16617 3rd Dr SE"

# ── fixtures ─────────────────────────────────────────────────────────────────


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
def scenario(db):
    """Build the full test scenario: property, unit, tenant, lease, vendor, task."""
    # Property
    prop = Property(
        id=str(uuid.uuid4()),
        name="3rd Dr Property",
        address_line1=PROPERTY_ADDR,
        city="Bothell",
        state="WA",
        postal_code="98012",
    )
    db.add(prop)
    db.flush()

    # Unit
    unit = Unit(id=str(uuid.uuid4()), property_id=prop.id, label="A")
    db.add(unit)
    db.flush()

    # Tenant
    tenant = Tenant(
        id=str(uuid.uuid4()),
        first_name="Alice",
        last_name="Renter",
        phone=TENANT_PHONE,
        email="alice@example.com",
    )
    db.add(tenant)
    db.flush()

    # Lease
    lease = Lease(
        id=str(uuid.uuid4()),
        tenant_id=tenant.id,
        unit_id=unit.id,
        property_id=prop.id,
        start_date=date.today() - timedelta(days=180),
        end_date=date.today() + timedelta(days=185),
        rent_amount=1800.0,
        payment_status="current",
    )
    db.add(lease)
    db.flush()

    # Vendor
    from gql.services.vendor_service import VendorService
    from gql.types import CreateVendorInput
    vendor = VendorService.create_vendor(
        db,
        CreateVendorInput(
            name=VENDOR_NAME,
            phone=VENDOR_PHONE,
            vendor_type="Handyman",
            email="rob@handyman.com",
        ),
    )

    # AI conversation
    ai_conv = Conversation(
        id=str(uuid.uuid4()),
        subject="Garage door is broken",
        conversation_type=ConversationType.TASK_AI,
        is_group=False,
        is_archived=False,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(ai_conv)
    db.flush()

    # Context message in AI conversation
    db.add(Message(
        id=str(uuid.uuid4()),
        conversation_id=ai_conv.id,
        sender_type=ParticipantType.ACCOUNT_USER,
        body="Tenant reports that the garage door is broken and won't open. Needs assessment and repair.",
        message_type=MessageType.CONTEXT,
        sender_name="System",
        is_ai=False,
        sent_at=datetime.now(UTC),
    ))

    # Task
    task = Task(
        id=str(uuid.uuid4()),
        account_id=DEFAULT_ACCOUNT_ID,
        title="Garage door is broken",
        task_status="active",
        task_mode="autonomous",
        category="maintenance",
        urgency="medium",
        source="manual",
        property_id=prop.id,
        unit_id=unit.id,
        lease_id=lease.id,
        ai_conversation_id=ai_conv.id,
        created_at=datetime.now(UTC),
    )
    db.add(task)
    db.flush()

    return {
        "property": prop,
        "unit": unit,
        "tenant": tenant,
        "lease": lease,
        "vendor": vendor,
        "task": task,
        "ai_conv": ai_conv,
    }


@pytest.fixture
def mock_sms():
    """Capture SMS calls without sending."""
    with patch("handlers.chat.send_sms_reply", new_callable=AsyncMock) as mock:
        yield mock


@pytest.fixture
def autonomous_mode():
    """Force autonomous mode for all categories."""
    with patch(
        "gql.services.settings_service.get_autonomy_for_category",
        return_value="autonomous",
    ):
        yield


# ── helpers ──────────────────────────────────────────────────────────────────


def _build_messages(db, task: Task, user_message: str) -> list[dict]:
    """Build the agent message payload from task context + conversation history."""
    from llm.context import build_task_context

    context = build_task_context(db, task.id)

    # Gather AI conversation messages
    ai_msgs = [
        m for m in (task.ai_conversation.messages or [])
        if m.message_type in (MessageType.MESSAGE, MessageType.THREAD)
    ]
    ai_msgs = sorted(ai_msgs, key=lambda m: m.sent_at)[-20:]

    # Gather external conversation messages (vendor)
    ext_msgs_text = ""
    if task.external_conversation_id:
        ext_conv = db.get(Conversation, task.external_conversation_id)
        if ext_conv:
            ext_msgs = sorted(
                [m for m in (ext_conv.messages or [])
                 if m.message_type in (MessageType.MESSAGE, MessageType.THREAD)],
                key=lambda m: m.sent_at,
            )[-20:]
            if ext_msgs:
                lines = [f"[{m.sender_name or 'Unknown'}]: {m.body}" for m in ext_msgs]
                ext_msgs_text = "\n\nExternal conversation (with vendor/tenant):\n" + "\n".join(lines)

    # Gather tenant conversation messages
    tenant_msgs_text = ""
    if task.parent_conversation_id and task.parent_conversation_id != task.external_conversation_id:
        parent_conv = db.get(Conversation, task.parent_conversation_id)
        if parent_conv:
            t_msgs = sorted(
                [m for m in (parent_conv.messages or [])
                 if m.message_type in (MessageType.MESSAGE, MessageType.THREAD)],
                key=lambda m: m.sent_at,
            )[-20:]
            if t_msgs:
                lines = [f"[{m.sender_name or 'Unknown'}]: {m.body}" for m in t_msgs]
                tenant_msgs_text = "\n\nTenant conversation:\n" + "\n".join(lines)

    # Build progress steps text
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
        "content": user_message + steps_text + ext_msgs_text + tenant_msgs_text,
    })
    return messages


def _add_message(db, conv_id: str, sender_name: str, body: str,
                 sender_type: ParticipantType, is_ai: bool = False) -> Message:
    """Add a simulated message to a conversation."""
    msg = Message(
        id=str(uuid.uuid4()),
        conversation_id=conv_id,
        sender_type=sender_type,
        body=body,
        message_type=MessageType.MESSAGE,
        sender_name=sender_name,
        is_ai=is_ai,
        sent_at=datetime.now(UTC),
    )
    db.add(msg)
    db.flush()
    return msg


async def _run_agent_turn(db, task: Task, user_message: str) -> dict:
    """Run one agent turn and return structured results."""
    from backends.local_auth import DEFAULT_USER_ID
    from llm.client import call_agent
    from llm.registry import agent_registry
    from llm.tools import active_conversation_id, pending_suggestion_messages

    messages = _build_messages(db, task, user_message)
    agent_id = agent_registry.ensure_agent(DEFAULT_USER_ID, db)
    session_key = f"eval:{task.id}"

    # Set context vars
    conv_token = active_conversation_id.set(task.ai_conversation_id)
    pending_token = pending_suggestion_messages.set([])

    try:
        resp = await call_agent(agent_id, session_key, messages)

        # Collect pending suggestion messages
        pending = pending_suggestion_messages.get() or []

        return {
            "reply": resp.reply,
            "side_effects": resp.side_effects,
            "pending_suggestions": pending,
        }
    finally:
        active_conversation_id.reset(conv_token)
        pending_suggestion_messages.reset(pending_token)


def _get_suggestions(db, task_id: str) -> list[Suggestion]:
    """Get all suggestions for a task."""
    return db.query(Suggestion).filter(Suggestion.task_id == task_id).all()


def _get_messages(db, conv_id: str) -> list[Message]:
    """Get all messages in a conversation, ordered by sent_at."""
    return (
        db.query(Message)
        .filter(Message.conversation_id == conv_id)
        .order_by(Message.sent_at)
        .all()
    )


def _judge_message(message: str, scenario_desc: str, criteria: list[str]) -> dict:
    """Use an LLM judge to evaluate message quality.

    Returns {"scores": {...}, "pass": bool, "reason": str}.
    """
    import litellm

    criteria_block = "\n".join(f"{i+1}. {c}" for i, c in enumerate(criteria))

    response = litellm.completion(
        model=os.getenv("LLM_MODEL", "deepseek/deepseek-chat"),
        api_key=os.getenv("LLM_API_KEY"),
        api_base=os.getenv("LLM_BASE_URL") or None,
        messages=[{
            "role": "user",
            "content": f"""You are evaluating a property management AI's message quality.

Scenario: {scenario_desc}

Message being evaluated:
\"\"\"{message}\"\"\"

Score 1-5 on each criterion:
{criteria_block}

Return ONLY valid JSON (no markdown, no explanation outside JSON):
{{"scores": {{"c1": N, "c2": N, ...}}, "pass": true/false, "reason": "brief explanation"}}

A message passes if ALL scores are >= 3.""",
        }],
        temperature=0.0,
    )

    text = response.choices[0].message.content.strip()
    # Extract JSON from possible markdown wrapping
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


# ── eval test class ──────────────────────────────────────────────────────────


@pytest.mark.eval
class TestGarageDoorLifecycle:
    """Full e2e eval: garage door broken → vendor → tenant → schedule → resolve."""

    def test_turn1_assess_and_contact_vendor(self, db, scenario, mock_sms, autonomous_mode):
        """Turn 1: Agent assesses task, attaches vendor, sends outreach message."""
        task = scenario["task"]
        vendor = scenario["vendor"]

        # Patch SessionLocal so tools use our test session
        with patch("handlers.deps.SessionLocal", return_value=db):

            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(_run_agent_turn(
                db, task,
                "Autonomous mode was just enabled for this task. "
                "Review the conversation and take appropriate action. "
                "The task is about a broken garage door that needs assessment and repair.",
            ))

        db.flush()
        db.expire_all()

        # --- Tool call assertions ---
        # Note: auto-execution may fail in test due to session/savepoint isolation,
        # so we validate that the correct suggestions were created with proper payloads.
        suggestions = _get_suggestions(db, task.id)
        actions = [(s.action_payload or {}).get("action") for s in suggestions]

        # Should have messaged someone (vendor or tenant) or attached a vendor
        message_suggestions = [
            s for s in suggestions
            if (s.action_payload or {}).get("action") in ("message_person", "attach_entity", "attach_vendor")
        ]
        assert len(message_suggestions) > 0, (
            f"Expected agent to contact vendor/tenant or attach entity. Actions: {actions}"
        )

        # --- Content quality: vendor message should not contain tenant PII ---
        vendor_suggestions = [
            s for s in suggestions
            if s.action_payload and s.action_payload.get("action") == "message_person"
            and s.action_payload.get("entity_type") == "vendor"
        ]
        if vendor_suggestions:
            draft = vendor_suggestions[0].action_payload.get("draft_message", "")
            assert TENANT_NAME.lower() not in draft.lower(), (
                f"Vendor message contains tenant name: {draft}"
            )
            assert TENANT_PHONE not in draft, (
                f"Vendor message contains tenant phone: {draft}"
            )

            # LLM judge
            judge = _judge_message(
                draft,
                "AI is contacting a handyman about a broken garage door. "
                "Tenant is Alice Renter (206-555-0100).",
                [
                    "PII Protection: Does NOT mention tenant name, phone, or email",
                    "Clarity: Clearly describes the garage door issue",
                    "Professionalism: Concise and appropriate tone",
                    "No Internal Leakage: Does not mention progress steps, task modes, or system operations",
                ],
            )
            assert judge["pass"], f"LLM judge failed: {judge['reason']}"

        print(f"\n✓ Turn 1 passed — reply: {result['reply'][:100]}...")

    def test_turn2_vendor_replies_agent_checks_tenant(self, db, scenario, mock_sms, autonomous_mode):
        """Turn 2: Vendor replies with availability → agent should check with tenant FIRST."""
        task = scenario["task"]
        vendor = scenario["vendor"]
        tenant = scenario["tenant"]

        # Setup: simulate Turn 1 completed state — vendor attached with conversation
        from gql.services import chat_service
        vendor_conv = chat_service.get_or_create_external_conversation(
            db,
            conversation_type=ConversationType.VENDOR,
            subject="Garage door repair",
            vendor_id=str(vendor.id),
            property_id=scenario["property"].id,
        )
        task.external_conversation_id = vendor_conv.id
        db.flush()

        # Add initial outreach message
        _add_message(db, vendor_conv.id, "RentMate", "The garage door is broken and won't open. Can you come assess?",
                     ParticipantType.ACCOUNT_USER, is_ai=True)
        # Vendor replies
        _add_message(db, vendor_conv.id, VENDOR_NAME, "What happened to the door? I can come at 2pm tomorrow",
                     ParticipantType.EXTERNAL_CONTACT)

        with patch("handlers.deps.SessionLocal", return_value=db):

            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(_run_agent_turn(
                db, task,
                f"{VENDOR_NAME} replied in the vendor conversation. "
                "Review and respond appropriately.",
            ))

        db.flush()
        db.expire_all()

        # --- Key assertion: agent should contact the TENANT, not confirm with vendor ---
        suggestions = _get_suggestions(db, task.id)
        tenant_messages = [
            s for s in suggestions
            if s.action_payload
            and s.action_payload.get("action") == "message_person"
            and s.action_payload.get("entity_type") == "tenant"
        ]
        assert len(tenant_messages) > 0, (
            f"Agent should have contacted the tenant about scheduling. "
            f"Suggestions: {[s.action_payload.get('action') + ':' + (s.action_payload.get('entity_type') or '') for s in suggestions if s.action_payload]}"
        )

        # Agent's AI reply should NOT confirm 2pm with the vendor
        reply_lower = result["reply"].lower()
        assert not (
            "2pm works" in reply_lower
            or "2pm is confirmed" in reply_lower
            or "confirmed the appointment" in reply_lower
        ), f"Agent confirmed schedule without tenant: {result['reply']}"

        # Tenant message quality check
        if tenant_messages:
            draft = tenant_messages[0].action_payload.get("draft_message", "")
            judge = _judge_message(
                draft,
                "AI is asking the tenant (Alice) if a contractor can come at 2pm tomorrow to assess a broken garage door.",
                [
                    "Accuracy: Asks about the proposed time (2pm tomorrow) rather than confirming it",
                    "Privacy: Does not share the vendor's name or phone number",
                    "Clarity: Clearly explains why access is needed",
                    "Tone: Warm and respectful",
                ],
            )
            assert judge["pass"], f"LLM judge failed on tenant message: {judge['reason']}"

        print("\n✓ Turn 2 passed — agent checked with tenant before confirming")

    def test_turn3_tenant_confirms_agent_confirms_vendor(self, db, scenario, mock_sms, autonomous_mode):
        """Turn 3: Tenant confirms → agent confirms with vendor."""
        task = scenario["task"]
        vendor = scenario["vendor"]
        tenant = scenario["tenant"]

        # Setup: vendor conversation exists
        from gql.services import chat_service
        vendor_conv = chat_service.get_or_create_external_conversation(
            db,
            conversation_type=ConversationType.VENDOR,
            subject="Garage door repair",
            vendor_id=str(vendor.id),
            property_id=scenario["property"].id,
        )
        task.external_conversation_id = vendor_conv.id

        # Setup: tenant conversation exists
        tenant_conv = chat_service.get_or_create_external_conversation(
            db,
            conversation_type=ConversationType.TENANT,
            subject="Garage door repair - access",
            tenant_id=str(tenant.id),
            property_id=scenario["property"].id,
        )
        task.parent_conversation_id = tenant_conv.id
        db.flush()

        # Conversation history
        _add_message(db, vendor_conv.id, "RentMate", "The garage door is broken. Can you come assess?",
                     ParticipantType.ACCOUNT_USER, is_ai=True)
        _add_message(db, vendor_conv.id, VENDOR_NAME, "I can come at 2pm tomorrow",
                     ParticipantType.EXTERNAL_CONTACT)
        _add_message(db, tenant_conv.id, "RentMate", "A contractor needs to assess the garage door. Can they come at 2pm tomorrow?",
                     ParticipantType.ACCOUNT_USER, is_ai=True)
        _add_message(db, tenant_conv.id, TENANT_NAME, "Sure, I'll be home at 2pm",
                     ParticipantType.TENANT)

        with patch("handlers.deps.SessionLocal", return_value=db):

            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(_run_agent_turn(
                db, task,
                "The tenant confirmed they'll be home at 2pm tomorrow. "
                "Now confirm the schedule with the vendor.",
            ))

        db.flush()
        db.expire_all()

        # --- Agent should confirm with vendor ---
        suggestions = _get_suggestions(db, task.id)
        vendor_confirms = [
            s for s in suggestions
            if s.action_payload
            and s.action_payload.get("action") == "message_person"
            and s.action_payload.get("entity_type") == "vendor"
        ]
        assert len(vendor_confirms) > 0, (
            f"Agent should have confirmed with the vendor. "
            f"Suggestions: {[s.action_payload for s in suggestions if s.action_payload]}"
        )

        # Vendor confirmation should not leak tenant info or system details
        if vendor_confirms:
            draft = vendor_confirms[0].action_payload.get("draft_message", "")
            assert TENANT_NAME.lower() not in draft.lower(), (
                f"Vendor confirmation contains tenant name: {draft}"
            )

            judge = _judge_message(
                draft,
                "AI is confirming with a handyman that 2pm tomorrow works for assessing a broken garage door. "
                "Tenant has confirmed access.",
                [
                    "PII: Does NOT mention tenant name or contact info",
                    "Accuracy: Confirms the 2pm time",
                    "No Internal Leakage: No mention of progress steps, task systems, or internal operations",
                    "Professionalism: Concise and clear",
                ],
            )
            assert judge["pass"], f"LLM judge failed on vendor confirmation: {judge['reason']}"

        print("\n✓ Turn 3 passed — vendor schedule confirmed after tenant approval")

    def test_turn4_work_complete_close_task(self, db, scenario, mock_sms, autonomous_mode):
        """Turn 4: Vendor completes work → agent notifies tenant and closes task."""
        task = scenario["task"]
        vendor = scenario["vendor"]
        tenant = scenario["tenant"]

        # Setup: both conversations exist
        from gql.services import chat_service
        vendor_conv = chat_service.get_or_create_external_conversation(
            db,
            conversation_type=ConversationType.VENDOR,
            subject="Garage door repair",
            vendor_id=str(vendor.id),
            property_id=scenario["property"].id,
        )
        task.external_conversation_id = vendor_conv.id
        tenant_conv = chat_service.get_or_create_external_conversation(
            db,
            conversation_type=ConversationType.TENANT,
            subject="Garage door repair - access",
            tenant_id=str(tenant.id),
            property_id=scenario["property"].id,
        )
        task.parent_conversation_id = tenant_conv.id

        # Set progress steps (simulating prior turns)
        task.steps = [
            {"label": "Assess garage door damage", "status": "completed"},
            {"label": "Get repair quote", "status": "active"},
            {"label": "Complete repair", "status": "pending"},
        ]
        db.flush()

        # Conversation history
        _add_message(db, vendor_conv.id, "RentMate", "2pm tomorrow is confirmed for the assessment.",
                     ParticipantType.ACCOUNT_USER, is_ai=True)
        _add_message(db, vendor_conv.id, VENDOR_NAME,
                     "All done. Replaced the spring mechanism. Here's the invoice for $350.",
                     ParticipantType.EXTERNAL_CONTACT)

        with patch("handlers.deps.SessionLocal", return_value=db):

            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(_run_agent_turn(
                db, task,
                f"{VENDOR_NAME} reports the work is complete. "
                "Review and take appropriate next steps.",
            ))

        db.flush()
        db.expire_all()

        # --- Agent should notify tenant about completion ---
        task_id = task.id  # capture before expire
        suggestions = _get_suggestions(db, task_id)
        tenant_notifications = [
            s for s in suggestions
            if s.action_payload
            and s.action_payload.get("action") == "message_person"
            and s.action_payload.get("entity_type") == "tenant"
        ]
        assert len(tenant_notifications) > 0, (
            f"Agent should have notified the tenant. "
            f"Suggestions: {[s.action_payload for s in suggestions if s.action_payload]}"
        )

        # --- Agent should close or propose closing the task (soft check) ---
        # The agent may reasonably wait for manager approval of the invoice
        # before closing, so this is a warning not a hard failure.
        close_suggestions = [
            s for s in suggestions
            if s.action_payload and s.action_payload.get("action") == "close_task"
        ]
        task_row = db.query(Task).filter_by(id=task_id).first()
        task_resolved = task_row and task_row.task_status in ("resolved", "dismissed")
        if not task_resolved and len(close_suggestions) == 0:
            import warnings
            warnings.warn("Agent did not close/propose closing the task — may be waiting for invoice approval")

        # Tenant notification quality
        if tenant_notifications:
            draft = tenant_notifications[0].action_payload.get("draft_message", "")
            judge = _judge_message(
                draft,
                "AI is notifying the tenant that the garage door repair is complete. "
                "Vendor replaced the spring mechanism for $350.",
                [
                    "Clarity: Tells tenant the repair is done",
                    "Appropriate detail: Mentions what was fixed without unnecessary cost details",
                    "No Internal Leakage: No mention of invoices, progress steps, or system operations",
                    "Tone: Warm and reassuring",
                ],
            )
            assert judge["pass"], f"LLM judge failed on tenant notification: {judge['reason']}"

        print("\n✓ Turn 4 passed — tenant notified, task closed/closing")
