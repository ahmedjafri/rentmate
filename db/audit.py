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
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger("rentmate.audit")

from .models import (
    Conversation,
    Message,
    ParticipantType,
    Property,
    Unit,
    Lease,
    Tenant,
)


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

# Statuses that count as "still open" — if one exists we skip creating a dup
_OPEN_STATUSES = {"suggested", "active", "paused"}


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
        db.query(Conversation)
        .filter(
            Conversation.is_task == True,        # noqa: E712
            Conversation.source == "ai_suggestion",
            Conversation.task_status.in_(_OPEN_STATUSES),
            Conversation.subject == subject,
        )
    )
    if property_id:
        q = q.filter(Conversation.property_id == property_id)
    if unit_id:
        q = q.filter(Conversation.unit_id == unit_id)
    return q.first() is not None


def _create_task(
    db: Session,
    subject: str,
    context_body: str,
    category: str,
    urgency: str,
    property_id: Optional[str] = None,
    unit_id: Optional[str] = None,
    autonomy_level: Optional[str] = None,
) -> None:
    """Insert a suggested ai_suggestion task with a context message."""
    task_mode, task_status = _AUTONOMY_MODE.get(autonomy_level or "suggest", _DEFAULT_MODE)
    task = Conversation(
        id=str(uuid.uuid4()),
        subject=subject,
        is_task=True,
        task_status=task_status,
        task_mode=task_mode,
        source="ai_suggestion",
        category=category,
        urgency=urgency,
        priority="routine",
        confidential=False,
        property_id=property_id,
        unit_id=unit_id,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(task)
    db.flush()  # get task.id without committing

    msg = Message(
        id=str(uuid.uuid4()),
        conversation_id=task.id,
        sender_type=ParticipantType.ACCOUNT_USER,
        body=context_body,
        message_type="context",
        sender_name="RentMate",
        is_ai=True,
        sent_at=datetime.utcnow(),
    )
    db.add(msg)
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
            category="compliance",
            urgency="low",
            property_id=p.id,
            autonomy_level=autonomy_level,
        )
        created += 1
    return created


def _check_vacant_units(db: Session, min_vacancy_days: int = 0, dry_run: bool = False, autonomy_level: Optional[str] = None) -> int:
    """Flag units that have no active (non-expired) lease."""
    created = 0
    today = date.today()
    units = db.query(Unit).all()
    for unit in units:
        active_leases = [
            l for l in unit.leases
            if l.end_date >= today
        ]
        if active_leases:
            continue

        prop = unit.property
        prop_label = _addr_summary(prop) if prop else "unknown property"
        subject = f"Vacant unit: {unit.label} at {prop_label}"
        if not dry_run and _task_exists(db, subject, property_id=unit.property_id, unit_id=unit.id):
            continue

        # Build richer context from lease history
        past_leases = sorted(unit.leases, key=lambda l: l.end_date, reverse=True)
        last_lease = past_leases[0] if past_leases else None

        days_vacant = (today - last_lease.end_date).days if last_lease else 0
        # Respect min_vacancy_days threshold — applies to never-leased units too (days_vacant=0)
        if days_vacant < min_vacancy_days:
            continue

        if last_lease:
            tenant_name = (
                f"{last_lease.tenant.first_name} {last_lease.tenant.last_name}".strip()
                if last_lease.tenant else "previous tenant"
            )
            urgency = "high" if days_vacant > 60 else "medium" if days_vacant > 14 else "low"
            vacancy_line = (
                f"Unit {unit.label!r} at {prop_label} has been vacant for "
                f"{days_vacant} day{'s' if days_vacant != 1 else ''} "
                f"(last leased to {tenant_name}, ended {last_lease.end_date})."
            )
            rent_hint = (
                f" Previous rent was ${last_lease.rent_amount:,.2f}/month — "
                "use this as a baseline when listing."
            )
        else:
            urgency = "low"
            vacancy_line = (
                f"Unit {unit.label!r} at {prop_label} has never had a lease on record."
            )
            rent_hint = ""

        context_body = (
            f"{vacancy_line}{rent_hint}\n\n"
            "Suggested next steps:\n"
            "1. Confirm the unit is move-in ready (inspect, clean, repairs).\n"
            "2. List the unit on rental platforms with current pricing.\n"
            "3. Follow up with any existing prospects or waitlist applicants.\n"
            "4. Once a tenant is found, create a new lease in RentMate."
        )

        _create_task(
            db,
            subject=subject,
            context_body=context_body,
            category="leasing",
            urgency=urgency,
            property_id=unit.property_id,
            unit_id=unit.id,
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

        # Try to find a linked property for context
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
            category="compliance",
            urgency="low",
            property_id=prop_id,
            autonomy_level=autonomy_level,
        )
        created += 1
    return created


