"""Multi-actor end-to-end eval harness.

Drives multi-turn conversations between RentMate's agent and one or more
persona-shaped actors (tenants, vendors, owners). Each turn:

  1. ``run_review`` invokes the agent against the test DB.
  2. For each ``Actor``, the harness scans the conversations linked to
     ``task`` where the actor is a participant for new manager-side
     messages (``is_ai=True`` and ``sender_type=ACCOUNT_USER``).
  3. For each new message, the harness generates a persona-shaped reply
     using ``demo.simulator.generate_persona_reply`` and inserts it via
     ``add_message`` so the agent reads it on the next turn.

The loop terminates when the goal predicate passes, the agent goes idle
(no new outbound messages and no actor reply produced), or ``max_turns``
is reached. Optionally an LLM judge evaluates the full transcript.

Failure-mode goals are supported via ``Goal.is_failure_goal=True`` —
"success" then means the failure condition was satisfied (e.g., tenant
remained dissatisfied, vendor declined and agent never re-routed).

Requirements:
  - Tests must use the ``e2e_autonomous_mode`` fixture (or equivalent
    AGGRESSIVE outbound_messages policy) so agent replies auto-send into
    actor conversations as Message rows.
  - LLM-driven; mark tests ``@pytest.mark.eval`` so the default test run
    skips them.
"""
from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from db.models import (
    Conversation,
    ConversationParticipant,
    Message,
    MessageType,
    ParticipantType,
    Suggestion,
    Task,
    User,
)
from demo.personalities import Personality
from demo.simulator import build_persona_history, generate_persona_reply, reset_reply_usage
from agent.time import current_utc
from evals.conftest import add_message, run_review
from evals.harness import safe_id
from evals.state_snapshots import write_state_snapshot

# ── data structures ──────────────────────────────────────────────────────────


@dataclass
class Actor:
    """A persona-driven participant in a multi-turn scenario.

    The harness identifies which conversations belong to this actor by
    walking ``ConversationParticipant`` rows where ``user_id == user.id``
    and the conversation's ``parent_task_id`` matches the scenario task.
    Conversations are discovered dynamically — the agent may create new
    ones during a run (e.g., outreach to a different vendor) and the
    harness will pick them up on the next turn.
    """
    name: str
    user: User
    persona: Personality
    conversation_type: str  # "tenant" | "vendor" | "owner"
    property_name: str | None = None
    _seen_message_ids: set[int] = field(default_factory=set)


@dataclass
class Goal:
    description: str
    predicate: Callable[[Session, Task], bool] | None = None
    is_failure_goal: bool = False


@dataclass
class TranscriptEntry:
    turn: int
    actor: str  # "agent" | actor.name
    body: str
    conversation_id: int


@dataclass
class ScenarioResult:
    turns_taken: int
    terminated_by: str  # "predicate" | "judge" | "agent_idle" | "max_turns"
    success: bool
    judge_reason: str | None
    transcript: list[TranscriptEntry]


# ── main entry point ─────────────────────────────────────────────────────────


