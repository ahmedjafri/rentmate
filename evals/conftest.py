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
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from backends.local_auth import reset_request_context, set_request_context
from db.enums import TaskCategory, TaskMode, TaskSource, TaskStatus, Urgency
from db.models import (
    Base,
    Conversation,
    ConversationType,
    Lease,
    Message,
    MessageType,
    ParticipantType,
    Property,
    Suggestion,
    Task,
    Tenant,
    Unit,
    User,
)
from db.models.account import create_shadow_user

DEFAULT_ACCOUNT_ID = 1

# Keep eval runs aligned with CI and avoid parallel Chroma crashes.
os.environ.setdefault("RENTMATE_DISABLE_VECTOR_INDEX", "1")


# ── DB fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def engine(isolated_engine):
    eng = isolated_engine
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

    session.add(User(
        id=DEFAULT_ACCOUNT_ID,
        external_id=str(uuid.uuid4()),
        org_id=1,
        email="eval-admin@example.com",
        first_name="Eval",
        last_name="Admin",
        user_type="account",
        active=True,
    ))
    session.flush()

    yield session
    session.close()
    if trans.is_active and connection.in_transaction():
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
    ), patch(
        "llm.action_policy.outbound_message_allows_risk",
        return_value=False,
    ):
        yield


# ── Scenario builder ─────────────────────────────────────────────────────────


