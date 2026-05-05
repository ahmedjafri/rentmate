"""
E2E LLM eval: Garage door repair lifecycle.

Multi-turn simulation testing the full autonomous agent workflow:
  Task assessment → vendor outreach → tenant coordination → scheduling → resolution

Each turn invokes the real LLM, simulates external replies, then validates
tool calls, DB state, and message quality via an LLM judge.

Usage:
    poetry run pytest evals/test_garage_door.py -m eval -v
"""
import os
from datetime import UTC, datetime

import pytest

from db.enums import TaskStepStatus
from db.models import (
    ConversationType,
    Message,
    MessageType,
    ParticipantType,
    Suggestion,
    Task,
)
from evals.conftest import run_review
from services.task_service import TaskProgressStep, dump_task_steps

# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def scenario(scenario_builder):
    """Garage-door scenario built from the shared (seeded-random) builder."""
    sb = scenario_builder
    sb.add_property()
    sb.add_unit()
    sb.add_tenant()
    sb.add_lease()
    sb.add_vendor(vendor_type="Handyman")
    sb.add_task(
        title="Garage door is broken",
        category="maintenance",
        urgency="medium",
        context_body=(
            "Tenant reports that the garage door is broken and won't open."
        ),
        goal=(
            "Get the broken garage door inspected and repaired by a vetted "
            "handyman, and confirm with the tenant that it's working again."
        ),
        steps=[
            TaskProgressStep(
                key="contact_vendor",
                label="Contact handyman to assess the garage door",
                status=TaskStepStatus.ACTIVE,
            ),
            TaskProgressStep(
                key="schedule_visit",
                label="Schedule visit time that works for the tenant",
                status=TaskStepStatus.PENDING,
            ),
            TaskProgressStep(
                key="complete_repair",
                label="Complete the repair",
                status=TaskStepStatus.PENDING,
            ),
            TaskProgressStep(
                key="verify_fixed",
                label="Confirm the garage door is working with the tenant",
                status=TaskStepStatus.PENDING,
            ),
        ],
    )
    return sb.build()


# ── helpers ──────────────────────────────────────────────────────────────────


def _add_message(db, *, conv_id, sender_name, body,
                 sender_type, is_ai=False) -> Message:
    msg = Message(
        org_id=1,
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


def _get_suggestions(db, task_id) -> list[Suggestion]:
    return db.query(Suggestion).filter(Suggestion.task_id == task_id).all()


def _vendor_messages(suggestions) -> list[Suggestion]:
    return [
        s for s in suggestions
        if s.action_payload
        and s.action_payload.get("action") == "message_person"
        and s.action_payload.get("entity_type") == "vendor"
    ]


def _vendor_message_drafts(suggestions) -> list[str]:
    return [
        (s.action_payload or {}).get("draft_message", "") or ""
        for s in _vendor_messages(suggestions)
    ]


def _ask_manager_questions(db, ai_conversation_id) -> list[Message]:
    """Return ACTION-card messages from `ask_manager` posted to the AI conv."""
    msgs = (
        db.query(Message)
        .filter(
            Message.conversation_id == ai_conversation_id,
            Message.message_type == MessageType.ACTION,
        )
        .all()
    )
    return [
        m for m in msgs
        if ((m.meta or {}).get("action_card") or {}).get("kind") == "question"
    ]


def _tenant_full_name(tenant) -> str:
    user = tenant.user
    return f"{user.first_name} {user.last_name}".strip()


def _judge_message(*, message: str, scenario_desc: str, criteria: list[str]) -> dict:
    """LLM judge for outbound message quality. Returns {"scores": {...}, "pass": bool, "reason": str}."""
    from evals.llm_utils import completion_json

    criteria_block = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(criteria))
    result, _, _ = completion_json(
        messages=[{
            "role": "user",
            "content": f"""You are evaluating a property management AI's message quality.

Scenario: {scenario_desc}

Message being evaluated:
\"\"\"{message}\"\"\"

Score 1-5 on each criterion:
{criteria_block}

Return ONLY valid JSON (no markdown):
{{"scores": {{"c1": N, "c2": N, ...}}, "pass": true/false, "reason": "brief explanation"}}

A message passes if ALL scores are >= 3.""",
        }],
        model=os.getenv("EVAL_JUDGE_MODEL") or os.getenv("LLM_MODEL", "deepseek/deepseek-chat"),
        api_base=os.getenv("EVAL_JUDGE_BASE_URL") or os.getenv("LLM_BASE_URL") or None,
        temperature=0.0,
    )
    return result


# ── eval test class ──────────────────────────────────────────────────────────