def run_e2e_scenario(
    db: Session,
    *,
    task: Task,
    actors: list[Actor],
    goal: Goal,
    max_turns: int = 6,
    judge_at_end: bool = True,
    turn_time_advance: timedelta | None = timedelta(minutes=15),
) -> ScenarioResult:
    """Drive the agent ↔ actors loop until the goal is met or we stop."""
    transcript: list[TranscriptEntry] = []
    consecutive_idle = 0
    last_outbound_count = _count_outbound_messages(db, task)
    last_suggestion_count = _count_suggestions(db, task)
    clock = _ScenarioClock(turn_time_advance)
    reset_reply_usage()

    with clock:
        for turn in range(1, max_turns + 1):
            clock.set_turn(db, task=task, turn=turn)
            # 1. Agent turn.
            run_review(db, task)
            # ``run_review`` calls ``db.expire_all()`` in its finally block, so
            # ``task`` is now detached. Re-load it for predicate calls.
            task = db.query(Task).filter(Task.id == task.id).one()

            # 2. Snapshot what the agent produced this turn.
            outbound_count = _count_outbound_messages(db, task)
            suggestion_count = _count_suggestions(db, task)
            new_outbound = outbound_count > last_outbound_count
            new_suggestions = suggestion_count > last_suggestion_count

            # 3. For each actor, find new manager-side messages and reply.
            any_actor_replied = False
            for actor in actors:
                new_msgs = _new_inbound_for_actor(db, task, actor)
                for msg in new_msgs:
                    transcript.append(TranscriptEntry(
                        turn=turn, actor="agent", body=msg.body or "",
                        conversation_id=msg.conversation_id,
                    ))
                    reply_body = _generate_actor_reply(
                        db,
                        actor=actor,
                        conversation_id=msg.conversation_id,
                    )
                    _post_actor_reply(
                        db,
                        actor=actor,
                        conversation_id=msg.conversation_id,
                        body=reply_body,
                    )
                    transcript.append(TranscriptEntry(
                        turn=turn, actor=actor.name, body=reply_body,
                        conversation_id=msg.conversation_id,
                    ))
                    actor._seen_message_ids.add(msg.id)
                    any_actor_replied = True

            # 4. Predicate check (after the agent and actors have moved).
            _write_turn_snapshot(db, task=task, turn=turn)

            if goal.predicate is not None:
                try:
                    hit = bool(goal.predicate(db, task))
                except Exception:
                    hit = False
                if hit and not goal.is_failure_goal:
                    return _build_result(
                        turns_taken=turn,
                        terminated_by="predicate",
                        success=True,
                        judge_reason=None,
                        transcript=transcript,
                    )

            # 5. Idle detection. If neither the agent nor any actor produced
            # new content, count it as an idle turn. Two idle turns in a row
            # → terminate. A single idle turn is tolerated because the agent
            # may need a tick to react to a fresh actor reply.
            if not new_outbound and not new_suggestions and not any_actor_replied:
                consecutive_idle += 1
                if consecutive_idle >= 2:
                    return _finalize(
                        db,
                        task=task,
                        goal=goal,
                        turns_taken=turn,
                        terminated_by="agent_idle",
                        transcript=transcript,
                        judge_at_end=judge_at_end,
                    )
            else:
                consecutive_idle = 0

            last_outbound_count = outbound_count
            last_suggestion_count = suggestion_count

        return _finalize(
            db,
            task=task,
            goal=goal,
            turns_taken=max_turns,
            terminated_by="max_turns",
            transcript=transcript,
            judge_at_end=judge_at_end,
        )


# ── transcript / output helpers ──────────────────────────────────────────────


def format_transcript(entries: list[TranscriptEntry]) -> str:
    """Pretty-print a transcript for assertion error messages."""
    out = []
    for e in entries:
        out.append(f"[turn {e.turn} conv={e.conversation_id} {e.actor}] {e.body}")
    return "\n".join(out) or "(no transcript)"


# ── internals ────────────────────────────────────────────────────────────────


