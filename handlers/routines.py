"""Routine executor — runs saved agent prompts on configurable schedules."""
import asyncio
import json
import logging
import re
from datetime import UTC, datetime, timedelta

from croniter import croniter
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from agent.tracing import log_trace, make_trace_envelope
from db.enums import RoutineState
from handlers.deps import require_user

router = APIRouter()

logger = logging.getLogger("rentmate.routines")

_POLL_SECONDS = 60


def _routine_trace_detail(
    *,
    flow: str,
    session_key: str,
    task_id: str,
    prompt: str,
    context: str,
    reply: str | None = None,
) -> dict:
    messages_payload = [
        {"role": "system", "content": context},
        {"role": "user", "content": prompt},
    ]
    return make_trace_envelope(
        "llm_exchange" if reply is not None else "llm_request",
        flow=flow,
        session_key=session_key,
        task_id=task_id,
        conversation_id=None,
        messages_payload=messages_payload,
        messages_breakdown={
            "system": messages_payload[0],
            "history": [],
            "latest_user": messages_payload[1],
        },
        context={"text": context, "sections": []},
        retrieval=None,
        reply=reply,
        side_effects=[],
        reasoning={
            "available": False,
            "note": "No provider reasoning trace available for this response.",
        },
    )


# ── Schedule parsing ─────────────────────────────────────────────────────────


def parse_schedule(expr: str) -> str:
    """Normalize a schedule expression to a cron string.

    Accepts:
    - Cron expressions: "0 9 * * 1" (already valid)
    - Intervals: "every 4h", "every 30m", "every 1d"
    - One-shot delays: "30m", "2h", "1d" (converted to a one-time run)

    Returns a 5-field cron expression, or the original for one-shot.
    """
    expr = expr.strip()

    # Already a valid cron expression (5 fields)
    parts = expr.split()
    if len(parts) == 5 and all(_is_cron_field(p) for p in parts):
        return expr

    # Interval: "every Xh", "every Xm", "every Xd"
    m = re.match(r"every\s+(\d+)\s*(m|min|minutes?|h|hours?|d|days?)", expr, re.IGNORECASE)
    if m:
        val, unit = int(m.group(1)), m.group(2)[0].lower()
        if unit == "m":
            return f"*/{val} * * * *"
        elif unit == "h":
            return f"0 */{val} * * *"
        elif unit == "d":
            return f"0 9 */{val} * *"  # default to 9am

    # Common named schedules
    named = {
        "hourly": "0 * * * *",
        "daily": "0 9 * * *",
        "weekly": "0 9 * * 1",
        "monthly": "0 9 1 * *",
    }
    if expr.lower() in named:
        return named[expr.lower()]

    return expr  # Return as-is, let croniter validate


def next_run(cron_expr: str, *, after: datetime | None = None) -> datetime:
    """Calculate the next run time for a cron expression."""
    base = after or datetime.now(UTC)
    # croniter needs naive datetime
    naive = base.replace(tzinfo=None)
    try:
        it = croniter(cron_expr, naive)
        nxt = it.get_next(datetime)
        return nxt.replace(tzinfo=UTC)
    except (ValueError, KeyError):
        # Invalid cron — default to 1 hour from now
        return base + timedelta(hours=1)


