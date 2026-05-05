"""Shared fixtures for eval tests.

Provides DB session, scenario builder, agent runner, and LLM judge.
All eval tests should use these instead of rolling their own.
"""
import asyncio
import json
import os
import random
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from db.enums import ActionPolicyLevel, TaskCategory, TaskMode, TaskSource, TaskStatus, Urgency
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
from evals.harness import append_jsonl, safe_id, utc_now_iso, weighted_score, write_json
from services.number_allocator import NumberAllocator

DEFAULT_ACCOUNT_ID = 1

# Keep eval runs aligned with CI and avoid parallel Chroma crashes.
os.environ.setdefault("RENTMATE_DISABLE_VECTOR_INDEX", "1")
os.environ.setdefault("RENTMATE_DISABLE_ASYNC_NOTIFICATIONS", "1")


def pytest_configure(config):
    agent_model = os.getenv("LLM_MODEL", "openai/gpt-4o-mini")
    judge_model = os.getenv("EVAL_JUDGE_MODEL") or agent_model
    api_key = os.getenv("LLM_API_KEY", "")
    masked = f"{api_key[:4]}...{api_key[-4:]}" if len(api_key) > 8 else ("(set)" if api_key else "(NOT SET)")
    base_url = os.getenv("LLM_BASE_URL") or "(default)"
    print(f"\n[evals] agent_model={agent_model}, judge_model={judge_model}, base_url={base_url}, api_key={masked}")


def _format_eval_debug_payload(payload: dict | None) -> str:
    if not payload:
        return "No Hermes debug payload captured."
    return "\n\n".join([
        f"model={payload.get('model')} provider={payload.get('provider')} api_base={payload.get('api_base')}",
        f"session_key={payload.get('session_key')} agent_id={payload.get('agent_id')}",
        "USER MESSAGE\n" + str(payload.get("user_message") or ""),
        "CONVERSATION HISTORY\n" + json.dumps(payload.get("conversation_history") or [], indent=2, ensure_ascii=False),
        "MEMORY CONTEXT\n" + str(payload.get("memory_context") or ""),
        "SYSTEM PROMPT\n" + str(payload.get("system_prompt") or ""),
    ])


_REPO_ROOT = Path(__file__).resolve().parents[1]
_EVAL_RUN_DUMP_DIR = Path(os.getenv("RENTMATE_EVAL_ARTIFACT_ROOT", str(_REPO_ROOT / "eval-runs")))
_FALSEY_ENV_VALUES = {"0", "false", "no", "off"}


def _eval_agent_output_enabled() -> bool:
    return os.getenv("RENTMATE_EVAL_PRINT_AGENT_OUTPUT", "1").lower() not in _FALSEY_ENV_VALUES


def _print_eval_agent_turn(user_message: str, result: dict) -> None:
    if not _eval_agent_output_enabled():
        return

    reply = str(result.get("reply") or "").strip()
    side_effects = result.get("side_effects") or []
    pending = result.get("pending_suggestions") or []

    print("\n[eval agent turn]")
    print(f"user: {user_message}")
    print(f"agent: {reply or '(empty reply)'}")
    if side_effects or pending:
        print(f"side_effects={len(side_effects)} pending_suggestions={len(pending)}")


def _latest_agent_step_message(run: dict) -> str:
    steps = run.get("steps") or []
    for step in reversed(steps):
        if step.get("source") == "agent" and step.get("message"):
            return str(step["message"])
    return ""


def _print_eval_agent_runs(case_id: str, trial_index: int, run_payload: dict) -> None:
    if not _eval_agent_output_enabled():
        return

    for run in run_payload.get("runs", []):
        final_response = str(run.get("final_response") or _latest_agent_step_message(run)).strip()
        if not final_response:
            continue
        run_id = run.get("run_id") or "(unknown run)"
        print(f"\n[eval agent final] {case_id} trial {trial_index} run {run_id}")
        print(final_response)


