"""Scheduled task executor — runs AI agent on cron schedules.

Replaces the old Property-Flow DSL automation system with natural language
prompts executed on configurable schedules.
"""
import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta

from croniter import croniter

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

        from backends.local_auth import DEFAULT_CREATOR_ID

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
        ]

        import uuid
        now = datetime.now(UTC)
        for d in defaults:
            nxt = next_run(d["schedule"])
            db.add(ScheduledTask(
                id=str(uuid.uuid4()),
                creator_id=DEFAULT_CREATOR_ID,
                name=d["name"],
                prompt=d["prompt"],
                schedule=d["schedule"],
                schedule_display=d["schedule_display"],
                enabled=True,
                state="scheduled",
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


async def _execute_task(task) -> str:
    """Run the agent with the scheduled task's prompt."""
    from backends.local_auth import DEFAULT_USER_ID, set_request_context
    from llm.client import call_agent
    from llm.context import load_account_context
    from llm.registry import agent_registry

    # Set request context for the task's creator
    creator_id = task.creator_id or DEFAULT_USER_ID
    tokens = set_request_context(user_id=creator_id, account_id=creator_id)

    try:
        from db.session import SessionLocal
        db = SessionLocal()
        try:
            agent_id = agent_registry.ensure_agent(creator_id, db)
            context = load_account_context(db)
        finally:
            db.close()

        messages = [
            {"role": "system", "content": context},
            {"role": "user", "content": task.prompt},
        ]

        session_key = f"scheduled:{task.id}"
        resp = await call_agent(agent_id, session_key=session_key, messages=messages)
        return resp.reply
    finally:
        from backends.local_auth import reset_request_context
        reset_request_context(tokens)