def human_schedule(cron_expr: str) -> str:
    """Convert a cron expression to a human-readable string."""
    parts = cron_expr.split()
    if len(parts) != 5:
        return cron_expr

    minute, hour, dom, month, dow = parts

    # Common patterns
    if cron_expr == "0 * * * *":
        return "Every hour"
    if cron_expr == "0 9 * * *":
        return "Daily at 9am"
    if cron_expr == "0 9 * * 1":
        return "Every Monday at 9am"
    if cron_expr == "0 9 1 * *":
        return "Monthly on the 1st at 9am"

    # Interval patterns
    if minute.startswith("*/"):
        return f"Every {minute[2:]} minutes"
    if hour.startswith("*/"):
        return f"Every {hour[2:]} hours"

    # Day-of-week
    dow_names = {0: "Sun", 1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat"}
    if dow != "*" and dow.isdigit():
        day = dow_names.get(int(dow), dow)
        return f"Every {day} at {hour}:{minute.zfill(2)}"

    return cron_expr


def _is_cron_field(s: str) -> bool:
    """Quick check if a string looks like a cron field."""
    return bool(re.match(r'^[\d\*\/\-\,]+$', s))


# ── Scheduler loop ───────────────────────────────────────────────────────────


def seed_default_routines():
    """Ensure the canonical default routines exist and match the code definitions.

    Idempotent: creates missing defaults, and updates the prompt / schedule of
    any existing default routine whose code definition has drifted since it
    was last seeded. User-mutable state (enabled, state, next_run_at,
    last_run_*, completed_count, simulated_at) is preserved, except that
    ``next_run_at`` is recomputed when the schedule changes so the scheduler
    picks up the new cadence.
    """
    from db.models import Routine
    from db.session import SessionLocal

    db = SessionLocal()
    try:
        from db.models import User
        account = db.query(User).first()
        if not account:
            logger.warning("No account found — cannot seed routines")
            return
        creator_id = account.id

        defaults = [
            {
                "name": "Weekly lease expiry check",
                "prompt": (
                    "Review all leases expiring within 60 days. For each expiring lease, "
                    "create a suggestion to discuss renewal with the tenant. Include the "
                    "property address, unit, tenant name, and expiry date."
                ),
                "schedule": "0 9 * * 1",
                "schedule_display": "Every Monday at 9am",
            },
            {
                "name": "Monthly data quality audit",
                "prompt": (
                    "Check all properties for missing addresses or incomplete info. "
                    "Check all tenants for missing phone numbers or email addresses. "
                    "Check for expired leases that haven't been renewed. "
                    "Create suggestions for any issues found."
                ),
                "schedule": "0 8 1 * *",
                "schedule_display": "Monthly on the 1st at 8am",
            },
            {
                "name": "Rent collection follow-up",
                "prompt": (
                    "Check all tenants with payment status 'late' or 'overdue'. "
                    "For each, create a suggestion to send a friendly rent reminder. "
                    "Include the amount due, how many days late, and the tenant's name."
                ),
                "schedule": "0 10 5 * *",
                "schedule_display": "Monthly on the 5th at 10am",
            },
            {
                "name": "Seasonal maintenance review",
                "prompt": (
                    "Review all properties and identify seasonal maintenance needs "
                    "based on the current month: gutter cleaning (fall), HVAC servicing "
                    "(spring/fall), landscaping (spring), winterization (late fall). "
                    "Create suggestions for each property that needs attention, "
                    "with appropriate vendor types."
                ),
                "schedule": "0 9 1 3,6,9,12 *",
                "schedule_display": "Quarterly on the 1st at 9am",
            },
            {
                "name": "Vacancy check",
                "prompt": (
                    "Check all units that are currently vacant (no active lease). "
                    "For units vacant more than 14 days, create a suggestion to "
                    "list the unit or follow up on pending applications. Include "
                    "the property address, unit label, and days vacant."
                ),
                "schedule": "0 9 * * 3",
                "schedule_display": "Every Wednesday at 9am",
            },
            {
                "name": "Tenant satisfaction check-in",
                "prompt": (
                    "Review tenants who have been in place for 6+ months and haven't "
                    "had any recent communication. For each tenant, call "
                    "`message_person` to send a brief check-in asking if everything "
                    "is going well with their unit. One call per tenant — do not "
                    "batch multiple tenants into a single suggestion."
                ),
                "schedule": "0 10 15 * *",
                "schedule_display": "Monthly on the 15th at 10am",
            },
        ]

        from services.number_allocator import NumberAllocator
        now = datetime.now(UTC)
        org_id = account.org_id
        created = 0
        updated = 0
        for d in defaults:
            existing = (
                db.query(Routine)
                .filter(
                    Routine.org_id == org_id,
                    Routine.is_default.is_(True),
                    Routine.name == d["name"],
                )
                .first()
            )
            if existing is None:
                db.add(Routine(
                    id=NumberAllocator.allocate_next(db, entity_type="routine", org_id=org_id),
                    org_id=org_id,
                    creator_id=creator_id,
                    name=d["name"],
                    prompt=d["prompt"],
                    schedule=d["schedule"],
                    schedule_display=d["schedule_display"],
                    is_default=True,
                    enabled=False,
                    state=RoutineState.PAUSED,
                    next_run_at=next_run(d["schedule"]),
                    created_at=now,
                    updated_at=now,
                ))
                created += 1
                continue

            # Sync the code-owned fields (prompt, schedule, schedule_display)
            # while preserving user-mutable state (enabled, state, counters).
            changed = False
            if existing.prompt != d["prompt"]:
                existing.prompt = d["prompt"]
                changed = True
            if existing.schedule_display != d["schedule_display"]:
                existing.schedule_display = d["schedule_display"]
                changed = True
            if existing.schedule != d["schedule"]:
                existing.schedule = d["schedule"]
                # Schedule cadence changed — recompute the next run so the
                # scheduler respects the new cadence.
                existing.next_run_at = next_run(d["schedule"])
                changed = True
            if changed:
                existing.updated_at = now
                updated += 1
        db.commit()
        logger.info(
            "Seeded default routines: %d created, %d updated, %d total",
            created, updated, len(defaults),
        )
    finally:
        db.close()


async def routine_loop():
    """Background loop that executes due routines."""
    logger.info("Routine runner started (interval=%ds)", _POLL_SECONDS)

    # Wait for server to fully start
    await asyncio.sleep(10)

    while True:
        try:
            await _tick()
        except Exception:
            logger.exception("Routine tick failed")
        await asyncio.sleep(_POLL_SECONDS)


async def _tick():
    """Execute all due routines."""
    from db.models import Routine
    from db.session import SessionLocal

    now = datetime.now(UTC)
    db = SessionLocal()
    try:
        due = (
            db.query(Routine)
            .filter(
                Routine.enabled.is_(True),
                Routine.state == RoutineState.SCHEDULED,
                Routine.next_run_at <= now,
            )
            .all()
        )
        if not due:
            return

        logger.info("Routine runner: %d routine(s) due", len(due))

        for task in due:
            try:
                output = await execute_routine(task)
                task.last_status = "ok"
                task.last_output = output[:5000] if output else ""
            except Exception as exc:
                logger.exception("Routine %s failed: %s", task.name, exc)
                task.last_status = "error"
                task.last_output = str(exc)[:2000]

            task.last_run_at = now
            task.completed_count += 1
            task.updated_at = now

            # Check repeat limit
            if task.repeat is not None and task.completed_count >= task.repeat:
                task.state = RoutineState.COMPLETED
                task.enabled = False
                logger.info("Routine '%s' completed (%d runs)", task.name, task.completed_count)
            else:
                # Advance to next run
                task.next_run_at = next_run(task.schedule, after=now)

        db.commit()
    finally:
        db.close()


async def execute_routine(
    routine,
    *,
    on_progress=None,
    session_prefix: str = "routine",
    prompt_override: str | None = None,
) -> str:
    """Run the agent for a routine.

    `prompt_override` lets callers (e.g. simulation) supply a modified prompt
    without mutating the stored row.
    """
    from agent.client import call_agent
    from agent.context import load_account_context
    from agent.registry import agent_registry
    from integrations.local_auth import set_request_context

    task_id = routine.id
    creator_id = routine.creator_id
    org_id = getattr(routine, "org_id", None)
    prompt = prompt_override if prompt_override is not None else routine.prompt

    # Set request context for the routine's creator
    tokens = set_request_context(account_id=creator_id, org_id=org_id)

    try:
        from db.session import SessionLocal
        db = SessionLocal()
        try:
            agent_id = agent_registry.ensure_agent(creator_id, db)
            context = load_account_context(db, query=prompt)
        finally:
            db.close()

        messages = [
            {"role": "system", "content": context},
            {"role": "user", "content": prompt},
        ]

        session_key = f"{session_prefix}:{task_id}"
        trace_source = "simulate" if session_prefix == "simulate" else "routine"
        trace_detail = _routine_trace_detail(
            flow=trace_source,
            session_key=session_key,
            task_id=str(task_id),
            prompt=prompt,
            context=context,
        )
        from agent.runs import derive_run_metadata, start_run
        with start_run(
            **derive_run_metadata(
                session_key=session_key,
                task_id=str(task_id),
                source_override=trace_source,
            ),
            trigger_input=prompt,
        ):
            log_trace(
                "llm_request",
                trace_source,
                f"Prepared {len(messages)} messages for model call",
                detail=trace_detail,
            )
            resp = await call_agent(
                agent_id,
                session_key=session_key,
                messages=messages,
                on_progress=on_progress,
                trace_context=trace_detail,
            )
            log_trace(
                "llm_reply",
                trace_source,
                (resp.reply or "")[:200],
                detail=_routine_trace_detail(
                    flow=trace_source,
                    session_key=session_key,
                    task_id=str(task_id),
                    prompt=prompt,
                    context=context,
                    reply=resp.reply,
                ),
            )
            return resp.reply
    finally:
        from integrations.local_auth import reset_request_context
        reset_request_context(tokens)


def _routine_not_found_stream() -> StreamingResponse:
    return StreamingResponse(
        iter([f"data: {json.dumps({'type': 'error', 'message': 'Routine not found'})}\n\n"]),
        media_type="text/event-stream",
    )


def _load_routine_snapshot(routine_id: int) -> dict | None:
    from db.session import SessionLocal
    from services.routine_service import RoutineService

    db = SessionLocal()
    try:
        routine = RoutineService.get_by_id(db, routine_id)
        if not routine:
            return None
        return {
            "id": routine.id,
            "creator_id": routine.creator_id,
            "org_id": routine.org_id,
            "prompt": routine.prompt,
        }
    finally:
        db.close()


def _stream_routine_response(snapshot: dict, *, simulate: bool) -> StreamingResponse:
    routine_id = snapshot["id"]
    creator_id = snapshot["creator_id"]
    org_id = snapshot["org_id"]
    prompt = snapshot["prompt"]

    async def generate():
        from agent.tools import simulation_suggestions
        from db.models import Routine
        from db.session import SessionLocal
        from integrations.local_auth import reset_request_context, set_request_context

        tokens = set_request_context(account_id=creator_id, org_id=org_id)
        sim_token = simulation_suggestions.set([]) if simulate else None
        try:
            queue: asyncio.Queue = asyncio.Queue()

            async def _progress_cb(text: str, **_kwargs):
                await queue.put(text)

            async def _run():
                try:
                    from services.routine_service import RoutineService
                    fetch_db = SessionLocal()
                    try:
                        hb = RoutineService.get_by_id(fetch_db, routine_id)
                        if not hb:
                            raise ValueError(f"Routine not found: {routine_id}")
                        fetch_db.expunge(hb)
                    finally:
                        fetch_db.close()
                    reply = await execute_routine(
                        hb,
                        on_progress=_progress_cb,
                        session_prefix="simulate" if simulate else "routine",
                        prompt_override=prompt if simulate else None,
                    )
                    await queue.put(None)
                    return reply
                except Exception as exc:
                    await queue.put(None)
                    raise exc

            agent_task = asyncio.create_task(_run())

            while True:
                text = await queue.get()
                if text is None:
                    break
                yield f"data: {json.dumps({'type': 'progress', 'text': text})}\n\n"

            try:
                reply = agent_task.result()
                update_db = SessionLocal()
                try:
                    row = update_db.query(Routine).filter_by(id=routine_id, org_id=org_id).first()
                    now = datetime.now(UTC)
                    payload = {"type": "done", "reply": reply}
                    if simulate:
                        if row:
                            row.simulated_at = now
                            update_db.commit()
                        actions = _collect_simulation_suggestions(
                            simulation_suggestions.get() or [],
                            [],
                        )
                        if not actions:
                            actions = _parse_suggestions_from_reply(reply)
                        payload["reply"] = _format_simulation_reply(reply, actions)
                        payload["actions"] = _shape_simulation_actions_for_ui(actions)
                        # Legacy key retained so pre-update UI bundles keep
                        # rendering something reasonable until they're redeployed.
                        payload["suggestions"] = payload["actions"]
                    elif row:
                        row.last_status = "ok"
                        row.last_output = reply[:5000] if reply else ""
                        row.last_run_at = now
                        row.completed_count += 1
                        row.updated_at = now
                        if row.repeat is not None and row.completed_count >= row.repeat:
                            row.state = RoutineState.COMPLETED
                            row.enabled = False
                        else:
                            row.next_run_at = next_run(row.schedule, after=now)
                        update_db.commit()
                        payload["task"] = {
                            "uid": row.id,
                            "lastStatus": row.last_status,
                            "lastOutput": row.last_output,
                            "lastRunAt": row.last_run_at.isoformat() if row.last_run_at else None,
                            "completedCount": row.completed_count,
                            "nextRunAt": row.next_run_at.isoformat() if row.next_run_at else None,
                            "state": row.state,
                            "enabled": row.enabled,
                        }
                    yield f"data: {json.dumps(payload)}\n\n"
                finally:
                    update_db.close()
            except Exception as exc:
                update_db = SessionLocal()
                try:
                    row = update_db.query(Routine).filter_by(id=routine_id, org_id=org_id).first()
                    now = datetime.now(UTC)
                    if row and not simulate:
                        row.last_status = "error"
                        row.last_output = str(exc)[:2000]
                        row.last_run_at = now
                        row.updated_at = now
                        update_db.commit()
                finally:
                    update_db.close()
                yield f"data: {json.dumps({'type': 'error', 'message': str(exc)[:500]})}\n\n"
        finally:
            if sim_token is not None:
                simulation_suggestions.reset(sim_token)
            reset_request_context(tokens)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/routines/{routine_id}/run")
async def run_routine_sse(routine_id: int, request: Request):
    """Stream a real routine run with progress events (SSE)."""
    await require_user(request)
    snapshot = _load_routine_snapshot(routine_id)
    if not snapshot:
        return _routine_not_found_stream()
    return _stream_routine_response(snapshot, simulate=False)


# ── SSE simulate endpoint ────────────────────────────────────────────────────


@router.post("/routines/{routine_id}/simulate")
async def simulate_routine_sse(routine_id: int, request: Request):
    """Stream a routine simulation with progress events (SSE)."""
    await require_user(request)
    snapshot = _load_routine_snapshot(routine_id)
    if not snapshot:
        return _routine_not_found_stream()
    snapshot["prompt"] = (
        "[SIMULATION — the simulation runtime intercepts every write tool call "
        "(message_person, propose_task, create_suggestion, create_property, "
        "create_tenant, create_vendor, create_document, save_memory, edit_memory, "
        "update_onboarding, close_task, …) and records the inputs without "
        "actually sending messages, writing to the database, or triggering any "
        "external effect. Read tools run normally so you can inspect real data. "
        "Call the exact tools you would call in a real run — if the routine asks "
        "you to message a tenant, call `message_person`; do NOT substitute "
        "`create_suggestion` just because this is a simulation. Do not answer "
        "with a generic plan or high-level summary — the simulation result IS "
        "the set of tool calls you made.]\n\n" + snapshot["prompt"]
    )
    return _stream_routine_response(snapshot, simulate=True)


def _normalize_side_effect_suggestions(side_effects: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    for index, effect in enumerate(side_effects, start=1):
        effect_type = effect.get("type", "")
        if effect_type.endswith("_suggestion") or effect_type == "create_suggestion":
            normalized.append({
                "id": f"side-{index}",
                "title": effect.get("title", "Untitled suggestion"),
                "body": effect.get("ai_context") or effect.get("body") or "",
                "category": effect.get("category") or effect.get("suggestion_type"),
                "urgency": effect.get("urgency"),
                "property_id": effect.get("property_id"),
                "task_id": effect.get("task_id"),
                "action_payload": effect.get("action_payload"),
                "risk_score": effect.get("risk_score"),
                "suggestion_type": effect.get("suggestion_type"),
            })
    return normalized


def _collect_simulation_suggestions(simulated: list[dict], side_effects: list[dict]) -> list[dict]:
    suggestions = simulated or _normalize_side_effect_suggestions(side_effects)
    return suggestions


def _parse_suggestions_from_reply(agent_reply: str) -> list[dict]:
    suggestions: list[dict] = []
    pattern = re.compile(
        r"^\s*(\d+)\.\s+\*\*(?P<title>[^*]+)\*\*\s*(?:[-:]\s*(?P<body>.+))?$",
        re.MULTILINE,
    )
    for index, match in enumerate(pattern.finditer(agent_reply or ""), start=1):
        suggestions.append({
            "id": f"reply-{index}",
            "title": match.group("title").strip(),
            "body": (match.group("body") or "").strip(),
        })
    return suggestions


_SIMULATION_SECTION_ORDER = [
    "message_person",
    "propose_task",
    "close_task",
    "create_property",
    "create_tenant",
    "create_vendor",
    "create_document",
    "create_scheduled_task",
    "save_memory",
    "edit_memory",
    "update_onboarding",
    "create_suggestion",
]


def _render_message_person_section(items: list[dict]) -> str:
    count = len(items)
    lines = [f"I would message {count} {'person' if count == 1 else 'people'} directly:"]
    for index, item in enumerate(items, start=1):
        args = item.get("args") or {}
        name = (args.get("entity_name") or args.get("entity_id") or "").strip()
        entity_type = (args.get("entity_type") or "").strip()
        if not name:
            name = f"{entity_type.title() or 'Recipient'}"
        draft = (args.get("draft_message") or "").strip()
        draft_preview = draft if draft else "(no draft provided)"
        lines.append(f'{index}. {name} — "{draft_preview}"')
    return "\n".join(lines)


def _render_create_suggestion_section(items: list[dict]) -> str:
    count = len(items)
    lines = [f"I would create {count} suggestion{'s' if count != 1 else ''}:"]
    for index, item in enumerate(items, start=1):
        args = item.get("args") or {}
        title = (args.get("title") or "Untitled suggestion").strip()
        meta: list[str] = []
        if args.get("category"):
            meta.append(f"category={args['category']}")
        if args.get("urgency"):
            meta.append(f"urgency={args['urgency']}")
        if args.get("property_id"):
            meta.append(f"property={args['property_id']}")
        if args.get("task_id"):
            meta.append(f"task={args['task_id']}")
        if args.get("risk_score") is not None:
            meta.append(f"risk={args['risk_score']}")
        header = f"{index}. {title}" + (f" ({', '.join(meta)})" if meta else "")
        lines.append(header)
        body = (args.get("body") or args.get("ai_context") or "").strip()
        if body:
            lines.append(body)
        action_payload = args.get("action_payload") or {}
        if action_payload:
            lines.append("Action payload:")
            lines.append(json.dumps(action_payload, indent=2, sort_keys=True))
    return "\n".join(lines)


def _render_default_section(tool_name: str, items: list[dict]) -> str:
    count = len(items)
    lines = [f"I would call `{tool_name}` {count} time{'s' if count != 1 else ''}:"]
    for index, item in enumerate(items, start=1):
        args = item.get("args") or {}
        summary = (
            args.get("title")
            or args.get("name")
            or args.get("address")
            or args.get("filename")
            or (args.get("content") or "")[:80]
            or "(no summary)"
        )
        lines.append(f"{index}. {summary}")
    return "\n".join(lines)


_SIMULATION_RENDERERS = {
    "message_person": _render_message_person_section,
    "create_suggestion": _render_create_suggestion_section,
}


def _coerce_simulation_action(entry: dict) -> dict:
    """Normalise a recorded action to the ``{id, tool, args}`` shape.

    Older code paths and the reply-parsing fallback return flat suggestion
    dicts; normalise them into a pseudo-``create_suggestion`` action so the
    formatter can treat every entry the same way.
    """
    if "tool" in entry and "args" in entry:
        return entry
    args = {
        "title": entry.get("title"),
        "body": entry.get("body"),
        "ai_context": entry.get("ai_context"),
        "category": entry.get("category"),
        "urgency": entry.get("urgency"),
        "property_id": entry.get("property_id"),
        "task_id": entry.get("task_id"),
        "risk_score": entry.get("risk_score"),
        "action_payload": entry.get("action_payload"),
        "suggestion_type": entry.get("suggestion_type"),
    }
    return {
        "id": entry.get("id") or "sim-create_suggestion-?",
        "tool": "create_suggestion",
        "args": {k: v for k, v in args.items() if v is not None},
    }


def _shape_simulation_actions_for_ui(actions: list[dict]) -> list[dict]:
    """Flatten recorded actions into the card shape the routine detail UI renders.

    The UI expects ``{id, title, body, category, urgency, property_id, task_id,
    risk_score, action_payload, tool}`` per card. We preserve ``tool`` so the
    frontend can label cards by what would have happened (message vs suggestion
    vs property create, etc.) without re-parsing ``action_payload``.
    """
    shaped: list[dict] = []
    for raw in actions:
        action = _coerce_simulation_action(raw)
        tool = action.get("tool") or "create_suggestion"
        args = action.get("args") or {}
        entry_id = action.get("id") or f"sim-{tool}-{len(shaped) + 1}"

        if tool == "message_person":
            entity_name = (
                args.get("entity_name")
                or args.get("entity_id")
                or args.get("entity_type")
                or "recipient"
            )
            entity_type = args.get("entity_type") or "person"
            shaped.append({
                "id": entry_id,
                "tool": tool,
                "title": f"Message {entity_type}: {entity_name}",
                "body": args.get("draft_message") or "",
                "task_id": args.get("task_id"),
                "action_payload": {k: v for k, v in args.items() if k != "draft_message"},
            })
            continue

        if tool == "create_suggestion":
            shaped.append({
                "id": entry_id,
                "tool": tool,
                "title": args.get("title") or "Untitled suggestion",
                "body": args.get("body") or args.get("ai_context") or "",
                "category": args.get("category"),
                "urgency": args.get("urgency"),
                "property_id": args.get("property_id"),
                "task_id": args.get("task_id"),
                "risk_score": args.get("risk_score"),
                "action_payload": args.get("action_payload"),
            })
            continue

        summary = (
            args.get("title")
            or args.get("name")
            or args.get("address")
            or args.get("filename")
            or (args.get("content") or args.get("draft_message") or "")[:120]
            or ""
        )
        shaped.append({
            "id": entry_id,
            "tool": tool,
            "title": f"Would call `{tool}`",
            "body": summary,
            "action_payload": args,
        })
    return shaped


def _format_simulation_reply(agent_reply: str, actions: list[dict]) -> str:
    if not actions:
        return agent_reply or "(no output)"

    by_tool: dict[str, list[dict]] = {}
    for raw in actions:
        action = _coerce_simulation_action(raw)
        tool_name = action.get("tool") or "create_suggestion"
        by_tool.setdefault(tool_name, []).append(action)

    sections: list[str] = []
    for tool_name in _SIMULATION_SECTION_ORDER:
        items = by_tool.pop(tool_name, None)
        if not items:
            continue
        renderer = _SIMULATION_RENDERERS.get(tool_name)
        sections.append(
            renderer(items) if renderer else _render_default_section(tool_name, items)
        )

    # Tools without a tailored renderer and not in the canonical order fall
    # through to the generic default renderer in whatever order we saw them.
    for tool_name, items in by_tool.items():
        sections.append(_render_default_section(tool_name, items))

    return "\n\n".join(sections).strip()