def _serialize_agent_run(run, traces, steps, trajectory) -> dict:
    return {
        "run_id": run.id,
        "source": run.source,
        "status": run.status,
        "task_id": run.task_id,
        "conversation_id": run.conversation_id,
        "model": run.model,
        "agent_version": run.agent_version,
        "execution_path": run.execution_path,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "ended_at": run.ended_at.isoformat() if run.ended_at else None,
        "iteration_count": run.iteration_count,
        "input_tokens": run.total_input_tokens,
        "output_tokens": run.total_output_tokens,
        "cost_cents": run.total_cost_cents,
        "trigger_input": run.trigger_input,
        "final_response": run.final_response,
        "error_message": run.error_message,
        "metadata": run.run_metadata,
        "traces": [
            {
                "sequence_num": t.sequence_num,
                "timestamp": t.timestamp.isoformat() if t.timestamp else None,
                "trace_type": t.trace_type,
                "source": t.source,
                "tool_name": t.tool_name,
                "summary": t.summary,
                "detail": t.detail,
                "input_tokens": t.input_tokens,
                "output_tokens": t.output_tokens,
                "model": t.model,
                "suggestion_id": t.suggestion_id,
            }
            for t in traces
        ],
        "steps": [
            {
                "step_id": s.step_id,
                "timestamp": s.timestamp.isoformat() if s.timestamp else None,
                "source": s.source,
                "message": s.message,
                "model_name": s.model_name,
                "reasoning_content": s.reasoning_content,
                "tool_calls": s.tool_calls,
                "observation": s.observation,
                "metrics": s.metrics,
                "extra": s.extra,
            }
            for s in steps
        ],
        "atif_trajectory": trajectory,
    }


