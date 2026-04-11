"""Scheduled task executor — runs AI agent on cron schedules.

Replaces the old Property-Flow DSL automation system with natural language
prompts executed on configurable schedules.
"""
import asyncio
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from croniter import croniter
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from handlers.deps import require_user

router = APIRouter()

logger = logging.getLogger("rentmate.scheduler")

_POLL_SECONDS = 60


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


def seed_default_tasks():
    """Create default scheduled tasks if none exist."""
    from db.models import ScheduledTask
    from db.session import SessionLocal

    db = SessionLocal()
    try:
        if db.query(ScheduledTask).count() > 0:
            return  # Already seeded

        from db.models import User
        account = db.query(User).first()
        if not account:
            logger.warning("No account found — cannot seed scheduled tasks")
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
                    "had any recent communication. Create suggestions to send a brief "
                    "check-in message asking if everything is going well with their unit."
                ),
                "schedule": "0 10 15 * *",
                "schedule_display": "Monthly on the 15th at 10am",
            },
        ]

        import uuid
        now = datetime.now(UTC)
        for d in defaults:
            nxt = next_run(d["schedule"])
            db.add(ScheduledTask(
                id=str(uuid.uuid4()),
                creator_id=creator_id,
                name=d["name"],
                prompt=d["prompt"],
                schedule=d["schedule"],
                schedule_display=d["schedule_display"],
                is_default=True,
                enabled=False,
                state="paused",
                next_run_at=nxt,
                created_at=now,
                updated_at=now,
            ))
        db.commit()
        logger.info("Seeded %d default scheduled tasks", len(defaults))
    finally:
        db.close()


async def scheduler_loop():
    """Background loop that executes due scheduled tasks."""
    logger.info("Scheduler started (interval=%ds)", _POLL_SECONDS)

    # Wait for server to fully start
    await asyncio.sleep(10)

    while True:
        try:
            await _tick()
        except Exception:
            logger.exception("Scheduler tick failed")
        await asyncio.sleep(_POLL_SECONDS)


async def _tick():
    """Execute all due scheduled tasks."""
    from db.models import ScheduledTask
    from db.session import SessionLocal

    now = datetime.now(UTC)
    db = SessionLocal()
    try:
        due = (
            db.query(ScheduledTask)
            .filter(
                ScheduledTask.enabled.is_(True),
                ScheduledTask.state == "scheduled",
                ScheduledTask.next_run_at <= now,
            )
            .all()
        )
        if not due:
            return

        logger.info("Scheduler: %d task(s) due", len(due))

        for task in due:
            try:
                output = await _execute_task(task)
                task.last_status = "ok"
                task.last_output = output[:5000] if output else ""
            except Exception as exc:
                logger.exception("Scheduled task %s failed: %s", task.name, exc)
                task.last_status = "error"
                task.last_output = str(exc)[:2000]

            task.last_run_at = now
            task.completed_count += 1
            task.updated_at = now

            # Check repeat limit
            if task.repeat is not None and task.completed_count >= task.repeat:
                task.state = "completed"
                task.enabled = False
                logger.info("Scheduled task '%s' completed (%d runs)", task.name, task.completed_count)
            else:
                # Advance to next run
                task.next_run_at = next_run(task.schedule, after=now)

        db.commit()
    finally:
        db.close()


async def _execute_task(task, *, on_progress=None, session_prefix: str = "scheduled") -> str:
    """Run the agent with the scheduled task's prompt."""
    from backends.local_auth import set_request_context
    from llm.client import call_agent
    from llm.context import load_account_context
    from llm.registry import agent_registry

    task_id = task.id
    creator_id = task.creator_id
    org_id = getattr(task, "org_id", None)
    prompt = task.prompt

    # Set request context for the task's creator
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
        resp = await call_agent(agent_id, session_key=session_key, messages=messages, on_progress=on_progress)
        return resp.reply
    finally:
        from backends.local_auth import reset_request_context
        reset_request_context(tokens)


def _task_not_found_stream() -> StreamingResponse:
    return StreamingResponse(
        iter([f"data: {json.dumps({'type': 'error', 'message': 'Task not found'})}\n\n"]),
        media_type="text/event-stream",
    )


def _load_task_snapshot(task_id: str) -> dict | None:
    from db.models import ScheduledTask
    from db.session import SessionLocal

    db = SessionLocal()
    try:
        st = db.query(ScheduledTask).filter_by(id=task_id).first()
        if not st:
            return None
        return {
            "id": st.id,
            "creator_id": st.creator_id,
            "org_id": st.org_id,
            "prompt": st.prompt,
        }
    finally:
        db.close()


