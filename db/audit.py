# db/audit.py
"""
Periodic data-quality auditor.

Scans the database for incomplete or actionable records and creates
`suggested` tasks in the action desk.  Deduplication is done by matching
(source, subject, property_id, unit_id) against open tasks so we never
create the same suggestion twice unless the previous one was
resolved/cancelled.
"""

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from .enums import TaskCategory, TaskSource, Urgency
from .models import (
    Conversation,
    Lease,
    Message,
    MessageType,
    ParticipantType,
    Property,
    Task,
    Tenant,
    Unit,
)

logger = logging.getLogger("rentmate.audit")

# How many days before lease end to flag as "expiring soon"
EXPIRY_WARN_DAYS = 60

# Maps autonomy level → (task_mode, task_status)
# "manual"     = Notify Only    — task visible but agent takes no autonomous action
# "suggest"    = Draft & Wait   — waiting for manager approval (default)
# "autonomous" = Auto-Execute   — agent handles it immediately
_AUTONOMY_MODE: dict[str, tuple[str, str]] = {
    "manual":     ("manual",           "suggested"),
    "suggest":    ("waiting_approval", "suggested"),
    "autonomous": ("autonomous",       "active"),
}
_DEFAULT_MODE = _AUTONOMY_MODE["suggest"]

# Statuses that count as "still open" — if one exists we skip creating a dup.
# 'dismissed' is included so dismissed tasks are never re-created by the audit.
_OPEN_STATUSES = {"suggested", "active", "paused", "dismissed"}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _task_exists(
    db: Session,
    subject: str,
    property_id: Optional[str] = None,
    unit_id: Optional[str] = None,
) -> bool:
    """Return True if a non-closed ai_suggestion task with this exact
    subject + property/unit combo already exists."""
    q = (
        db.query(Task)
        .filter(
            Task.source == TaskSource.AI_SUGGESTION,
            Task.task_status.in_(_OPEN_STATUSES),
            Task.title == subject,
        )
    )
    if property_id:
        q = q.filter(Task.property_id == property_id)
    if unit_id:
        q = q.filter(Task.unit_id == unit_id)
    return q.first() is not None


def _get_account_id(db: Session, property_id: Optional[str], unit_id: Optional[str]) -> str:
    """Lookup the account that owns a property/unit."""
    try:
        if property_id:
            prop = db.query(Property).filter(Property.id == property_id).first()
            if prop and prop.account_id:
                return prop.account_id
        if unit_id:
            unit = db.query(Unit).filter(Unit.id == unit_id).first()
            if unit and unit.account_id:
                return unit.account_id
    except Exception:
        pass
    return "00000000-0000-0000-0000-000000000001"