def _collect_side_effects(db) -> dict:
    """Snapshot what the agent changed in the test DB.

    Captures: suggestions queued, ask_manager questions posted, outbound
    messages the agent drafted (in external conversations or the AI
    conversation), and the rows present in the entity tables. Lets a
    human reviewer see at a glance what the run actually did.
    """
    from db.models import (
        Conversation,
        Lease,
        Message,
        MessageType,
        Property,
        Suggestion,
        Task,
        Tenant,
        Unit,
        User,
    )

    suggestions = []
    for s in db.query(Suggestion).order_by(Suggestion.created_at).all():
        suggestions.append({
            "id": s.id,
            "task_id": s.task_id,
            "title": s.title,
            "status": getattr(s, "status", None),
            "category": getattr(s, "category", None),
            "risk_score": getattr(s, "risk_score", None),
            "action_payload": s.action_payload,
        })

    ask_manager_questions = []
    for m in (
        db.query(Message)
        .filter(Message.message_type == MessageType.ACTION)
        .order_by(Message.sent_at)
        .all()
    ):
        meta = m.meta or {}
        card = meta.get("action_card") or {}
        if card.get("kind") != "question":
            continue
        ask_manager_questions.append({
            "conversation_id": m.conversation_id,
            "sent_at": m.sent_at.isoformat() if m.sent_at else None,
            "question": m.body,
        })

    # Agent-authored messages outside the AI conversation (i.e. drafts
    # the agent actually sent into a vendor/tenant thread).
    outbound_messages = []
    for m in (
        db.query(Message)
        .filter(
            Message.is_ai.is_(True),
            Message.message_type.in_((MessageType.MESSAGE, MessageType.THREAD)),
        )
        .order_by(Message.sent_at)
        .all()
    ):
        convo = db.query(Conversation).filter_by(id=m.conversation_id).first()
        if convo is None:
            continue
        outbound_messages.append({
            "conversation_id": m.conversation_id,
            "conversation_type": (
                convo.conversation_type.value
                if hasattr(convo.conversation_type, "value")
                else str(convo.conversation_type)
            ),
            "sent_at": m.sent_at.isoformat() if m.sent_at else None,
            "sender_name": m.sender_name,
            "body": m.body,
        })

    properties = [
        {
            "id": p.id,
            "name": p.name,
            "address": " ".join(filter(None, [p.address_line1, p.city, p.state, p.postal_code])),
        }
        for p in db.query(Property).order_by(Property.created_at).all()
    ]
    units = [
        {"id": u.id, "label": u.label, "property_id": u.property_id}
        for u in db.query(Unit).order_by(Unit.created_at).all()
    ]

    def _user_name(user_id):
        if user_id is None:
            return None
        u = db.query(User).filter_by(id=user_id).first()
        if u is None:
            return None
        return " ".join(filter(None, [u.first_name, u.last_name])) or u.email

    tenants = []
    for t in db.query(Tenant).all():
        u = db.query(User).filter_by(id=t.user_id).first() if t.user_id else None
        tenants.append({
            "id": t.id,
            "name": _user_name(t.user_id),
            "phone": getattr(u, "phone", None),
            "email": getattr(u, "email", None),
        })

    vendors = [
        {"id": v.id, "name": v.name, "vendor_type": getattr(v, "vendor_type", None), "phone": getattr(v, "phone", None)}
        for v in db.query(User).filter(User.user_type == "vendor").all()
    ]

    leases = [
        {
            "id": l.id,
            "tenant_id": l.tenant_id,
            "unit_id": l.unit_id,
            "rent_amount": l.rent_amount,
            "payment_status": l.payment_status,
        }
        for l in db.query(Lease).all()
    ]

    tasks = []
    for t in db.query(Task).order_by(Task.created_at).all():
        tasks.append({
            "id": t.id,
            "title": t.title,
            "status": (t.task_status.value if hasattr(t.task_status, "value") else str(t.task_status)),
            "goal": t.goal,
            "steps": t.steps,
            "last_review_status": getattr(t, "last_review_status", None),
            "last_review_summary": getattr(t, "last_review_summary", None),
            "last_review_next_step": getattr(t, "last_review_next_step", None),
        })

    return {
        "suggestions": suggestions,
        "ask_manager_questions": ask_manager_questions,
        "outbound_messages": outbound_messages,
        "tasks": tasks,
        "entities": {
            "properties": properties,
            "units": units,
            "tenants": tenants,
            "vendors": vendors,
            "leases": leases,
        },
    }


def _dump_eval_runs(item, report) -> str | None:
    """Serialize every AgentRun + AgentTrace from the test's DB session
    into ``eval-runs/<test>-<ts>.json`` and return the path.

    Must run while the test's transactional session is still open (i.e.
    inside ``pytest_runtest_makereport`` for the ``call`` phase, before
    fixture teardown rolls the savepoint back).
    """
    db = (getattr(item, "funcargs", {}) or {}).get("db")
    if db is None:
        return None

    from agent.trajectory import to_trajectory
    from db.models import AgentRun, AgentStep, AgentTrace

    runs = db.query(AgentRun).order_by(AgentRun.started_at).all()
    if not runs:
        return None

    payload: list[dict] = []
    for run in runs:
        traces = (
            db.query(AgentTrace)
            .filter(AgentTrace.run_id == run.id)
            .order_by(AgentTrace.sequence_num)
            .all()
        )
        steps = (
            db.query(AgentStep)
            .filter(AgentStep.run_id == run.id)
            .order_by(AgentStep.step_id)
            .all()
        )
        payload.append(_serialize_agent_run(run, traces, steps, to_trajectory(db, run.id)))

    side_effects = _collect_side_effects(db)

    _EVAL_RUN_DUMP_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", item.name)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = _EVAL_RUN_DUMP_DIR / f"{safe_name}-{timestamp}.json"
    path.write_text(
        json.dumps(
            {
                "test": item.nodeid,
                "side_effects": side_effects,
                "runs": payload,
            },
            indent=2, default=str,
        ),
    )
    return str(path)