def _check_expiring_leases(db: Session, warn_days: int = EXPIRY_WARN_DAYS, dry_run: bool = False, autonomy_level: Optional[str] = None) -> int:
    """Flag leases expiring within warn_days days."""
    created = 0
    today = date.today()
    cutoff = today + timedelta(days=warn_days)

    leases = (
        db.query(Lease)
        .filter(Lease.end_date >= today, Lease.end_date <= cutoff)
        .all()
    )
    for lease in leases:
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
            category="leasing",
            urgency="medium" if days_left > 30 else "high",
            property_id=lease.property_id,
            unit_id=lease.unit_id,
            autonomy_level=autonomy_level,
        )
        created += 1
    return created


def _check_overdue_rent(db: Session, dry_run: bool = False, autonomy_level: Optional[str] = None) -> int:
    """Flag leases whose payment_status is late or overdue."""
    created = 0
    leases = (
        db.query(Lease)
        .filter(Lease.payment_status.in_(["late", "overdue"]))
        .all()
    )
    for lease in leases:
        tenant_name = (
            f"{lease.tenant.first_name} {lease.tenant.last_name}".strip()
            if lease.tenant else "Unknown tenant"
        )
        unit_label = lease.unit.label if lease.unit else "unknown unit"

        subject = f"Overdue rent: {tenant_name} – {unit_label}"
        if not dry_run and _task_exists(db, subject, property_id=lease.property_id, unit_id=lease.unit_id):
            continue

        prop_label = _addr_summary(lease.property) if lease.property else "unknown property"
        _create_task(
            db,
            subject=subject,
            context_body=(
                f"{tenant_name} at {unit_label}, {prop_label} "
                f"has payment status '{lease.payment_status}' "
                f"(${lease.rent_amount:,.2f}/month). Follow up on outstanding rent."
            ),
            category="rent",
            urgency="high",
            property_id=lease.property_id,
            unit_id=lease.unit_id,
            autonomy_level=autonomy_level,
        )
        created += 1
    return created


def _check_expired_leases(db: Session, dry_run: bool = False, autonomy_level: Optional[str] = None) -> int:
    """Flag leases that expired without a newer lease replacing them."""
    created = 0
    today = date.today()

    expired = (
        db.query(Lease)
        .filter(Lease.end_date < today)
        .all()
    )
    for lease in expired:
        # Skip if the same unit has a newer active lease
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
            category="leasing",
            urgency="high",
            property_id=lease.property_id,
            unit_id=lease.unit_id,
            autonomy_level=autonomy_level,
        )
        created += 1
    return created


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_data_audit(
    db: Session,
    config: dict | None = None,
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
        if _enabled("incomplete_properties"):
            total += _check_incomplete_properties(db, dry_run=dry_run, autonomy_level=_autonomy("compliance"))
        if _enabled("vacant_units"):
            min_days = checks.get("vacant_units", {}).get("min_vacancy_days", 0)
            total += _check_vacant_units(db, min_vacancy_days=min_days, dry_run=dry_run, autonomy_level=_autonomy("leasing"))
        if _enabled("missing_contact"):
            total += _check_tenants_missing_contact(db, dry_run=dry_run, autonomy_level=_autonomy("compliance"))
        if _enabled("expiring_leases"):
            warn_days = checks.get("expiring_leases", {}).get("warn_days", EXPIRY_WARN_DAYS)
            total += _check_expiring_leases(db, warn_days=warn_days, dry_run=dry_run, autonomy_level=_autonomy("leasing"))
        if _enabled("expired_leases"):
            total += _check_expired_leases(db, dry_run=dry_run, autonomy_level=_autonomy("leasing"))
        if _enabled("overdue_rent"):
            total += _check_overdue_rent(db, dry_run=dry_run, autonomy_level=_autonomy("rent"))
        if total:
            logger.info("Created %d new suggested task(s).", total)
        else:
            logger.debug("No new tasks needed.")
    except Exception as exc:
        db.rollback()
        logger.exception("Error during data audit: %s", exc)
        return 0

    # Run custom automation scripts
    custom_meta = cfg.get("custom_meta", {})
    logger.info("audit: custom_meta keys=%r  check_name=%r", list(custom_meta.keys()), check_name)
    if custom_meta:
        from db.dsl_runner import run_script
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
                total += run_script(db, script, params=check_params, dry_run=dry_run)
            except Exception as exc:
                logger.exception("Error running DSL script for %r: %s", custom_key, exc)

    return total