def _create_task(
    db: Session,
    subject: str,
    context_body: str,
    category: TaskCategory,
    urgency: Urgency,
    property_id: Optional[str] = None,
    unit_id: Optional[str] = None,
    autonomy_level: Optional[str] = None,
    tenant_name: Optional[str] = None,
    property_address: Optional[str] = None,
) -> None:
    """Insert a suggested ai_suggestion task with a context message and, when in
    waiting_approval mode, an approval message containing a draft suggested action."""
    task_mode, task_status = _AUTONOMY_MODE.get(autonomy_level or "suggest", _DEFAULT_MODE)
    task_id = str(uuid.uuid4())
    task = Task(
        id=task_id,
        account_id=_get_account_id(db, property_id, unit_id),
        title=subject,
        task_status=task_status,
        task_mode=task_mode,
        source=TaskSource.AI_SUGGESTION,
        category=category,
        urgency=urgency,
        priority="routine",
        confidential=False,
        property_id=property_id,
        unit_id=unit_id,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(task)
    db.flush()  # get task.id without committing

    # Assign task_number per account
    from sqlalchemy import func as sa_func, select as sa_select
    max_num = db.execute(
        sa_select(sa_func.coalesce(sa_func.max(Task.task_number), 0))
        .where(Task.account_id == task.account_id)
    ).scalar()
    task.task_number = max_num + 1

    # Create primary internal conversation thread for this task
    convo = Conversation(
        id=str(uuid.uuid4()),
        subject=subject,
        property_id=property_id,
        unit_id=unit_id,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(convo)
    db.flush()

    task.ai_conversation_id = convo.id

    db.add(Message(
        id=str(uuid.uuid4()),
        conversation_id=convo.id,
        sender_type=ParticipantType.ACCOUNT_USER,
        body=context_body,
        message_type=MessageType.CONTEXT,
        sender_name="RentMate",
        is_ai=True,
        sent_at=datetime.now(UTC),
    ))
    db.flush()

    # For suggest/waiting_approval tasks, generate a draft action the manager can send
    if task_mode == "waiting_approval":
        from llm.suggest import generate_task_suggestion
        draft = generate_task_suggestion(
            subject=subject,
            context_body=context_body,
            category=category,
            tenant_name=tenant_name,
            property_address=property_address,
        )
        if draft:
            db.add(Message(
                id=str(uuid.uuid4()),
                conversation_id=convo.id,
                sender_type=ParticipantType.ACCOUNT_USER,
                body="Here's a suggested message you can send:",
                message_type=MessageType.SUGGESTION,
                sender_name="RentMate",
                is_ai=True,
                draft_reply=draft,
                sent_at=datetime.now(UTC),
            ))
            db.flush()

    logger.debug("Created audit task: %r (category=%s, urgency=%s)", subject, category, urgency)


def _addr_summary(p: Property) -> str:
    parts = [p.address_line1, p.city, p.state, p.postal_code]
    return ", ".join(x for x in parts if x) or f"property {p.id[:8]}"


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _check_incomplete_properties(db: Session, dry_run: bool = False, autonomy_level: Optional[str] = None) -> int:
    """Flag properties missing city, state, or postal code."""
    created = 0
    props = db.query(Property).all()
    for p in props:
        missing = [
            f for f, v in [("city", p.city), ("state", p.state), ("postal code", p.postal_code)]
            if not v
        ]
        if not missing:
            continue

        subject = f"Incomplete address: {_addr_summary(p)}"
        if not dry_run and _task_exists(db, subject, property_id=p.id):
            continue

        fields = ", ".join(missing)
        _create_task(
            db,
            subject=subject,
            context_body=(
                f'Property "{_addr_summary(p)}" is missing {fields}. '
                "Complete the address so leases and documents are accurate."
            ),
            category=TaskCategory.COMPLIANCE,
            urgency=Urgency.LOW,
            property_id=p.id,
            autonomy_level=autonomy_level,
        )
        created += 1
    return created


def _check_tenants_missing_contact(db: Session, dry_run: bool = False, autonomy_level: Optional[str] = None) -> int:
    """Flag tenants with neither phone nor email."""
    created = 0
    tenants = db.query(Tenant).all()
    for t in tenants:
        if t.phone or t.email:
            continue

        name = f"{t.first_name} {t.last_name}".strip()
        subject = f"Missing contact info: {name}"
        if not dry_run and _task_exists(db, subject):
            continue

        prop_id = None
        if t.leases:
            prop_id = t.leases[0].property_id

        _create_task(
            db,
            subject=subject,
            context_body=(
                f"Tenant {name!r} has no phone number or email address on file. "
                "Add contact details so they can be reached."
            ),
            category=TaskCategory.COMPLIANCE,
            urgency=Urgency.LOW,
            property_id=prop_id,
            autonomy_level=autonomy_level,
        )
        created += 1
    return created


def _check_lease_status(db: Session, warn_days: int = EXPIRY_WARN_DAYS, dry_run: bool = False, autonomy_level: Optional[str] = None) -> int:
    """Flag leases that are expiring soon or already expired without replacement."""
    created = 0
    today = date.today()
    cutoff = today + timedelta(days=warn_days)

    # Expiring leases (end_date between today and cutoff)
    expiring = (
        db.query(Lease)
        .filter(Lease.end_date >= today, Lease.end_date <= cutoff)
        .all()
    )
    for lease in expiring:
        tenant_name = (
            f"{lease.tenant.first_name} {lease.tenant.last_name}".strip()
            if lease.tenant else "Unknown tenant"
        )
        unit_label = lease.unit.label if lease.unit else "unknown unit"
        prop_label = _addr_summary(lease.property) if lease.property else "unknown property"
        days_left = (lease.end_date - today).days

        subject = f"Lease expiring {lease.end_date}: {tenant_name} – {unit_label}"
        if not dry_run and _task_exists(db, subject, property_id=lease.property_id, unit_id=lease.unit_id):
            continue

        _create_task(
            db,
            subject=subject,
            context_body=(
                f"Lease for {tenant_name} at {unit_label}, {prop_label} "
                f"expires on {lease.end_date} ({days_left} days). "
                "Reach out about renewal or move-out logistics."
            ),
            category=TaskCategory.LEASING,
            urgency=Urgency.MEDIUM if days_left > 30 else Urgency.HIGH,
            property_id=lease.property_id,
            unit_id=lease.unit_id,
            autonomy_level=autonomy_level,
            tenant_name=tenant_name,
            property_address=prop_label,
        )
        created += 1

    # Expired leases (end_date < today, no newer active lease on the same unit)
    expired = (
        db.query(Lease)
        .filter(Lease.end_date < today)
        .all()
    )
    for lease in expired:
        newer = any(
            l.id != lease.id and l.end_date >= today
            for l in (lease.unit.leases if lease.unit else [])
        )
        if newer:
            continue

        tenant_name = (
            f"{lease.tenant.first_name} {lease.tenant.last_name}".strip()
            if lease.tenant else "Unknown tenant"
        )
        unit_label = lease.unit.label if lease.unit else "unknown unit"
        prop_label = _addr_summary(lease.property) if lease.property else "unknown property"

        subject = f"Expired lease: {tenant_name} – {unit_label}"
        if not dry_run and _task_exists(db, subject, property_id=lease.property_id, unit_id=lease.unit_id):
            continue

        _create_task(
            db,
            subject=subject,
            context_body=(
                f"The lease for {tenant_name} at {unit_label}, {prop_label} "
                f"expired on {lease.end_date} and has not been renewed. "
                "Confirm move-out status or start a renewal conversation."
            ),
            category=TaskCategory.LEASING,
            urgency=Urgency.HIGH,
            property_id=lease.property_id,
            unit_id=lease.unit_id,
            autonomy_level=autonomy_level,
            tenant_name=tenant_name,
            property_address=prop_label,
        )
        created += 1

    return created


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_data_audit(
    db: Session,
    *, config: dict | None = None,
    check_name: str | None = None,
    dry_run: bool = False,
) -> int:
    """
    Run data-quality checks and flush any new suggested tasks.
    Returns the total number of new tasks created.

    NOTE: does NOT commit — the caller is responsible for committing so that
    this function composes correctly with test savepoint patterns.

    Pass a config dict to enable/disable checks and tune their parameters.
    Pass check_name to run only a single named check.
    Pass dry_run=True (simulation) to skip deduplication in DSL scripts.
    """
    cfg = config or {}
    checks = cfg.get("checks", {})
    autonomy = cfg.get("autonomy", {})

    def _autonomy(category: str) -> Optional[str]:
        return autonomy.get(category)

    def _enabled(name: str) -> bool:
        if check_name is not None:
            # Explicit single-check run (e.g. simulate): skip others, always run the target
            return name == check_name
        return checks.get(name, {}).get("enabled", True)

    total = 0
    try:
        if _enabled("lease_status"):
            warn_days = checks.get("lease_status", {}).get("warn_days", EXPIRY_WARN_DAYS)
            total += _check_lease_status(db, warn_days=warn_days, dry_run=dry_run, autonomy_level=_autonomy("leasing"))
        if _enabled("incomplete_properties"):
            total += _check_incomplete_properties(db, dry_run=dry_run, autonomy_level=_autonomy("compliance"))
        if _enabled("missing_contact"):
            total += _check_tenants_missing_contact(db, dry_run=dry_run, autonomy_level=_autonomy("compliance"))
        if total:
            logger.info("Created %d new suggested task(s).", total)
        else:
            logger.debug("No new tasks needed.")
    except Exception as exc:
        db.rollback()
        logger.exception("Error during data audit: %s", exc)
        return 0

    # Run built-in DSL scripts for checks not handled by a dedicated Python function above.
    # Like custom automations, these only run when explicitly enabled in the config.
    _PYTHON_HANDLED = {
        "lease_status", "incomplete_properties", "missing_contact",
    }
    from db.dsl_runner import run_script
    from handlers.default_automations import _CHECK_META as _BUILTIN_META
    for builtin_key, meta in _BUILTIN_META.items():
        if builtin_key in _PYTHON_HANDLED:
            continue
        if check_name is not None and check_name != builtin_key:
            continue
        if check_name is None and not checks.get(builtin_key, {}).get("enabled", False):
            continue
        script = meta.get("script")
        if not script:
            continue
        logger.info("Running built-in DSL script for %r", builtin_key)
        check_params = {k: v for k, v in checks.get(builtin_key, {}).items()
                        if k not in ("enabled", "interval_hours")}
        try:
            total += run_script(db, script_yaml=script, params=check_params, dry_run=dry_run)
        except Exception as exc:
            logger.exception("Error running built-in DSL script for %r: %s", builtin_key, exc)

    # Run custom automation scripts
    custom_meta = cfg.get("custom_meta", {})
    logger.info("audit: custom_meta keys=%r  check_name=%r", list(custom_meta.keys()), check_name)
    if custom_meta:
        for custom_key, meta in custom_meta.items():
            if check_name is not None and custom_key != check_name:
                continue
            if check_name is None and not checks.get(custom_key, {}).get("enabled", False):
                continue
            script = meta.get("script")
            if not script:
                logger.info("Custom automation %r has no script — skipping", custom_key)
                continue
            logger.info("Running DSL script for %r (script length=%d)", custom_key, len(script))
            check_params = {k: v for k, v in checks.get(custom_key, {}).items()
                            if k not in ("enabled", "interval_hours")}
            try:
                total += run_script(db, script_yaml=script, params=check_params, dry_run=dry_run)
            except Exception as exc:
                logger.exception("Error running DSL script for %r: %s", custom_key, exc)

    return total