def _write_eval_trial_artifacts(item, report) -> str | None:
    if not os.getenv("RENTMATE_EVAL_WRITE_ARTIFACTS"):
        return None

    root = Path(os.getenv("RENTMATE_EVAL_ARTIFACT_ROOT", str(_EVAL_RUN_DUMP_DIR)))
    trial_index = int(os.getenv("RENTMATE_EVAL_TRIAL_INDEX", "1"))
    trials = int(os.getenv("RENTMATE_EVAL_TRIALS", "1"))
    case_id = item.nodeid
    case_dir = root / safe_id(case_id)
    trial_dir = case_dir / f"trial-{trial_index:03d}"
    trial_dir.mkdir(parents=True, exist_ok=True)

    run_dump_path = _dump_eval_runs(item, report)
    run_payload = {"test": case_id, "runs": []}
    if run_dump_path:
        run_payload = json.loads(Path(run_dump_path).read_text())
    write_json(trial_dir / "agent_runs.json", run_payload)
    _print_eval_agent_runs(case_id, trial_index, run_payload)

    trajectories = [
        {
            "run_id": run.get("run_id"),
            "trajectory": run.get("atif_trajectory"),
        }
        for run in run_payload.get("runs", [])
    ]
    write_json(trial_dir / "atif_trajectory.json", {"test": case_id, "trajectories": trajectories})

    failure = ""
    if report.failed:
        failure = getattr(report, "longreprtext", "") or str(report.longrepr)
    score_results = (getattr(item, "funcargs", {}) or {}).get("eval_scores") or []
    score_payload = [score.to_dict() for score in score_results]
    score = weighted_score(score_results) if score_results else (1.0 if report.passed else 0.0)
    row = {
        "case_id": case_id,
        "trial": trial_index,
        "trials": trials,
        "passed": bool(report.passed),
        "score": score,
        "scorers": score_payload,
        "duration_seconds": float(getattr(report, "duration", 0.0) or 0.0),
        "started_at": utc_now_iso(),
        "nodeid": item.nodeid,
        "failure": failure,
        "artifact_dir": str(trial_dir),
        "agent_runs_path": str(trial_dir / "agent_runs.json"),
        "atif_path": str(trial_dir / "atif_trajectory.json"),
    }
    write_json(trial_dir / "scores.json", row)
    write_json(trial_dir / "input.json", {"nodeid": item.nodeid, "keywords": sorted(str(k) for k in item.keywords)})
    write_json(trial_dir / "output.json", {"passed": report.passed, "failed": report.failed, "failure": failure})
    append_jsonl(root / "eval-results.jsonl", row)
    return str(trial_dir)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when != "call" or not item.get_closest_marker("eval"):
        return
    if report.skipped:
        return
    if report.failed:
        try:
            from agent.client import get_last_eval_debug_payload

            payload = get_last_eval_debug_payload()
            report.sections.append(("Hermes Eval Context", _format_eval_debug_payload(payload)))
        except Exception as exc:  # noqa: BLE001
            report.sections.append(("Hermes Eval Context", f"Failed to capture Hermes debug payload: {exc}"))

    try:
        artifact_dir = _write_eval_trial_artifacts(item, report)
        if artifact_dir:
            marker = f">>> Eval trial artifact: {artifact_dir}"
            print(f"\n{marker}")
            report.sections.append(("Eval Trial Artifact", marker))
        else:
            # Dump on every eval run (pass or fail) — reviewers want to
            # see what the agent did even on green so they can audit
            # message drafts, suggestions, and side effects.
            dump_path = _dump_eval_runs(item, report)
            if dump_path:
                marker = f">>> Eval run dump: {dump_path}"
                print(f"\n{marker}")
                report.sections.append(("Eval Run Dump", marker))
    except Exception as exc:  # noqa: BLE001
        report.sections.append(("Eval Run Dump", f"Failed to dump eval runs: {exc}"))


# ── DB fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def eval_scores():
    return []


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
    with patch(
        "services.notification_service.NotificationService._send_sms",
        new_callable=AsyncMock,
    ) as mock:
        yield mock


