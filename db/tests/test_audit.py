# tests/test_audit.py
"""
Tests for the periodic data-quality auditor (db/audit.py).

Covers:
- lease expiring within 60 days (>30) → creates task with urgency=medium
- lease expiring within 30 days → creates task with urgency=high
- lease not expiring soon → no task
- expired lease with no replacement → creates task
- expired lease replaced by newer active lease → no task
- duplicate suppression: running audit twice creates each task only once
- resolved task → re-created on next audit run
- run_data_audit returns correct created count
"""

import pytest
from datetime import date, timedelta

from db.models import (
    Base,
    Property,
    Unit,
    Tenant,
    Lease,
    Task,
    Conversation,
    Message,
)
from db.audit import run_data_audit, EXPIRY_WARN_DAYS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _prop(db, address="100 Oak St", city="Portland", state="OR", postal_code="97201"):
    p = Property(address_line1=address, city=city, state=state, postal_code=postal_code)
    db.add(p)
    db.flush()
    return p


def _unit(db, prop, label="1A"):
    u = Unit(property_id=prop.id, label=label)
    db.add(u)
    db.flush()
    return u


def _tenant(db, first="Bob", last="Smith", phone=None, email=None):
    t = Tenant(first_name=first, last_name=last, phone=phone, email=email)
    db.add(t)
    db.flush()
    return t


def _lease(db, prop, unit, tenant, start=None, end=None):
    today = date.today()
    l = Lease(
        property_id=prop.id,
        unit_id=unit.id,
        tenant_id=tenant.id,
        start_date=start or today - timedelta(days=30),
        end_date=end or today + timedelta(days=365),
        rent_amount=1200.0,
    )
    db.add(l)
    db.flush()
    return l


def _open_tasks(db):
    return (
        db.query(Task)
        .filter(Task.source == "ai_suggestion")
        .all()
    )


# ---------------------------------------------------------------------------
# Lease status (expiring + expired combined)
# ---------------------------------------------------------------------------

class TestExpiringLeases:

    def test_lease_expiring_within_60_days_creates_task(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-1111")
        _lease(db, p, u, t, end=date.today() + timedelta(days=45))
        count = run_data_audit(db)
        tasks = _open_tasks(db)
        assert any("Lease expiring" in task.title for task in tasks)

    def test_expiring_over_30_days_is_medium_urgency(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-1111")
        _lease(db, p, u, t, end=date.today() + timedelta(days=45))
        run_data_audit(db)
        tasks = _open_tasks(db)
        expiring = next(task for task in tasks if "Lease expiring" in task.title)
        assert expiring.urgency == "medium"

    def test_expiring_within_30_days_is_high_urgency(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-1111")
        _lease(db, p, u, t, end=date.today() + timedelta(days=20))
        run_data_audit(db)
        tasks = _open_tasks(db)
        expiring = next(task for task in tasks if "Lease expiring" in task.title)
        assert expiring.urgency == "high"

    def test_lease_not_expiring_soon_no_task(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-1111")
        _lease(db, p, u, t, end=date.today() + timedelta(days=EXPIRY_WARN_DAYS + 10))
        count = run_data_audit(db)
        assert count == 0

    def test_expiring_subject_uses_end_date_not_days_left(self, db):
        """Dedup must survive re-runs — subject must be stable (use end_date)."""
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-1111")
        end = date.today() + timedelta(days=40)
        _lease(db, p, u, t, end=end)
        run_data_audit(db)
        tasks = _open_tasks(db)
        expiring = next(task for task in tasks if "Lease expiring" in task.title)
        assert str(end) in expiring.title


class TestExpiredLeases:

    def test_expired_lease_no_replacement_creates_task(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-2222")
        _lease(db, p, u, t, end=date.today() - timedelta(days=10))
        count = run_data_audit(db)
        tasks = _open_tasks(db)
        assert any("Expired lease" in task.title for task in tasks)
        assert any("Expired lease" in task.title and task.urgency == "high" for task in tasks)

    def test_expired_lease_with_newer_active_lease_no_task(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-3333")
        _lease(db, p, u, t, end=date.today() - timedelta(days=10))
        t2 = _tenant(db, first="New", last="Tenant", phone="555-4444")
        _lease(db, p, u, t2, start=date.today() - timedelta(days=5), end=date.today() + timedelta(days=365))
        count = run_data_audit(db)
        subjects = [task.title for task in _open_tasks(db)]
        assert not any("Expired lease" in s for s in subjects)


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

class TestDeduplication:

    def test_running_audit_twice_creates_task_only_once(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-1234")
        _lease(db, p, u, t, end=date.today() + timedelta(days=20))
        run_data_audit(db)
        run_data_audit(db)
        tasks = _open_tasks(db)
        expiring = [t for t in tasks if "Lease expiring" in t.title]
        assert len(expiring) == 1

    def test_resolved_task_is_recreated_on_next_run(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-1234")
        _lease(db, p, u, t, end=date.today() + timedelta(days=20))
        run_data_audit(db)
        task = next(t for t in _open_tasks(db) if "Lease expiring" in t.title)
        task.task_status = "resolved"
        db.flush()
        run_data_audit(db)
        open_tasks = [
            t for t in db.query(Task).filter(
                Task.source == "ai_suggestion",
                Task.task_status != "resolved",
            ).all()
            if "Lease expiring" in t.title
        ]
        assert len(open_tasks) == 1

    def test_cancelled_task_is_recreated_on_next_run(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-1234")
        _lease(db, p, u, t, end=date.today() + timedelta(days=20))
        run_data_audit(db)
        task = next(t for t in _open_tasks(db) if "Lease expiring" in t.title)
        task.task_status = "cancelled"
        db.flush()
        run_data_audit(db)
        open_tasks = [
            t for t in db.query(Task).filter(
                Task.source == "ai_suggestion",
                Task.task_status.in_(["suggested", "active", "paused"]),
            ).all()
            if "Lease expiring" in t.title
        ]
        assert len(open_tasks) == 1

    def test_multiple_issues_no_cross_dedup(self, db):
        """Two expiring leases at different properties → two tasks."""
        p1 = _prop(db, address="100 Elm St")
        u1 = _unit(db, p1, label="A1")
        t1 = _tenant(db, first="A", phone="555-1111")
        _lease(db, p1, u1, t1, end=date.today() + timedelta(days=20))
        p2 = _prop(db, address="200 Pine St")
        u2 = _unit(db, p2, label="B1")
        t2 = _tenant(db, first="B", phone="555-2222")
        _lease(db, p2, u2, t2, end=date.today() + timedelta(days=20))
        count = run_data_audit(db)
        assert count >= 2


# ---------------------------------------------------------------------------
# Return value
# ---------------------------------------------------------------------------

class TestReturnValue:

    def test_returns_zero_when_nothing_to_do(self, db):
        assert run_data_audit(db) == 0

    def test_returns_count_of_created_tasks(self, db):
        p1 = _prop(db)
        u1 = _unit(db, p1, label="A")
        t1 = _tenant(db, first="A", phone="555-1111")
        _lease(db, p1, u1, t1, end=date.today() + timedelta(days=20))
        p2 = _prop(db, address="200 Oak")
        u2 = _unit(db, p2, label="B")
        t2 = _tenant(db, first="B", phone="555-2222")
        _lease(db, p2, u2, t2, end=date.today() + timedelta(days=20))
        count = run_data_audit(db)
        assert count == 2

    def test_no_crash_on_empty_db(self, db):
        """Audit should silently return 0 on a completely empty database."""
        count = run_data_audit(db)
        assert count == 0