def _count_outbound_messages(db: Session, task: Task) -> int:
    """Count agent-sent messages across all task-linked external conversations."""
    return (
        db.query(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .filter(
            Conversation.parent_task_id == task.id,
            Conversation.conversation_type.in_(["tenant", "vendor", "owner"]),
            Message.sender_type == ParticipantType.ACCOUNT_USER,
            Message.is_ai.is_(True),
        )
        .count()
    )


def _count_suggestions(db: Session, task: Task) -> int:
    return db.query(Suggestion).filter(Suggestion.task_id == task.id).count()


class _ScenarioClock:
    appointment_buffer = timedelta(hours=1)

    def __init__(self, turn_time_advance: timedelta | None):
        self.turn_time_advance = turn_time_advance
        self.base = _parse_eval_start() or current_utc()
        self.now = self.base
        self.previous = os.environ.get("RENTMATE_EVAL_NOW_UTC")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.previous is None:
            os.environ.pop("RENTMATE_EVAL_NOW_UTC", None)
        else:
            os.environ["RENTMATE_EVAL_NOW_UTC"] = self.previous

    def set_turn(self, db: Session, *, task: Task, turn: int) -> None:
        if self.turn_time_advance is None:
            return
        if turn == 1:
            self.now = self.base
        else:
            fallback = self.now + self.turn_time_advance
            appointment = _next_confirmed_appointment(db, task=task, after=self.now)
            self.now = max(fallback, appointment + self.appointment_buffer) if appointment else fallback
        os.environ["RENTMATE_EVAL_NOW_UTC"] = self.now.astimezone(UTC).isoformat()


def _parse_eval_start() -> datetime | None:
    raw = os.getenv("RENTMATE_EVAL_START_UTC", "").strip()
    if not raw:
        return None
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


_APPOINTMENT_CUE = re.compile(
    r"\b("
    r"i(?:'|’)ll be there|i will be there|will be there|"
    r"i(?:'|’)ll head over|i will head over|head over now|heading over|"
    r"confirmed|is confirmed|scheduled|go ahead|plan to head over|"
    r"will make sure i(?:'|’)m here|tenant will be there"
    r")\b",
    re.IGNORECASE,
)
_TIME_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.IGNORECASE)
_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _next_confirmed_appointment(db: Session, *, task: Task, after: datetime) -> datetime | None:
    messages = (
        db.query(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .filter(Conversation.parent_task_id == task.id)
        .order_by(Message.sent_at.asc())
        .all()
    )
    candidates = []
    for message in messages:
        body = message.body or ""
        reference = _aware_utc(message.sent_at) or after
        candidates.extend(_confirmed_appointment_times(body, reference=reference))
    future = [candidate for candidate in candidates if candidate > after]
    return min(future) if future else None


def _confirmed_appointment_times(text: str, *, reference: datetime) -> list[datetime]:
    normalized = " ".join((text or "").lower().split())
    if not normalized or not _APPOINTMENT_CUE.search(normalized):
        return []

    if re.search(r"\bnow\b", normalized) and re.search(r"\b(head|heading|come|coming|swing)\b", normalized):
        return [reference + timedelta(hours=2)]

    candidates: list[datetime] = []
    for match in _TIME_RE.finditer(normalized):
        hour = int(match.group(1))
        minute = int(match.group(2) or "0")
        meridiem = match.group(3).lower()
        if meridiem == "pm" and hour != 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
        candidates.append(_combine_schedule_date(_schedule_date(normalized, reference), hour, minute))

    if not candidates and "afternoon" in normalized:
        candidates.append(_combine_schedule_date(_schedule_date(normalized, reference), 17, 0))
    if not candidates and "morning" in normalized:
        candidates.append(_combine_schedule_date(_schedule_date(normalized, reference), 12, 0))
    return candidates


def _schedule_date(text: str, reference: datetime) -> date:
    base = reference.astimezone(UTC).date()
    if "tomorrow" in text:
        return base + timedelta(days=1)
    for name, weekday in _WEEKDAYS.items():
        if re.search(rf"\b(?:this|next)?\s*{name}\b", text):
            days = (weekday - base.weekday()) % 7
            if "next " in text and days == 0:
                days = 7
            return base + timedelta(days=days)
    return base


def _eval_display_tz() -> ZoneInfo:
    return ZoneInfo(os.getenv("RENTMATE_EVAL_DISPLAY_TZ", "America/Los_Angeles"))


def _combine_schedule_date(day: date, hour: int, minute: int) -> datetime:
    local = datetime.combine(day, time(hour=hour, minute=minute), tzinfo=_eval_display_tz())
    return local.astimezone(UTC)


def _aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _write_turn_snapshot(db: Session, *, task: Task, turn: int) -> None:
    if not os.getenv("RENTMATE_EVAL_WRITE_ARTIFACTS"):
        return
    case_id = os.getenv("PYTEST_CURRENT_TEST", "").split(" ", 1)[0] or "unknown"
    artifact_root = Path(os.getenv("RENTMATE_EVAL_ARTIFACT_ROOT", "eval-runs"))
    trial = int(os.getenv("RENTMATE_EVAL_TRIAL_INDEX", "1"))
    snapshot_dir = artifact_root / safe_id(case_id) / f"trial-{trial:03d}" / "state_snapshots"
    db.flush()
    write_state_snapshot(
        db,
        snapshot_dir=snapshot_dir,
        case_id=case_id,
        trial=trial,
        turn=turn,
        task_id=task.id,
    )


def _new_inbound_for_actor(
    db: Session, task: Task, actor: Actor,
) -> list[Message]:
    """Return manager-side messages in actor's conversations not yet replied to.

    A conversation belongs to this actor if it's linked to the task
    (``parent_task_id``), is the right type, and has the actor's user as
    an active participant.
    """
    conv_ids = [
        cid
        for (cid,) in db.query(Conversation.id)
        .join(
            ConversationParticipant,
            ConversationParticipant.conversation_id == Conversation.id,
        )
        .filter(
            Conversation.parent_task_id == task.id,
            Conversation.conversation_type == actor.conversation_type,
            ConversationParticipant.user_id == actor.user.id,
            ConversationParticipant.is_active.is_(True),
        )
        .all()
    ]
    if not conv_ids:
        return []

    msgs = (
        db.query(Message)
        .filter(
            Message.conversation_id.in_(conv_ids),
            Message.sender_type == ParticipantType.ACCOUNT_USER,
            Message.is_ai.is_(True),
            Message.message_type == MessageType.MESSAGE,
        )
        .order_by(Message.sent_at)
        .all()
    )
    fresh = [m for m in msgs if m.id not in actor._seen_message_ids and (m.body or "").strip()]
    return fresh


def _generate_actor_reply(
    db: Session, *, actor: Actor, conversation_id: int,
) -> str:
    """Build the persona prompt + call the LLM for a single reply."""
    visible = (
        db.query(Message)
        .filter(
            Message.conversation_id == conversation_id,
            Message.message_type == MessageType.MESSAGE,
        )
        .order_by(Message.sent_at)
        .all()
    )
    history = build_persona_history(visible)
    return asyncio.run(generate_persona_reply(
        user=actor.user,
        personality=actor.persona,
        conversation_type=actor.conversation_type,
        property_name=actor.property_name,
        history=history,
        raise_on_failure=True,
    ))


def _post_actor_reply(
    db: Session, *, actor: Actor, conversation_id: int, body: str,
) -> Message:
    sender_type = (
        ParticipantType.TENANT if actor.conversation_type == "tenant"
        else ParticipantType.EXTERNAL_CONTACT  # vendor or owner
    )
    sender_name = " ".join(
        filter(None, [actor.user.first_name, actor.user.last_name])
    ) or actor.name
    msg = add_message(
        db,
        conversation_id,
        sender_name,
        body,
        sender_type,
        is_ai=False,
    )
    db.commit()
    return msg


def _finalize(
    db: Session,
    *,
    task: Task,
    goal: Goal,
    turns_taken: int,
    terminated_by: str,
    transcript: list[TranscriptEntry],
    judge_at_end: bool,
) -> ScenarioResult:
    """Resolve final success state, optionally via the LLM judge."""
    predicate_hit = False
    if goal.predicate is not None:
        try:
            predicate_hit = bool(goal.predicate(db, task))
        except Exception:
            predicate_hit = False

    if goal.is_failure_goal:
        # Failure goals: the failure condition holding == success.
        if goal.predicate is not None:
            return _build_result(
                turns_taken=turns_taken,
                terminated_by=terminated_by,
                success=predicate_hit,
                judge_reason=None,
                transcript=transcript,
            )
        if judge_at_end:
            verdict, reason = _judge_goal(goal, transcript)
            return _build_result(
                turns_taken=turns_taken,
                terminated_by="judge",
                success=verdict,
                judge_reason=reason,
                transcript=transcript,
            )
        return _build_result(
            turns_taken=turns_taken,
            terminated_by=terminated_by,
            success=False,
            judge_reason="No predicate or judge configured for failure goal.",
            transcript=transcript,
        )

    # Success goals.
    if predicate_hit:
        return _build_result(
            turns_taken=turns_taken,
            terminated_by="predicate",
            success=True,
            judge_reason=None,
            transcript=transcript,
        )
    if judge_at_end:
        verdict, reason = _judge_goal(goal, transcript)
        return _build_result(
            turns_taken=turns_taken,
            terminated_by="judge" if verdict else terminated_by,
            success=verdict,
            judge_reason=reason,
            transcript=transcript,
        )
    return _build_result(
        turns_taken=turns_taken,
        terminated_by=terminated_by,
        success=False,
        judge_reason=None,
        transcript=transcript,
    )


def _build_result(
    *,
    turns_taken: int,
    terminated_by: str,
    success: bool,
    judge_reason: str | None,
    transcript: list[TranscriptEntry],
) -> ScenarioResult:
    return ScenarioResult(
        turns_taken=turns_taken,
        terminated_by=terminated_by,
        success=success,
        judge_reason=judge_reason,
        transcript=transcript,
    )


def _judge_goal(goal: Goal, transcript: list[TranscriptEntry]) -> tuple[bool, str]:
    """LLM judge: did the agent achieve ``goal.description``?

    Returns (verdict_bool, reason_string).
    """
    from evals.llm_utils import completion_json

    transcript_text = format_transcript(transcript) or "(empty transcript)"
    framing = (
        "the FAILURE condition described below held by the end of the run"
        if goal.is_failure_goal
        else "the SUCCESS condition described below was satisfied by the end of the run"
    )
    prompt = f"""You are evaluating a property-management AI agent against a goal.

Goal description:
{goal.description}

Question: did {framing}? Reply ONLY with valid JSON of the form:
{{"verdict": true|false, "reason": "brief explanation grounded in the transcript"}}

Transcript (each line is a turn; "agent" is RentMate, others are
persona-driven actors):

{transcript_text}
"""
    try:
        result, _, _ = completion_json(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
    except Exception as exc:
        return False, f"judge LLM call failed: {exc}"
    verdict = bool(result.get("verdict", False))
    reason = str(result.get("reason", "") or "")
    return verdict, reason


__all__ = [
    "Actor",
    "Goal",
    "ScenarioResult",
    "TranscriptEntry",
    "format_transcript",
    "run_e2e_scenario",
]


# Helper for tests that don't need a real Conversation record yet (the
# unit test patches ``run_review``); kept here so the public surface
# stays small.
def _ensure_user(user: Any) -> User:  # pragma: no cover - defensive
    if not isinstance(user, User):
        raise TypeError(f"Actor.user must be a User row, got {type(user)!r}")
    return user