@pytest.fixture
def autonomous_mode():
    """Eval policy mix: act aggressively but keep outbound messages as
    pending suggestions so tests can inspect the drafted message instead
    of racing against auto-execution.
    """
    eval_policy = {
        "entity_changes": ActionPolicyLevel.AGGRESSIVE,
        "outbound_messages": ActionPolicyLevel.STRICT,
        "task_suggestion_creation": ActionPolicyLevel.STRICT,
    }
    with patch(
        "services.settings_service.get_action_policy_settings",
        return_value=eval_policy,
    ), patch(
        "agent.action_policy.get_action_policy_settings",
        return_value=eval_policy,
    ):
        yield


# ── Scenario builder ─────────────────────────────────────────────────────────


# Pools used to randomize names / addresses / phones when callers don't pass
# explicit values. Kept small and deliberate — large enough that adjacent
# tests get distinct values, small enough that drifting eval failures stay
# easy to recognize.
_TENANT_FIRST_NAMES = (
    "Alice", "Bryn", "Carol", "Devon", "Elena", "Felix", "Gita",
    "Harvey", "Imani", "Jules", "Kara", "Lior", "Mira", "Nadia",
)
_TENANT_LAST_NAMES = (
    "Reyes", "Patel", "Nguyen", "Carter", "Okafor", "Lindgren",
    "Ramos", "Hayashi", "Brennan", "Schultz", "Yamada", "Volkov",
)
_VENDOR_FIRST_NAMES = (
    "Rob", "Sam", "Pat", "Chris", "Jamie", "Morgan", "Taylor",
    "Riley", "Jordan", "Avery",
)
_STREET_NAMES = (
    "Oak Ave", "Pine St", "Maple Lane", "Cedar Blvd", "Birch Way",
    "Willow Pl", "Aspen Dr", "Elm Ct", "Hawthorn Rd", "Sycamore St",
)
_PROPERTY_NAMES = (
    "Cedar Court", "Maple Heights", "Riverstone", "Brookline",
    "Northwoods", "The Bluffs", "Harbor View", "Lakeside", "Pinegrove",
)


def _seeded_rng_for(node_id: str) -> random.Random:
    """Deterministic RNG seeded from the pytest test nodeid.

    Same test → same scenario every run. Different tests → different
    scenarios so memorization doesn't paper over real failures.
    """
    import hashlib

    digest = hashlib.sha256((node_id or "fallback").encode()).digest()
    seed = int.from_bytes(digest[:8], "big")
    return random.Random(seed)