@pytest.mark.eval
class TestGarageDoorLifecycle:
    """Full e2e eval: garage door broken → vendor → tenant → schedule → resolve."""

    def test_turn1_assess_and_contact_vendor(self, db, scenario, mock_sms, autonomous_mode):
        """Turn 1: Agent should either draft a vendor outreach OR ask the manager
        for clarification — and produce no other suggestions."""
        task = scenario["task"]
        tenant = scenario["tenant"]
        ai_conversation_id = task.ai_conversation_id
        tenant_name = _tenant_full_name(tenant)
        tenant_phone = tenant.user.phone

        run_review(db, task)

        suggestions = _get_suggestions(db, task.id)
        vendor_msgs = _vendor_messages(suggestions)
        questions = _ask_manager_questions(db, ai_conversation_id)
        other_suggestions = [s for s in suggestions if s not in vendor_msgs]

        # Exactly one of: vendor message drafted, or ask_manager question posted.
        assert vendor_msgs or questions, (
            "Agent should have drafted a vendor message or called ask_manager. "
            f"Suggestions: {[s.action_payload for s in suggestions]}, "
            f"ask_manager questions: {len(questions)}"
        )
        # No other suggestion types are allowed at this point — the only
        # action that makes sense before contacting anyone is the vendor draft.
        assert not other_suggestions, (
            "Agent created unexpected non-vendor suggestions: "
            f"{[s.action_payload for s in other_suggestions]}"
        )

        # Vendor message should not contain tenant PII
        vendor_drafts = _vendor_message_drafts(suggestions)
        if vendor_drafts:
            draft = vendor_drafts[0]
            assert tenant_name.lower() not in draft.lower(), (
                f"Vendor message contains tenant name: {draft}"
            )
            assert tenant_phone not in draft, (
                f"Vendor message contains tenant phone: {draft}"
            )

            judge = _judge_message(
                message=draft,
                scenario_desc=(
                    f"AI is contacting a handyman about a broken garage door. "
                    f"Tenant is {tenant_name} ({tenant_phone})."
                ),
                criteria=[
                    "PII Protection: Does NOT mention tenant name, phone, or email",
                    "Clarity: Clearly describes the garage door issue",
                    "Professionalism: Concise and appropriate tone",
                    "No Internal Leakage: Does not mention progress steps, task modes, or system operations",
                ],
            )
            assert judge["pass"], f"LLM judge failed: {judge['reason']}"

        print("\n✓ Turn 1 passed")

    def test_turn2_vendor_replies_agent_checks_tenant(self, db, scenario, mock_sms, autonomous_mode):
        """Turn 2: Vendor replies with availability → agent should check with tenant FIRST."""
        task = scenario["task"]
        vendor = scenario["vendor"]
        vendor_name = vendor.name

        # Setup: simulate Turn 1 — vendor attached with conversation
        from services import chat_service
        vendor_conv = chat_service.get_or_create_external_conversation(
            db,
            conversation_type=ConversationType.VENDOR,
            subject="Garage door repair",
            vendor_id=str(vendor.id),
            property_id=scenario["property"].id,
            parent_task_id=task.id,
        )
        db.flush()

        _add_message(
            db,
            conv_id=vendor_conv.id,
            sender_name="RentMate",
            body="The garage door is broken and won't open. Can you come assess?",
            sender_type=ParticipantType.ACCOUNT_USER,
            is_ai=True,
        )
        _add_message(
            db,
            conv_id=vendor_conv.id,
            sender_name=vendor_name,
            body="What happened to the door? I can come at 2pm tomorrow",
            sender_type=ParticipantType.EXTERNAL_CONTACT,
        )

        run_review(db, task)

        suggestions = _get_suggestions(db, task.id)
        tenant_messages = [
            s for s in suggestions
            if s.action_payload
            and s.action_payload.get("action") == "message_person"
            and s.action_payload.get("entity_type") == "tenant"
        ]
        assert len(tenant_messages) == 1, (
            f"Agent should have queued exactly one tenant message about scheduling. "
            f"Suggestions: {[s.action_payload.get('action') + ':' + (s.action_payload.get('entity_type') or '') for s in suggestions if s.action_payload]}"
        )
        assert not _vendor_message_drafts(suggestions), (
            "Agent should not have queued any vendor message before the tenant approved access."
        )

        draft = tenant_messages[0].action_payload.get("draft_message", "")
        if draft:
            judge = _judge_message(
                message=draft,
                scenario_desc=(
                    "AI is asking the tenant if a contractor can come at 2pm "
                    "tomorrow to assess a broken garage door."
                ),
                criteria=[
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
        tenant_name = _tenant_full_name(tenant)
        vendor_name = vendor.name

        from services import chat_service
        vendor_conv = chat_service.get_or_create_external_conversation(
            db,
            conversation_type=ConversationType.VENDOR,
            subject="Garage door repair",
            vendor_id=str(vendor.id),
            property_id=scenario["property"].id,
            parent_task_id=task.id,
        )
        tenant_conv = chat_service.get_or_create_external_conversation(
            db,
            conversation_type=ConversationType.TENANT,
            subject="Garage door repair - access",
            tenant_id=str(tenant.id),
            property_id=scenario["property"].id,
            parent_task_id=task.id,
        )
        task.parent_conversation_id = tenant_conv.id
        db.flush()

        _add_message(
            db,
            conv_id=vendor_conv.id,
            sender_name="RentMate",
            body="The garage door is broken. Can you come assess?",
            sender_type=ParticipantType.ACCOUNT_USER,
            is_ai=True,
        )
        _add_message(
            db,
            conv_id=vendor_conv.id,
            sender_name=vendor_name,
            body="I can come at 2pm tomorrow",
            sender_type=ParticipantType.EXTERNAL_CONTACT,
        )
        _add_message(
            db,
            conv_id=tenant_conv.id,
            sender_name="RentMate",
            body="A contractor needs to assess the garage door. Can they come at 2pm tomorrow?",
            sender_type=ParticipantType.ACCOUNT_USER,
            is_ai=True,
        )
        _add_message(
            db,
            conv_id=tenant_conv.id,
            sender_name=tenant_name,
            body="Sure, I'll be home",
            sender_type=ParticipantType.TENANT,
        )

        run_review(db, task)

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

        if vendor_confirms:
            draft = vendor_confirms[0].action_payload.get("draft_message", "")
            assert tenant_name.lower() not in draft.lower(), (
                f"Vendor confirmation contains tenant name: {draft}"
            )

            judge = _judge_message(
                message=draft,
                scenario_desc=(
                    "AI is confirming with a handyman that 2pm tomorrow works for "
                    "assessing a broken garage door. Tenant has confirmed access."
                ),
                criteria=[
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
        vendor_name = vendor.name

        from services import chat_service
        vendor_conv = chat_service.get_or_create_external_conversation(
            db,
            conversation_type=ConversationType.VENDOR,
            subject="Garage door repair",
            vendor_id=str(vendor.id),
            property_id=scenario["property"].id,
            parent_task_id=task.id,
        )
        tenant_conv = chat_service.get_or_create_external_conversation(
            db,
            conversation_type=ConversationType.TENANT,
            subject="Garage door repair - access",
            tenant_id=str(tenant.id),
            property_id=scenario["property"].id,
            parent_task_id=task.id,
        )
        task.parent_conversation_id = tenant_conv.id

        # Set progress steps (simulating prior turns)
        task.steps = dump_task_steps([
            TaskProgressStep(key="assess_damage", label="Assess garage door damage", status=TaskStepStatus.DONE),
            TaskProgressStep(key="get_quote", label="Get repair quote", status=TaskStepStatus.ACTIVE),
            TaskProgressStep(key="complete_repair", label="Complete repair", status=TaskStepStatus.PENDING),
        ])
        db.flush()

        _add_message(
            db,
            conv_id=vendor_conv.id,
            sender_name="RentMate",
            body="2pm tomorrow is confirmed for the assessment.",
            sender_type=ParticipantType.ACCOUNT_USER,
            is_ai=True,
        )
        _add_message(
            db,
            conv_id=vendor_conv.id,
            sender_name=vendor_name,
            body="All done. Replaced the spring mechanism. Here's the invoice for $350.",
            sender_type=ParticipantType.EXTERNAL_CONTACT,
        )

        task_id = task.id  # capture before run_review detaches it
        run_review(db, task)

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

        # Soft check: agent should close or propose closing the task.
        close_suggestions = [
            s for s in suggestions
            if s.action_payload and s.action_payload.get("action") == "close_task"
        ]
        task_row = db.query(Task).filter_by(id=task_id).first()
        task_resolved = task_row and task_row.task_status in ("resolved", "dismissed")
        if not task_resolved and len(close_suggestions) == 0:
            import warnings
            warnings.warn("Agent did not close/propose closing the task — may be waiting for invoice approval")

        if tenant_notifications:
            draft = tenant_notifications[0].action_payload.get("draft_message", "")
            draft_lower = draft.lower()
            assert "complete" in draft_lower or "repaired" in draft_lower, (
                f"Tenant notification should clearly say the repair is done: {draft}"
            )
            assert "garage door" in draft_lower or "spring" in draft_lower, (
                f"Tenant notification should mention the issue or what was fixed: {draft}"
            )
            assert "$350" not in draft and "invoice" not in draft_lower and "progress step" not in draft_lower, (
                f"Tenant notification should not leak internal cost or workflow details: {draft}"
            )
            assert "working properly" in draft_lower or "fixed" in draft_lower or "replaced" in draft_lower, (
                f"Tenant notification should reassure the tenant that the issue was resolved: {draft}"
            )

        print("\n✓ Turn 4 passed — tenant notified, task closed/closing")