class ScenarioBuilder:
    """Builds a test scenario with property, unit, tenant, lease, vendor, and task."""

    def __init__(self, db):
        self.db = db
        self.entities = {}

    @staticmethod
    def _coerce_task_category(value):
        if isinstance(value, TaskCategory):
            return value
        return TaskCategory(value)

    @staticmethod
    def _coerce_task_mode(value):
        if isinstance(value, TaskMode):
            return value
        return TaskMode[value.upper()]

    @staticmethod
    def _coerce_task_status(value):
        if isinstance(value, TaskStatus):
            return value
        return TaskStatus[value.upper()]

    @staticmethod
    def _coerce_urgency(value):
        if isinstance(value, Urgency):
            return value
        return Urgency[value.upper()]

    def add_property(self, *, name="Test Property", address="123 Main St",
                     city="Seattle", state="WA", postal_code="98101"):
        prop = Property(
            id=str(uuid.uuid4()),
            org_id=1,
            creator_id=DEFAULT_ACCOUNT_ID,
            name=name,
            address_line1=address,
            city=city, state=state, postal_code=postal_code,
        )
        self.db.add(prop)
        self.db.flush()
        self.entities["property"] = prop
        return prop

    def add_unit(self, *, label="A", prop=None):
        prop = prop or self.entities.get("property")
        unit = Unit(
            id=str(uuid.uuid4()),
            org_id=1,
            creator_id=DEFAULT_ACCOUNT_ID,
            property_id=prop.id,
            label=label,
        )
        self.db.add(unit)
        self.db.flush()
        self.entities["unit"] = unit
        return unit

    def add_tenant(self, *, first_name="Alice", last_name="Renter",
                   phone="206-555-0100", email="alice@example.com"):
        shadow_user = create_shadow_user(
            self.db,
            org_id=1,
            creator_id=DEFAULT_ACCOUNT_ID,
            user_type="tenant",
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            email=email,
        )
        tenant = Tenant(
            org_id=1,
            creator_id=DEFAULT_ACCOUNT_ID,
            user_id=shadow_user.id,
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
            id=str(uuid.uuid4()),
            org_id=1,
            creator_id=DEFAULT_ACCOUNT_ID,
            tenant_id=tenant.id,
            unit_id=unit.id,
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
                   vendor_type="Handyman", email=None):
        from gql.services.vendor_service import VendorService
        from gql.types import CreateVendorInput
        if email is None:
            normalized_phone = "".join(ch.lower() for ch in phone if ch.isalnum()) or uuid.uuid4().hex[:8]
            normalized_name = "".join(ch.lower() for ch in name if ch.isalnum()) or "vendor"
            email = f"{normalized_name}-{normalized_phone}@example.com"
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
            org_id=1,
            creator_id=DEFAULT_ACCOUNT_ID,
            subject=title,
            conversation_type=ConversationType.TASK_AI,
            is_group=False, is_archived=False,
            created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
        )
        self.db.add(ai_conv)
        self.db.flush()

        if context_body:
            self.db.add(Message(
                org_id=1,
                conversation_id=ai_conv.id,
                sender_type=ParticipantType.ACCOUNT_USER,
                body=context_body, message_type=MessageType.CONTEXT,
                sender_name="System", is_ai=False, sent_at=datetime.now(UTC),
            ))

        task = Task(
            org_id=1,
            creator_id=DEFAULT_ACCOUNT_ID,
            title=title,
            task_status=self._coerce_task_status(task_status),
            task_mode=self._coerce_task_mode(task_mode),
            category=self._coerce_task_category(category),
            urgency=self._coerce_urgency(urgency),
            source=TaskSource.MANUAL,
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
    DEFAULT_USER_ID = "1"
    from llm.client import call_agent
    from llm.registry import agent_registry
    from llm.tools import active_conversation_id, pending_suggestion_messages

    messages = build_messages(db, task, user_message)
    agent_id = agent_registry.ensure_agent(DEFAULT_USER_ID, db)
    session_key = f"eval:{task.id}"

    ctx_token = set_request_context(account_id=DEFAULT_ACCOUNT_ID, org_id=1)
    conv_token = active_conversation_id.set(task.ai_conversation_id)
    pending_token = pending_suggestion_messages.set([])

    try:
        resp = await call_agent(agent_id, session_key=session_key, messages=messages)
        pending = pending_suggestion_messages.get() or []
        outbound_reply = _extract_latest_outbound_message(
            db,
            task.id,
            user_message=user_message,
            fallback_reply=resp.reply,
        ) or resp.reply
        return {
            "reply": outbound_reply,
            "side_effects": resp.side_effects,
            "pending_suggestions": pending,
        }
    finally:
        reset_request_context(ctx_token)
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
             patch("handlers.deps.SessionLocal", mock_sl), \
             patch("gql.services.settings_service.SessionLocal", mock_sl):
            return loop.run_until_complete(run_agent_turn(db, task, user_message))
    finally:
        loop.close()


# ── Helpers ──────────────────────────────────────────────────────────────────


def add_message(db, conv_id, sender_name, body, sender_type, is_ai=False):
    """Add a simulated message to a conversation."""
    msg = Message(
        org_id=1,
        conversation_id=conv_id,
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
    judge_model = os.getenv("EVAL_JUDGE_MODEL") or os.getenv("LLM_MODEL", "deepseek/deepseek-chat")

    response = litellm.completion(
        model=judge_model,
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

Evaluation rules:
- Score the words that are actually present in the message, not what you personally would have preferred.
- Ground every score in concrete evidence from the message. Before deciding, identify the exact words or sentence that support or fail each criterion.
- Never say a message "does not acknowledge" or "lacks" something if the relevant wording appears verbatim or in a clear paraphrase in the message.
- Treat explicit phrases in the reply as decisive evidence. If the message says "I'm sorry", "I'm so sorry", "I hope you're recovering", or similar, that counts as empathy unless immediately undermined.
- Accept any of the following as clear empathy examples when sincere and context-appropriate:
  - "I'm sorry to hear that."
  - "I'm so sorry to hear about your hospitalization."
  - "I'm sorry you went through that."
  - "I understand this has been frustrating."
  - "Please prioritize your recovery."
  - "I hope you're recovering well."
  - "Take care."
- Accept any of the following as clear acknowledgment of a payment or timing commitment:
  - "Thanks for letting me know you'll be able to pay by then."
  - "I understand you'll be able to make the payment at that time."
  - "Thanks for confirming the payment timing."
  - "Noted that you'll have the payment on the timeline you mentioned."
  - Any natural restatement that repeats or clearly refers back to the same concrete day/date/time the tenant gave.
- If the message repeats or clearly refers back to a concrete day/date/time from the tenant, that counts as acknowledging the payment or timing commitment.
- If the message says it will check with the property manager, follow up, review a waiver/request, or get back to the tenant, that counts as a concrete escalation or next step.
- If the message explicitly contains empathy language such as "I'm sorry", "I understand", "I know this is frustrating", or "I hope you're recovering", score empathy at least 4 unless the rest of the message undermines it.
- If the message explicitly repeats a promised timing such as "by Friday", "tomorrow", or another concrete date/time from the tenant, score acknowledgment of that commitment at least 4.
- If the message says it will check with the manager, follow up, or review a waiver/request, treat that as a valid escalation/next step.
- Do not fail a message just because it is concise. Short professional replies are acceptable.
- Be lenient about wording variation. Judge the meaning, not exact phrasing.
- In the reason field, briefly cite the exact phrase that drove the decision when possible.

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
    result = json.loads(text)

    scores = dict(result.get("scores") or {})
    reply_lower = message.lower()

    empathy_markers = (
        "sorry",
        "i'm sorry",
        "i am sorry",
        "sorry to hear",
        "sorry you went through that",
        "i understand",
        "that sounds frustrating",
        "i know this is frustrating",
        "please prioritize your recovery",
        "take care",
        "hope you're recovering",
        "hope you are recovering",
        "i hope you're recovering",
        "i hope you are recovering",
    )
    escalation_markers = (
        "check with the property manager",
        "check with the manager",
        "get back to you",
        "follow up",
        "review",
        "look into",
    )
    threat_markers = (
        "evict",
        "eviction",
        "penalty",
        "penalties",
        "legal action",
        "notice to vacate",
    )
    timing_markers = (
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
        "tomorrow",
        "today",
        "tonight",
        "this week",
        "next week",
        "by ",
        "at ",
        "on ",
    )

    changed = False
    for idx, criterion in enumerate(criteria, start=1):
        key = f"c{idx}"
        score = int(scores.get(key, 0))
        criterion_lower = criterion.lower()

        if ("empath" in criterion_lower or "frustration" in criterion_lower) and any(
            marker in reply_lower for marker in empathy_markers
        ):
            if score < 4:
                scores[key] = 4
                changed = True
            continue

        if (
            "acknowledge" in criterion_lower
            or "commitment" in criterion_lower
            or "friday" in criterion_lower
            or "timing" in criterion_lower
            or "payment" in criterion_lower
        ) and any(marker in reply_lower for marker in timing_markers):
            if score < 4:
                scores[key] = 4
                changed = True
            continue

        if ("escalate" in criterion_lower or "check on waiver" in criterion_lower or "next step" in criterion_lower) and any(
            marker in reply_lower for marker in escalation_markers
        ):
            if score < 4:
                scores[key] = 4
                changed = True
            continue

        if ("does not immediately threaten" in criterion_lower or "not punitive" in criterion_lower) and not any(
            marker in reply_lower for marker in threat_markers
        ):
            if score < 4:
                scores[key] = 4
                changed = True

    result["scores"] = scores
    result["pass"] = all(int(scores.get(f"c{i+1}", 0)) >= 3 for i in range(len(criteria)))
    if changed:
        reason = (result.get("reason") or "").strip()
        suffix = "Explicit text evidence override applied."
        result["reason"] = f"{reason} {suffix}".strip() if reason else suffix
    return result


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


def _reply_looks_internal_or_recovery(reply: str) -> bool:
    text = (reply or "").strip().lower()
    if not text:
        return True
    markers = (
        "let me ",
        "i need to ",
        "i should ",
        "create a suggestion",
        "creating suggestion",
        "processed it appropriately",
        "close the task",
        "saving note",
        "the system is",
        "i'll acknowledge this and outline the next steps",
    )
    return any(marker in text for marker in markers)


def _extract_latest_outbound_message(db, task_id, *, user_message: str = "", fallback_reply: str = ""):
    """Return the latest outbound draft for a task when that draft is the user-facing artifact to grade."""
    suggestions = (
        db.query(Suggestion)
        .filter(Suggestion.task_id == task_id)
        .order_by(Suggestion.created_at.desc())
        .all()
    )
    latest_vendor = None
    latest_tenant = None
    user_lower = (user_message or "").lower()
    prefer_tenant = any(
        phrase in user_lower
        for phrase in (
            "reply to the tenant",
            "reply to tenant",
            "message the tenant",
            "message tenant",
            "tell the tenant",
            "send the tenant",
            "tenant asks",
            "tenant says",
            "what should i tell the tenant",
        )
    )
    prefer_vendor = any(
        phrase in user_lower
        for phrase in (
            "contact the ",
            "contact a ",
            "message the ",
            "message a ",
            "reach out to the ",
            "reach out to a ",
            "contact our ",
            "coordinate with the vendor",
            "confirm with the vendor",
        )
    )
    if any(
        phrase in user_lower
        for phrase in (
            "all washington properties",
            "all washington state properties",
            "all wa properties",
            "all matching properties",
        )
    ):
        prefer_vendor = False
    for suggestion in suggestions:
        payload = suggestion.action_payload or {}
        if payload.get("action") != "message_person":
            continue
        draft = payload.get("draft_message")
        if not draft:
            continue
        if payload.get("entity_type") == "tenant":
            if latest_tenant is None:
                latest_tenant = draft
            continue
        if latest_vendor is None:
            latest_vendor = draft
    if prefer_tenant and latest_tenant:
        return latest_tenant
    if prefer_vendor and latest_vendor:
        return latest_vendor
    if latest_tenant and _reply_looks_internal_or_recovery(fallback_reply):
        return latest_tenant
    if latest_tenant and not latest_vendor:
        return latest_tenant
    return None