class ScenarioBuilder:
    """Builds a test scenario with property, unit, tenant, lease, vendor, and task.

    Defaults that aren't passed explicitly are filled in from a seeded
    RNG so two evals don't share the same "Alice Renter / 206-555-0100"
    fingerprint. Tests should read names / phones / emails off the
    returned entity objects (not module constants) when asserting.
    """

    def __init__(self, db, *, rng: random.Random | None = None):
        self.db = db
        self.entities = {}
        self.rng = rng or random.Random()

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

    # ── randomized defaults ──────────────────────────────────────────────

    def _random_phone(self) -> str:
        return f"206-555-{self.rng.randint(1000, 9999):04d}"

    def _random_address(self) -> str:
        return f"{self.rng.randint(100, 9999)} {self.rng.choice(_STREET_NAMES)}"

    def _random_tenant_name(self) -> tuple[str, str]:
        return self.rng.choice(_TENANT_FIRST_NAMES), self.rng.choice(_TENANT_LAST_NAMES)

    def _random_property_name(self) -> str:
        return self.rng.choice(_PROPERTY_NAMES)

    def _random_vendor_name(self, vendor_type: str) -> str:
        first = self.rng.choice(_VENDOR_FIRST_NAMES)
        return f"{first} the {vendor_type}"

    # ── add_* methods ────────────────────────────────────────────────────

    def add_property(self, *, name=None, address=None,
                     city="Seattle", state="WA", postal_code="98101"):
        if address is None:
            address = self._random_address()
        if name is None:
            name = self._random_property_name()
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

    def add_unit(self, *, label=None, prop=None):
        if label is None:
            label = self.rng.choice(("A", "B", "1A", "2B", "3C", "Main"))
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

    def add_tenant(self, *, first_name=None, last_name=None,
                   phone=None, email=None):
        if first_name is None or last_name is None:
            rand_first, rand_last = self._random_tenant_name()
            first_name = first_name or rand_first
            last_name = last_name or rand_last
        if phone is None:
            phone = self._random_phone()
        if email is None:
            email = f"{first_name.lower()}.{last_name.lower()}@example.com"
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

    def add_vendor(self, *, name=None, phone=None,
                   vendor_type="Handyman", email=None):
        from gql.types import CreateVendorInput
        from services.vendor_service import VendorService
        if name is None:
            name = self._random_vendor_name(vendor_type)
        if phone is None:
            phone = self._random_phone()
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

    def add_task(self, *, title, context_body, goal, steps,
                 category="maintenance", urgency="medium",
                 task_mode="autonomous", task_status="active"):
        """Create a Task. ``title``, ``context_body``, ``goal``, and
        ``steps`` are required — the production review path needs them
        all to behave realistically.

        ``steps`` accepts either pre-dumped dict rows or a list of
        ``TaskProgressStep`` objects (which we dump here so callers can
        write the natural shape).
        """
        from services.task_service import TaskProgressStep, dump_task_steps

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

        self.db.add(Message(
            org_id=1,
            conversation_id=ai_conv.id,
            sender_type=ParticipantType.ACCOUNT_USER,
            body=context_body, message_type=MessageType.CONTEXT,
            sender_name="System", is_ai=False, sent_at=datetime.now(UTC),
        ))

        steps_value = (
            dump_task_steps(steps) if steps and isinstance(steps[0], TaskProgressStep)
            else steps
        )

        task = Task(
            id=NumberAllocator.allocate_next(self.db, entity_type="task", org_id=1),
            org_id=1,
            creator_id=DEFAULT_ACCOUNT_ID,
            title=title,
            goal=goal,
            steps=steps_value,
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
def scenario_builder(db, request):
    """Per-test ScenarioBuilder with an RNG seeded from the test nodeid."""
    return ScenarioBuilder(db, rng=_seeded_rng_for(request.node.nodeid))


# ── Agent runner ─────────────────────────────────────────────────────────────


def run_review(db, task) -> None:
    """Trigger a production task review against the test session.

    Patches every SessionLocal binding the review path touches so the
    agent's tools see our test database. This is the canonical way to
    drive the agent in evals — it exercises the same prompt + dispatch
    path that runs in production via the periodic review loop.
    """
    from unittest.mock import MagicMock

    from handlers.task_review import _review_one_task

    db.flush()
    db.expunge(task)

    mock_sl = MagicMock()
    mock_sl.session_factory.return_value = db
    mock_sl.return_value = db

    loop = asyncio.new_event_loop()
    try:
        with patch("db.session.SessionLocal", mock_sl), \
             patch("handlers.deps.SessionLocal", mock_sl), \
             patch("services.settings_service.SessionLocal", mock_sl):
            loop.run_until_complete(_review_one_task(task))
    finally:
        loop.close()
        db.expire_all()


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
    from evals.llm_utils import completion_json

    criteria_block = "\n".join(f"{i+1}. {c}" for i, c in enumerate(criteria))
    result, _, _ = completion_json(
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
        model=os.getenv("EVAL_JUDGE_MODEL") or os.getenv("LLM_MODEL", "deepseek/deepseek-chat"),
        api_base=os.getenv("EVAL_JUDGE_BASE_URL") or os.getenv("LLM_BASE_URL") or None,
        temperature=0.0,
    )

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
        prefer_vendor = True
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