def _stream_scheduled_task_response(snapshot: dict, *, simulate: bool) -> StreamingResponse:
    task_id = snapshot["id"]
    creator_id = snapshot["creator_id"]
    org_id = snapshot["org_id"]
    prompt = snapshot["prompt"]

    async def generate():
        from backends.local_auth import reset_request_context, set_request_context
        from db.models import ScheduledTask
        from db.session import SessionLocal
        from llm.tools import simulation_suggestions

        tokens = set_request_context(account_id=creator_id, org_id=org_id)
        sim_token = simulation_suggestions.set([]) if simulate else None
        try:
            queue: asyncio.Queue = asyncio.Queue()

            async def _progress_cb(text: str, **_kwargs):
                await queue.put(text)

            async def _run():
                try:
                    reply = await _execute_task(
                        SimpleNamespace(id=task_id, creator_id=creator_id, org_id=org_id, prompt=prompt),
                        on_progress=_progress_cb,
                        session_prefix="simulate" if simulate else "scheduled",
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
                    row = update_db.query(ScheduledTask).filter_by(id=task_id).first()
                    now = datetime.now(UTC)
                    payload = {"type": "done", "reply": reply}
                    if simulate:
                        if row:
                            row.simulated_at = now
                            update_db.commit()
                        suggestions = _collect_simulation_suggestions(
                            simulation_suggestions.get() or [],
                            [],
                        )
                        if not suggestions:
                            suggestions = _parse_suggestions_from_reply(reply)
                        payload["reply"] = _format_simulation_reply(reply, suggestions)
                        payload["suggestions"] = suggestions
                    elif row:
                        row.last_status = "ok"
                        row.last_output = reply[:5000] if reply else ""
                        row.last_run_at = now
                        row.completed_count += 1
                        row.updated_at = now
                        if row.repeat is not None and row.completed_count >= row.repeat:
                            row.state = "completed"
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
                    row = update_db.query(ScheduledTask).filter_by(id=task_id).first()
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


@router.post("/scheduled-task/{task_id}/run")
async def run_scheduled_task_sse(task_id: str, request: Request):
    """Stream a real scheduled-task run with progress events (SSE)."""
    await require_user(request)
    snapshot = _load_task_snapshot(task_id)
    if not snapshot:
        return _task_not_found_stream()
    return _stream_scheduled_task_response(snapshot, simulate=False)


# ── SSE simulate endpoint ────────────────────────────────────────────────────


@router.post("/scheduled-task/{task_id}/simulate")
async def simulate_scheduled_task_sse(task_id: str, request: Request):
    """Stream a scheduled task simulation with progress events (SSE)."""
    await require_user(request)
    snapshot = _load_task_snapshot(task_id)
    if not snapshot:
        return _task_not_found_stream()
    snapshot["prompt"] = (
        "[SIMULATION — do NOT take direct action. Query the real data, decide what actions "
        "should happen, and call the normal suggestion-producing tools for each action you "
        "would stage. Do not persist side effects. Do not answer with a generic plan or "
        "high-level summary. The simulation result should be the concrete suggestions that "
        "would be created for manager review.]\n\n" + snapshot["prompt"]
    )
    return _stream_scheduled_task_response(snapshot, simulate=True)


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


def _format_simulation_reply(agent_reply: str, suggestions: list[dict]) -> str:
    if not suggestions:
        return agent_reply or "(no output)"

    lines = [
        f"I would create {len(suggestions)} suggestion{'s' if len(suggestions) != 1 else ''}:",
        "",
    ]
    for index, suggestion in enumerate(suggestions, start=1):
        header = f"{index}. {suggestion.get('title') or 'Untitled suggestion'}"
        meta: list[str] = []
        if suggestion.get("category"):
            meta.append(f"category={suggestion['category']}")
        if suggestion.get("urgency"):
            meta.append(f"urgency={suggestion['urgency']}")
        if suggestion.get("property_id"):
            meta.append(f"property={suggestion['property_id']}")
        if suggestion.get("task_id"):
            meta.append(f"task={suggestion['task_id']}")
        if suggestion.get("risk_score") is not None:
            meta.append(f"risk={suggestion['risk_score']}")
        lines.append(header if not meta else f"{header} ({', '.join(meta)})")

        body = (suggestion.get("body") or "").strip()
        if body:
            lines.append(body)

        action_payload = suggestion.get("action_payload") or {}
        if action_payload:
            lines.append("Action payload:")
            lines.append(json.dumps(action_payload, indent=2, sort_keys=True))
        lines.append("")

    return "\n".join(lines).strip()
