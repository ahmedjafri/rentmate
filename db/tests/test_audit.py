# tests/test_audit.py
"""
Tests for the periodic data-quality auditor (db/audit.py).

Covers:
- incomplete property address → creates compliance task
- complete property → no task
- vacant unit → creates leasing task
- unit with active lease → no task
- tenant with no phone or email → creates compliance task
- tenant with phone only / email only → no task
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
# Incomplete property
# ---------------------------------------------------------------------------

class TestIncompleteProperty:

    def test_missing_city_creates_task(self, db):
        _prop(db, city=None)
        count = run_data_audit(db)
        assert count == 1
        tasks = _open_tasks(db)
        assert len(tasks) == 1
        assert "Incomplete address" in tasks[0].title
        assert tasks[0].category == "compliance"
        assert tasks[0].urgency == "low"

    def test_missing_state_creates_task(self, db):
        _prop(db, state=None)
        count = run_data_audit(db)
        assert count == 1

    def test_missing_postal_creates_task(self, db):
        _prop(db, postal_code=None)
        count = run_data_audit(db)
        assert count == 1

    def test_complete_address_no_task(self, db):
        _prop(db)  # all fields populated
        count = run_data_audit(db)
        assert count == 0

    def test_task_has_context_message(self, db):
        _prop(db, city=None)
        run_data_audit(db)
        task = _open_tasks(db)[0]
        conv_id = task.conversations[0].id
        msgs = db.query(Message).filter(Message.conversation_id == conv_id).all()
        assert len(msgs) == 1
        assert msgs[0].message_type == "context"
        assert msgs[0].is_ai is True

    def test_property_id_set_on_task(self, db):
        p = _prop(db, city=None)
        run_data_audit(db)
        task = _open_tasks(db)[0]
        assert task.property_id == p.id


# ---------------------------------------------------------------------------
# Vacant units
# ---------------------------------------------------------------------------

class TestVacantUnits:

    def test_unit_with_no_lease_creates_task(self, db):
        p = _prop(db)
        _unit(db, p)
        count = run_data_audit(db)
        assert count == 1
        tasks = _open_tasks(db)
        assert any("Vacant unit" in t.title for t in tasks)

    def test_unit_with_active_lease_no_task(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-1234")
        _lease(db, p, u, t)  # active (ends in 1 year)
        count = run_data_audit(db)
        assert count == 0

    def test_unit_with_only_expired_lease_creates_task(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-1234")
        _lease(db, p, u, t, end=date.today() - timedelta(days=1))
        count = run_data_audit(db)
        # vacant unit + expired lease = 2 tasks
        subjects = [task.title for task in _open_tasks(db)]
        assert any("Vacant unit" in s for s in subjects)

    def test_vacant_task_linked_to_unit(self, db):
        p = _prop(db)
        u = _unit(db, p)
        run_data_audit(db)
        tasks = _open_tasks(db)
        vacant_tasks = [t for t in tasks if "Vacant unit" in t.title]
        assert vacant_tasks[0].unit_id == u.id
        assert vacant_tasks[0].property_id == p.id


# ---------------------------------------------------------------------------
# Tenants missing contact info
# ---------------------------------------------------------------------------

class TestMissingContact:

    def test_no_phone_no_email_creates_task(self, db):
        _tenant(db)  # no phone, no email
        count = run_data_audit(db)
        assert count == 1
        tasks = _open_tasks(db)
        assert any("Missing contact info" in t.title for t in tasks)

    def test_phone_only_no_task(self, db):
        _tenant(db, phone="555-9999")
        count = run_data_audit(db)
        assert count == 0

    def test_email_only_no_task(self, db):
        _tenant(db, email="bob@example.com")
        count = run_data_audit(db)
        assert count == 0

    def test_both_contact_fields_no_task(self, db):
        _tenant(db, phone="555-0000", email="x@y.com")
        count = run_data_audit(db)
        assert count == 0

    def test_contact_task_includes_tenant_name(self, db):
        _tenant(db, first="Jane", last="Doe")
        run_data_audit(db)
        tasks = _open_tasks(db)
        contact_task = next(t for t in tasks if "Missing contact info" in t.title)
        assert "Jane Doe" in contact_task.title


# ---------------------------------------------------------------------------
# Expiring leases
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


# ---------------------------------------------------------------------------
# Expired leases
# ---------------------------------------------------------------------------

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
        # Newer active lease on the same unit
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
        _prop(db, city=None)
        run_data_audit(db)
        run_data_audit(db)
        tasks = _open_tasks(db)
        incomplete = [t for t in tasks if "Incomplete address" in t.title]
        assert len(incomplete) == 1

    def test_resolved_task_is_recreated_on_next_run(self, db):
        _prop(db, city=None)
        run_data_audit(db)
        # Resolve the task
        task = next(t for t in _open_tasks(db) if "Incomplete address" in t.title)
        task.task_status = "resolved"
        db.flush()
        # Second run should create a new one
        run_data_audit(db)
        open_tasks = [
            t for t in db.query(Task).filter(
                Task.source == "ai_suggestion",
                Task.task_status != "resolved",
            ).all()
            if "Incomplete address" in t.title
        ]
        assert len(open_tasks) == 1

    def test_cancelled_task_is_recreated_on_next_run(self, db):
        _prop(db, city=None)
        run_data_audit(db)
        task = next(t for t in _open_tasks(db) if "Incomplete address" in t.title)
        task.task_status = "cancelled"
        db.flush()
        run_data_audit(db)
        open_tasks = [
            t for t in db.query(Task).filter(
                Task.source == "ai_suggestion",
                Task.task_status.in_(["suggested", "active", "paused"]),
            ).all()
            if "Incomplete address" in t.title
        ]
        assert len(open_tasks) == 1

    def test_multiple_issues_no_cross_dedup(self, db):
        """Two separate properties with incomplete addresses → two tasks."""
        _prop(db, address="100 Elm St", city=None)
        _prop(db, address="200 Pine St", state=None)
        count = run_data_audit(db)
        assert count == 2


# ---------------------------------------------------------------------------
# Return value
# ---------------------------------------------------------------------------

class TestReturnValue:

    def test_returns_zero_when_nothing_to_do(self, db):
        assert run_data_audit(db) == 0

    def test_returns_count_of_created_tasks(self, db):
        _prop(db, city=None)     # 1 compliance
        _prop(db, state=None)    # 1 compliance
        t = _tenant(db)          # 1 compliance (no contact)
        count = run_data_audit(db)
        assert count == 3

    def test_no_crash_on_empty_db(self, db):
        """Audit should silently return 0 on a completely empty database."""
        count = run_data_audit(db)
        assert count == 0
