# tests/test_dsl.py
"""
Exhaustive tests for Property-Flow DSL behaviour as implemented in db/audit.py.

Each test class corresponds to one of the six built-in checks.  The tests
validate the exact conditions, urgency expressions, param bindings, scope
filters, deduplication rules, and cross-check interactions described in the
Property-Flow YAML scripts in handlers/automations._CHECK_META.

DSL logic under test
--------------------
vacant_units
  conditions : active_lease_count == 0
               days_vacant >= params.min_vacancy_days
  urgency    : high   if days_vacant > 60
               medium if days_vacant > 14
               low    otherwise

expiring_leases
  scope filters : end_date >= today
                  end_date <= today + params.warn_days
  urgency       : high   if days_until_end <= 30
                  medium otherwise

overdue_rent
  scope filters : payment_status in [late, overdue]
  urgency       : high (unconditional)
  category      : rent

incomplete_properties
  conditions : any_of(city not_exists, state not_exists, postal_code not_exists)
  urgency    : low
  category   : compliance

missing_contact
  conditions : phone not_exists  AND  email not_exists
  urgency    : low
  category   : compliance

expired_leases
  scope filters : end_date < today
  conditions    : unit.active_lease_count == 0
  urgency       : high
  category      : leasing
"""

import uuid
from datetime import date, timedelta

import pytest

from db.audit import run_data_audit, EXPIRY_WARN_DAYS
from db.models import Conversation, Task, Lease, Message, Property, Tenant, Unit


# ---------------------------------------------------------------------------
# Shared helpers — mirror test_audit.py helpers so tests are self-contained
# ---------------------------------------------------------------------------

TODAY = date.today()


def _prop(db, address="1 Test St", city="Portland", state="OR", postal_code="97201"):
    p = Property(address_line1=address, city=city, state=state, postal_code=postal_code)
    db.add(p)
    db.flush()
    return p


def _unit(db, prop, label="1A"):
    u = Unit(property_id=prop.id, label=label)
    db.add(u)
    db.flush()
    return u


def _tenant(db, first="Alice", last="Smith", phone=None, email=None):
    t = Tenant(first_name=first, last_name=last, phone=phone, email=email)
    db.add(t)
    db.flush()
    return t


def _lease(db, prop, unit, tenant, start=None, end=None, rent=1000.0, payment_status="current"):
    l = Lease(
        property_id=prop.id,
        unit_id=unit.id,
        tenant_id=tenant.id,
        start_date=start or TODAY - timedelta(days=30),
        end_date=end or TODAY + timedelta(days=365),
        rent_amount=rent,
        payment_status=payment_status,
    )
    db.add(l)
    db.flush()
    return l


def _cfg(**checks):
    """Build a minimal config dict with the given check overrides."""
    return {"checks": checks}


def _tasks(db):
    return (
        db.query(Task)
        .filter(
            Task.source == "ai_suggestion",
        )
        .all()
    )


def _open_tasks(db):
    return [t for t in _tasks(db) if t.task_status in {"suggested", "active", "paused"}]


def _subjects(db):
    return [t.title for t in _open_tasks(db)]


def _context_body(db, task):
    conv_id = task.conversations[0].id if task.conversations else None
    if not conv_id:
        return ""
    msg = (
        db.query(Message)
        .filter(Message.conversation_id == conv_id, Message.message_type == "context")
        .first()
    )
    return msg.body if msg else ""


def _audit(db, check=None, **check_cfgs):
    """Run audit with only the specified check enabled (all others disabled)."""
    all_checks = {
        "vacant_units":          {"enabled": False, "interval_hours": 1, "min_vacancy_days": 0},
        "expiring_leases":       {"enabled": False, "interval_hours": 1, "warn_days": 60},
        "overdue_rent":          {"enabled": False, "interval_hours": 1},
        "incomplete_properties": {"enabled": False, "interval_hours": 1},
        "missing_contact":       {"enabled": False, "interval_hours": 1},
        "expired_leases":        {"enabled": False, "interval_hours": 1},
    }
    if check:
        all_checks[check] = {"enabled": True, "interval_hours": 1, **check_cfgs}
    return run_data_audit(db, config={"checks": all_checks})


# ===========================================================================
# vacant_units
# ===========================================================================

class TestVacantUnits:
    """
    DSL: scope=unit, conditions=[active_lease_count==0, days_vacant>=min_vacancy_days]
    urgency: high>60d | medium>14d | low otherwise
    """

    # ── condition: active_lease_count == 0 ──────────────────────────────────

    def test_unit_with_no_leases_at_all_triggers(self, db):
        p = _prop(db)
        _unit(db, p, "1A")
        n = _audit(db, "vacant_units")
        assert n == 1
        assert any("Vacant unit" in s for s in _subjects(db))

    def test_unit_with_active_lease_suppressed(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-0001")
        _lease(db, p, u, t, end=TODAY + timedelta(days=180))
        n = _audit(db, "vacant_units")
        assert n == 0

    def test_unit_with_lease_ending_today_is_active(self, db):
        """Lease ending today still counts as active (end_date >= today)."""
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-0002")
        _lease(db, p, u, t, end=TODAY)
        n = _audit(db, "vacant_units")
        assert n == 0

    def test_unit_with_lease_ending_yesterday_is_vacant(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-0003")
        _lease(db, p, u, t, end=TODAY - timedelta(days=1))
        n = _audit(db, "vacant_units")
        assert n >= 1
        assert any("Vacant unit" in s for s in _subjects(db))

    def test_multiple_leases_all_expired_counts_as_vacant(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t1 = _tenant(db, first="A", phone="1")
        t2 = _tenant(db, first="B", phone="2")
        _lease(db, p, u, t1, end=TODAY - timedelta(days=400))
        _lease(db, p, u, t2, end=TODAY - timedelta(days=5))
        n = _audit(db, "vacant_units")
        assert any("Vacant unit" in s for s in _subjects(db))

    def test_one_active_one_expired_lease_not_vacant(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t1 = _tenant(db, first="Old", phone="1")
        t2 = _tenant(db, first="New", phone="2")
        _lease(db, p, u, t1, end=TODAY - timedelta(days=100))
        _lease(db, p, u, t2, end=TODAY + timedelta(days=200))
        n = _audit(db, "vacant_units")
        assert n == 0

    # ── condition: days_vacant >= min_vacancy_days ──────────────────────────

    def test_min_vacancy_days_zero_triggers_immediately(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-0010")
        _lease(db, p, u, t, end=TODAY - timedelta(days=1))
        n = _audit(db, "vacant_units", min_vacancy_days=0)
        assert n >= 1

    def test_min_vacancy_days_respected_skip_when_below(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-0011")
        _lease(db, p, u, t, end=TODAY - timedelta(days=5))   # 5 days vacant
        n = _audit(db, "vacant_units", min_vacancy_days=14)  # threshold = 14
        assert n == 0

    def test_min_vacancy_days_triggers_when_equal(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-0012")
        _lease(db, p, u, t, end=TODAY - timedelta(days=14))  # exactly 14 days
        n = _audit(db, "vacant_units", min_vacancy_days=14)
        assert n >= 1

    def test_min_vacancy_days_triggers_when_above(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-0013")
        _lease(db, p, u, t, end=TODAY - timedelta(days=30))
        n = _audit(db, "vacant_units", min_vacancy_days=14)
        assert n >= 1

    def test_min_vacancy_30_skips_unit_vacant_20_days(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-0014")
        _lease(db, p, u, t, end=TODAY - timedelta(days=20))
        n = _audit(db, "vacant_units", min_vacancy_days=30)
        assert n == 0

    def test_unit_never_leased_is_treated_as_vacant(self, db):
        """DSL days_vacant=0 for never-leased units; min_vacancy_days=0 catches it."""
        p = _prop(db)
        _unit(db, p, "Never")
        n = _audit(db, "vacant_units", min_vacancy_days=0)
        assert n >= 1

    def test_never_leased_unit_skipped_when_min_vacancy_positive(self, db):
        """days_vacant computed as 0 for never-leased units → skipped when min > 0."""
        p = _prop(db)
        _unit(db, p, "Never")
        # Python audit treats never-leased as days_vacant=0, so min_vacancy_days=1 skips it
        n = _audit(db, "vacant_units", min_vacancy_days=1)
        assert n == 0

    # ── urgency conditional expression ──────────────────────────────────────

    def test_urgency_high_when_vacant_over_60_days(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-0020")
        _lease(db, p, u, t, end=TODAY - timedelta(days=61))
        _audit(db, "vacant_units")
        task = next(t for t in _open_tasks(db) if "Vacant unit" in t.title)
        assert task.urgency == "high"

    def test_urgency_high_at_exactly_61_days(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-0021")
        _lease(db, p, u, t, end=TODAY - timedelta(days=61))
        _audit(db, "vacant_units")
        task = next(t for t in _open_tasks(db) if "Vacant unit" in t.title)
        assert task.urgency == "high"

    def test_urgency_medium_when_vacant_15_to_60_days(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-0022")
        _lease(db, p, u, t, end=TODAY - timedelta(days=30))  # 30 days vacant
        _audit(db, "vacant_units")
        task = next(t for t in _open_tasks(db) if "Vacant unit" in t.title)
        assert task.urgency == "medium"

    def test_urgency_medium_at_exactly_15_days(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-0023")
        _lease(db, p, u, t, end=TODAY - timedelta(days=15))
        _audit(db, "vacant_units")
        task = next(t for t in _open_tasks(db) if "Vacant unit" in t.title)
        assert task.urgency == "medium"

    def test_urgency_low_when_vacant_14_days_or_fewer(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-0024")
        _lease(db, p, u, t, end=TODAY - timedelta(days=5))
        _audit(db, "vacant_units")
        task = next(t for t in _open_tasks(db) if "Vacant unit" in t.title)
        assert task.urgency == "low"

    def test_urgency_low_never_leased(self, db):
        p = _prop(db)
        _unit(db, p, "Never2")
        _audit(db, "vacant_units", min_vacancy_days=0)
        task = next(t for t in _open_tasks(db) if "Vacant unit" in t.title)
        assert task.urgency == "low"

    # ── task metadata ────────────────────────────────────────────────────────

    def test_task_category_is_leasing(self, db):
        p = _prop(db)
        _unit(db, p)
        _audit(db, "vacant_units")
        task = next(t for t in _open_tasks(db) if "Vacant unit" in t.title)
        assert task.category == "leasing"

    def test_task_links_unit_and_property(self, db):
        p = _prop(db)
        u = _unit(db, p)
        _audit(db, "vacant_units")
        task = next(t for t in _open_tasks(db) if "Vacant unit" in t.title)
        assert task.unit_id == u.id
        assert task.property_id == p.id

    def test_subject_includes_unit_label(self, db):
        p = _prop(db)
        _unit(db, p, label="3C")
        _audit(db, "vacant_units")
        assert any("3C" in s for s in _subjects(db))

    def test_context_body_mentions_previous_tenant(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, first="Carlos", last="Rivera", phone="555-0030")
        _lease(db, p, u, t, end=TODAY - timedelta(days=20))
        _audit(db, "vacant_units")
        task = next(t for t in _open_tasks(db) if "Vacant unit" in t.title)
        body = _context_body(db, task)
        assert "Carlos Rivera" in body or "Carlos" in body

    def test_context_body_mentions_previous_rent(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-0031")
        _lease(db, p, u, t, end=TODAY - timedelta(days=20), rent=1500.0)
        _audit(db, "vacant_units")
        task = next(t for t in _open_tasks(db) if "Vacant unit" in t.title)
        body = _context_body(db, task)
        assert "1,500" in body or "1500" in body

    def test_multiple_vacant_units_produce_separate_tasks(self, db):
        p = _prop(db)
        _unit(db, p, "A1")
        _unit(db, p, "A2")
        _unit(db, p, "A3")
        n = _audit(db, "vacant_units")
        assert n == 3

    def test_tasks_across_different_properties(self, db):
        p1 = _prop(db, address="100 Elm")
        p2 = _prop(db, address="200 Oak")
        _unit(db, p1, "101")
        _unit(db, p2, "201")
        n = _audit(db, "vacant_units")
        assert n == 2
        property_ids = {t.property_id for t in _open_tasks(db) if "Vacant unit" in t.title}
        assert p1.id in property_ids
        assert p2.id in property_ids


# ===========================================================================
# expiring_leases
# ===========================================================================

class TestExpiringLeases:
    """
    DSL: scope=lease, filters=[end_date>=today, end_date<=today+warn_days]
    urgency: high if days_until_end<=30, medium otherwise
    """

    # ── scope filter: end_date in [today, today+warn_days] ──────────────────

    def test_lease_expiring_within_default_warn_window_triggers(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-1001")
        _lease(db, p, u, t, end=TODAY + timedelta(days=EXPIRY_WARN_DAYS - 1))
        n = _audit(db, "expiring_leases", warn_days=EXPIRY_WARN_DAYS)
        assert n == 1
        assert any("Lease expiring" in s for s in _subjects(db))

    def test_lease_ending_today_triggers(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-1002")
        _lease(db, p, u, t, end=TODAY)
        n = _audit(db, "expiring_leases", warn_days=EXPIRY_WARN_DAYS)
        assert n == 1

    def test_lease_outside_warn_window_suppressed(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-1003")
        _lease(db, p, u, t, end=TODAY + timedelta(days=EXPIRY_WARN_DAYS + 1))
        n = _audit(db, "expiring_leases", warn_days=EXPIRY_WARN_DAYS)
        assert n == 0

    def test_already_expired_lease_excluded(self, db):
        """end_date < today → excluded by end_date >= today filter."""
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-1004")
        _lease(db, p, u, t, end=TODAY - timedelta(days=1))
        n = _audit(db, "expiring_leases", warn_days=EXPIRY_WARN_DAYS)
        assert n == 0

    def test_custom_warn_days_30_triggers_within_30(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-1005")
        _lease(db, p, u, t, end=TODAY + timedelta(days=25))
        n = _audit(db, "expiring_leases", warn_days=30)
        assert n == 1

    def test_custom_warn_days_30_suppresses_lease_at_31_days(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-1006")
        _lease(db, p, u, t, end=TODAY + timedelta(days=31))
        n = _audit(db, "expiring_leases", warn_days=30)
        assert n == 0

    def test_warn_days_90_catches_lease_at_89_days(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-1007")
        _lease(db, p, u, t, end=TODAY + timedelta(days=89))
        n = _audit(db, "expiring_leases", warn_days=90)
        assert n == 1

    # ── urgency conditional: high if days_until_end<=30, medium otherwise ───

    def test_urgency_medium_when_31_to_60_days(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-1010")
        _lease(db, p, u, t, end=TODAY + timedelta(days=45))
        _audit(db, "expiring_leases", warn_days=60)
        task = next(t for t in _open_tasks(db) if "Lease expiring" in t.title)
        assert task.urgency == "medium"

    def test_urgency_high_when_exactly_30_days(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-1011")
        _lease(db, p, u, t, end=TODAY + timedelta(days=30))
        _audit(db, "expiring_leases", warn_days=60)
        task = next(t for t in _open_tasks(db) if "Lease expiring" in t.title)
        assert task.urgency == "high"

    def test_urgency_high_when_under_30_days(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-1012")
        _lease(db, p, u, t, end=TODAY + timedelta(days=15))
        _audit(db, "expiring_leases", warn_days=60)
        task = next(t for t in _open_tasks(db) if "Lease expiring" in t.title)
        assert task.urgency == "high"

    def test_urgency_high_when_ending_today(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-1013")
        _lease(db, p, u, t, end=TODAY)
        _audit(db, "expiring_leases", warn_days=60)
        task = next(t for t in _open_tasks(db) if "Lease expiring" in t.title)
        assert task.urgency == "high"

    # ── task metadata ────────────────────────────────────────────────────────

    def test_task_category_is_leasing(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-1020")
        _lease(db, p, u, t, end=TODAY + timedelta(days=20))
        _audit(db, "expiring_leases", warn_days=60)
        task = next(t for t in _open_tasks(db) if "Lease expiring" in t.title)
        assert task.category == "leasing"

    def test_subject_includes_end_date(self, db):
        """Subject must use end_date (not days_left) for stable deduplication."""
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-1021")
        end = TODAY + timedelta(days=40)
        _lease(db, p, u, t, end=end)
        _audit(db, "expiring_leases", warn_days=60)
        assert any(str(end) in s for s in _subjects(db))

    def test_subject_includes_tenant_name(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, first="Priya", last="Patel", phone="555-1022")
        _lease(db, p, u, t, end=TODAY + timedelta(days=20))
        _audit(db, "expiring_leases", warn_days=60)
        assert any("Priya Patel" in s for s in _subjects(db))

    def test_task_links_unit_and_property(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-1023")
        _lease(db, p, u, t, end=TODAY + timedelta(days=20))
        _audit(db, "expiring_leases", warn_days=60)
        task = next(t for t in _open_tasks(db) if "Lease expiring" in t.title)
        assert task.unit_id == u.id
        assert task.property_id == p.id

    def test_multiple_expiring_leases_produce_separate_tasks(self, db):
        p = _prop(db)
        for i in range(3):
            u = _unit(db, p, label=f"U{i}")
            t = _tenant(db, first=f"T{i}", phone=f"555-200{i}")
            _lease(db, p, u, t, end=TODAY + timedelta(days=10 + i))
        n = _audit(db, "expiring_leases", warn_days=60)
        assert n == 3

    def test_context_body_mentions_days_until_end(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-1030")
        _lease(db, p, u, t, end=TODAY + timedelta(days=25))
        _audit(db, "expiring_leases", warn_days=60)
        task = next(t for t in _open_tasks(db) if "Lease expiring" in t.title)
        body = _context_body(db, task)
        assert "25" in body or "days" in body.lower()


# ===========================================================================
# overdue_rent
# ===========================================================================

class TestOverdueRent:
    """
    DSL: scope=lease, filters=[payment_status in [late, overdue]]
    urgency: high (unconditional); category: rent
    """

    def test_status_late_triggers_task(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-2001")
        _lease(db, p, u, t, payment_status="late")
        n = _audit(db, "overdue_rent")
        assert n == 1
        assert any("Overdue rent" in s for s in _subjects(db))

    def test_status_overdue_triggers_task(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-2002")
        _lease(db, p, u, t, payment_status="overdue")
        n = _audit(db, "overdue_rent")
        assert n == 1

    def test_status_current_suppressed(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-2003")
        _lease(db, p, u, t, payment_status="current")
        n = _audit(db, "overdue_rent")
        assert n == 0

    def test_status_pending_suppressed(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-2004")
        _lease(db, p, u, t, payment_status="pending")
        n = _audit(db, "overdue_rent")
        assert n == 0

    def test_status_none_suppressed(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-2005")
        _lease(db, p, u, t, payment_status=None)
        n = _audit(db, "overdue_rent")
        assert n == 0

    def test_urgency_is_always_high(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-2010")
        _lease(db, p, u, t, payment_status="late")
        _audit(db, "overdue_rent")
        task = next(t for t in _open_tasks(db) if "Overdue rent" in t.title)
        assert task.urgency == "high"

    def test_overdue_also_high_urgency(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-2011")
        _lease(db, p, u, t, payment_status="overdue")
        _audit(db, "overdue_rent")
        task = next(t for t in _open_tasks(db) if "Overdue rent" in t.title)
        assert task.urgency == "high"

    def test_category_is_rent(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-2012")
        _lease(db, p, u, t, payment_status="late")
        _audit(db, "overdue_rent")
        task = next(t for t in _open_tasks(db) if "Overdue rent" in t.title)
        assert task.category == "rent"

    def test_subject_includes_tenant_name_and_unit(self, db):
        p = _prop(db)
        u = _unit(db, p, "2B")
        t = _tenant(db, first="Omar", last="Hassan", phone="555-2013")
        _lease(db, p, u, t, payment_status="late")
        _audit(db, "overdue_rent")
        subjects = _subjects(db)
        assert any("Omar Hassan" in s for s in subjects)
        assert any("2B" in s for s in subjects)

    def test_context_body_mentions_payment_status(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-2014")
        _lease(db, p, u, t, payment_status="overdue")
        _audit(db, "overdue_rent")
        task = next(t for t in _open_tasks(db) if "Overdue rent" in t.title)
        body = _context_body(db, task)
        assert "overdue" in body.lower()

    def test_context_body_mentions_rent_amount(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-2015")
        _lease(db, p, u, t, payment_status="late", rent=2200.0)
        _audit(db, "overdue_rent")
        task = next(t for t in _open_tasks(db) if "Overdue rent" in t.title)
        body = _context_body(db, task)
        assert "2,200" in body or "2200" in body

    def test_multiple_overdue_leases_each_get_task(self, db):
        p = _prop(db)
        for i in range(3):
            u = _unit(db, p, label=f"R{i}")
            t = _tenant(db, first=f"R{i}", phone=f"555-300{i}")
            _lease(db, p, u, t, payment_status="late" if i % 2 == 0 else "overdue")
        n = _audit(db, "overdue_rent")
        assert n == 3

    def test_task_links_unit_and_property(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-2020")
        _lease(db, p, u, t, payment_status="late")
        _audit(db, "overdue_rent")
        task = next(t for t in _open_tasks(db) if "Overdue rent" in t.title)
        assert task.unit_id == u.id
        assert task.property_id == p.id


# ===========================================================================
# incomplete_properties
# ===========================================================================

class TestIncompleteProperties:
    """
    DSL: scope=property, conditions=[any_of(city not_exists, state not_exists, postal_code not_exists)]
    urgency: low; category: compliance
    """

    def test_missing_city_triggers(self, db):
        _prop(db, city=None)
        n = _audit(db, "incomplete_properties")
        assert n == 1
        assert any("Incomplete address" in s for s in _subjects(db))

    def test_missing_state_triggers(self, db):
        _prop(db, state=None)
        n = _audit(db, "incomplete_properties")
        assert n == 1

    def test_missing_postal_code_triggers(self, db):
        _prop(db, postal_code=None)
        n = _audit(db, "incomplete_properties")
        assert n == 1

    def test_missing_two_fields_triggers_once(self, db):
        """any_of(missing city OR state) on the same property → single task."""
        _prop(db, city=None, state=None)
        n = _audit(db, "incomplete_properties")
        assert n == 1

    def test_all_fields_missing_triggers_once(self, db):
        _prop(db, city=None, state=None, postal_code=None)
        n = _audit(db, "incomplete_properties")
        assert n == 1

    def test_complete_address_suppressed(self, db):
        _prop(db)
        n = _audit(db, "incomplete_properties")
        assert n == 0

    def test_category_is_compliance(self, db):
        _prop(db, city=None)
        _audit(db, "incomplete_properties")
        task = next(t for t in _open_tasks(db) if "Incomplete address" in t.title)
        assert task.category == "compliance"

    def test_urgency_is_low(self, db):
        _prop(db, city=None)
        _audit(db, "incomplete_properties")
        task = next(t for t in _open_tasks(db) if "Incomplete address" in t.title)
        assert task.urgency == "low"

    def test_task_property_id_set(self, db):
        p = _prop(db, city=None)
        _audit(db, "incomplete_properties")
        task = next(t for t in _open_tasks(db) if "Incomplete address" in t.title)
        assert task.property_id == p.id

    def test_subject_includes_address(self, db):
        _prop(db, address="555 Maple Ave", city=None)
        _audit(db, "incomplete_properties")
        assert any("555 Maple Ave" in s for s in _subjects(db))

    def test_two_incomplete_properties_produce_two_tasks(self, db):
        _prop(db, address="100 A", city=None)
        _prop(db, address="200 B", state=None)
        n = _audit(db, "incomplete_properties")
        assert n == 2

    def test_context_body_mentions_missing_field(self, db):
        _prop(db, city=None)
        _audit(db, "incomplete_properties")
        task = next(t for t in _open_tasks(db) if "Incomplete address" in t.title)
        body = _context_body(db, task)
        assert "city" in body.lower() or "missing" in body.lower()

    def test_only_address_line1_present_triggers(self, db):
        _prop(db, city=None, state=None, postal_code=None)
        n = _audit(db, "incomplete_properties")
        assert n == 1

    def test_empty_string_city_treated_as_missing(self, db):
        _prop(db, city="")
        n = _audit(db, "incomplete_properties")
        assert n == 1


# ===========================================================================
# missing_contact
# ===========================================================================

class TestMissingContact:
    """
    DSL: scope=tenant, conditions=[phone not_exists AND email not_exists]
    urgency: low; category: compliance
    """

    def test_no_phone_no_email_triggers(self, db):
        _tenant(db)
        n = _audit(db, "missing_contact")
        assert n == 1
        assert any("Missing contact info" in s for s in _subjects(db))

    def test_phone_only_suppressed(self, db):
        _tenant(db, phone="555-3001")
        n = _audit(db, "missing_contact")
        assert n == 0

    def test_email_only_suppressed(self, db):
        _tenant(db, email="foo@bar.com")
        n = _audit(db, "missing_contact")
        assert n == 0

    def test_both_contact_fields_suppressed(self, db):
        _tenant(db, phone="555-3002", email="x@y.com")
        n = _audit(db, "missing_contact")
        assert n == 0

    def test_empty_string_phone_treated_as_missing(self, db):
        """Empty string is falsy — same as not_exists in Python."""
        _tenant(db, phone="", email="")
        n = _audit(db, "missing_contact")
        assert n == 1

    def test_category_is_compliance(self, db):
        _tenant(db)
        _audit(db, "missing_contact")
        task = next(t for t in _open_tasks(db) if "Missing contact info" in t.title)
        assert task.category == "compliance"

    def test_urgency_is_low(self, db):
        _tenant(db)
        _audit(db, "missing_contact")
        task = next(t for t in _open_tasks(db) if "Missing contact info" in t.title)
        assert task.urgency == "low"

    def test_subject_includes_tenant_full_name(self, db):
        _tenant(db, first="Lena", last="Müller")
        _audit(db, "missing_contact")
        assert any("Lena Müller" in s or "Lena" in s for s in _subjects(db))

    def test_multiple_tenants_without_contact_each_get_task(self, db):
        _tenant(db, first="A", last="One")
        _tenant(db, first="B", last="Two")
        _tenant(db, first="C", last="Three")
        n = _audit(db, "missing_contact")
        assert n == 3

    def test_mixed_tenants_only_missing_contact_flagged(self, db):
        _tenant(db, first="Good", phone="555-9")
        _tenant(db, first="Bad")
        n = _audit(db, "missing_contact")
        assert n == 1

    def test_context_body_contains_tenant_name(self, db):
        _tenant(db, first="Wei", last="Zhang")
        _audit(db, "missing_contact")
        task = next(t for t in _open_tasks(db) if "Missing contact info" in t.title)
        body = _context_body(db, task)
        assert "Wei" in body and "Zhang" in body

    def test_context_message_is_ai(self, db):
        _tenant(db)
        _audit(db, "missing_contact")
        task = _open_tasks(db)[0]
        conv_id = task.conversations[0].id
        msg = (
            db.query(Message)
            .filter(Message.conversation_id == conv_id)
            .first()
        )
        assert msg.is_ai is True
        assert msg.message_type == "context"


# ===========================================================================
# expired_leases
# ===========================================================================

class TestExpiredLeases:
    """
    DSL: scope=lease, filters=[end_date < today], conditions=[unit.active_lease_count==0]
    urgency: high; category: leasing
    """

    def test_expired_lease_no_replacement_triggers(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-4001")
        _lease(db, p, u, t, end=TODAY - timedelta(days=10))
        n = _audit(db, "expired_leases")
        assert n == 1
        assert any("Expired lease" in s for s in _subjects(db))

    def test_active_lease_excluded_by_filter(self, db):
        """end_date >= today → not in scope for expired_leases check."""
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-4002")
        _lease(db, p, u, t, end=TODAY + timedelta(days=10))
        n = _audit(db, "expired_leases")
        assert n == 0

    def test_lease_ending_today_not_expired(self, db):
        """end_date == today is active, not expired."""
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-4003")
        _lease(db, p, u, t, end=TODAY)
        n = _audit(db, "expired_leases")
        assert n == 0

    def test_expired_replaced_by_newer_active_lease_suppressed(self, db):
        """unit.active_lease_count > 0 → condition fails → no task."""
        p = _prop(db)
        u = _unit(db, p)
        t1 = _tenant(db, first="Old", phone="555-4010")
        t2 = _tenant(db, first="New", phone="555-4011")
        _lease(db, p, u, t1, end=TODAY - timedelta(days=60))
        _lease(db, p, u, t2, end=TODAY + timedelta(days=300))
        n = _audit(db, "expired_leases")
        subjects = _subjects(db)
        assert not any("Expired lease" in s for s in subjects)

    def test_urgency_is_always_high(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-4020")
        _lease(db, p, u, t, end=TODAY - timedelta(days=5))
        _audit(db, "expired_leases")
        task = next(t for t in _open_tasks(db) if "Expired lease" in t.title)
        assert task.urgency == "high"

    def test_category_is_leasing(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-4021")
        _lease(db, p, u, t, end=TODAY - timedelta(days=5))
        _audit(db, "expired_leases")
        task = next(t for t in _open_tasks(db) if "Expired lease" in t.title)
        assert task.category == "leasing"

    def test_subject_includes_tenant_name_and_unit(self, db):
        p = _prop(db)
        u = _unit(db, p, "4D")
        t = _tenant(db, first="Aisha", last="Okonkwo", phone="555-4022")
        _lease(db, p, u, t, end=TODAY - timedelta(days=5))
        _audit(db, "expired_leases")
        subjects = _subjects(db)
        assert any("Aisha Okonkwo" in s for s in subjects)
        assert any("4D" in s for s in subjects)

    def test_task_links_unit_and_property(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-4023")
        _lease(db, p, u, t, end=TODAY - timedelta(days=5))
        _audit(db, "expired_leases")
        task = next(t for t in _open_tasks(db) if "Expired lease" in t.title)
        assert task.unit_id == u.id
        assert task.property_id == p.id

    def test_context_body_mentions_expiry_date(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-4024")
        end = TODAY - timedelta(days=8)
        _lease(db, p, u, t, end=end)
        _audit(db, "expired_leases")
        task = next(t for t in _open_tasks(db) if "Expired lease" in t.title)
        body = _context_body(db, task)
        assert str(end) in body

    def test_multiple_expired_unreplaced_leases_get_tasks(self, db):
        p = _prop(db)
        for i in range(4):
            u = _unit(db, p, label=f"E{i}")
            t = _tenant(db, first=f"E{i}", phone=f"555-500{i}")
            _lease(db, p, u, t, end=TODAY - timedelta(days=i + 1))
        n = _audit(db, "expired_leases")
        assert n == 4

    def test_replacement_on_different_unit_still_flags_original(self, db):
        """Replacement must be on the SAME unit to suppress the task."""
        p = _prop(db)
        u1 = _unit(db, p, "U1")
        u2 = _unit(db, p, "U2")
        t_old = _tenant(db, first="Old", phone="1")
        t_new = _tenant(db, first="New", phone="2")
        _lease(db, p, u1, t_old, end=TODAY - timedelta(days=10))
        _lease(db, p, u2, t_new, end=TODAY + timedelta(days=300))  # different unit
        n = _audit(db, "expired_leases")
        subjects = _subjects(db)
        assert any("Expired lease" in s for s in subjects)


# ===========================================================================
# Deduplication (cross-check)
# ===========================================================================

class TestDeduplication:
    """
    DSL spec: deduplication by (source, subject, property_id, unit_id) across open statuses.
    """

    def test_same_check_twice_no_duplicate(self, db):
        _prop(db, city=None)
        _audit(db, "incomplete_properties")
        _audit(db, "incomplete_properties")
        tasks = [t for t in _open_tasks(db) if "Incomplete address" in t.title]
        assert len(tasks) == 1

    def test_suggested_status_blocks_dedup(self, db):
        _prop(db, city=None)
        _audit(db, "incomplete_properties")
        task = next(t for t in _open_tasks(db) if "Incomplete address" in t.title)
        assert task.task_status == "suggested"
        _audit(db, "incomplete_properties")
        count = len([t for t in _open_tasks(db) if "Incomplete address" in t.title])
        assert count == 1

    def test_resolved_task_is_recreated(self, db):
        _prop(db, city=None)
        _audit(db, "incomplete_properties")
        task = next(t for t in _open_tasks(db) if "Incomplete address" in t.title)
        task.task_status = "resolved"
        db.flush()
        _audit(db, "incomplete_properties")
        open_count = len([
            t for t in _tasks(db)
            if "Incomplete address" in t.title and t.task_status in {"suggested", "active", "paused"}
        ])
        assert open_count == 1

    def test_cancelled_task_is_recreated(self, db):
        _prop(db, city=None)
        _audit(db, "incomplete_properties")
        task = next(t for t in _open_tasks(db) if "Incomplete address" in t.title)
        task.task_status = "cancelled"
        db.flush()
        _audit(db, "incomplete_properties")
        open_count = len([
            t for t in _tasks(db)
            if "Incomplete address" in t.title and t.task_status in {"suggested", "active", "paused"}
        ])
        assert open_count == 1

    def test_active_task_blocks_new_creation(self, db):
        _prop(db, city=None)
        _audit(db, "incomplete_properties")
        task = next(t for t in _open_tasks(db) if "Incomplete address" in t.title)
        task.task_status = "active"
        db.flush()
        n = _audit(db, "incomplete_properties")
        assert n == 0

    def test_vacant_unit_dedup_keyed_on_unit_id(self, db):
        p = _prop(db)
        u1 = _unit(db, p, "V1")
        u2 = _unit(db, p, "V2")
        _audit(db, "vacant_units")   # creates 2 tasks
        _audit(db, "vacant_units")   # should create 0 more
        tasks = [t for t in _open_tasks(db) if "Vacant unit" in t.title]
        assert len(tasks) == 2

    def test_overdue_rent_dedup_per_lease(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-5001")
        _lease(db, p, u, t, payment_status="late")
        _audit(db, "overdue_rent")
        _audit(db, "overdue_rent")
        tasks = [t for t in _open_tasks(db) if "Overdue rent" in t.title]
        assert len(tasks) == 1

    def test_two_properties_same_issue_get_separate_tasks(self, db):
        _prop(db, address="100 A St", city=None)
        _prop(db, address="200 B St", city=None)
        n = _audit(db, "incomplete_properties")
        assert n == 2


# ===========================================================================
# Config / check_name filtering (DSL: enabled/disabled checks)
# ===========================================================================

class TestCheckFiltering:
    """
    The config dict maps to DSL: trigger.type=schedule + enabled flag per check.
    check_name parameter runs a single check regardless of other config.
    """

    def test_disabled_check_produces_no_tasks(self, db):
        _prop(db, city=None)
        cfg = {"checks": {"incomplete_properties": {"enabled": False, "interval_hours": 1}}}
        n = run_data_audit(db, config=cfg)
        tasks = [t for t in _open_tasks(db) if "Incomplete address" in t.title]
        assert len(tasks) == 0

    def test_check_name_filter_runs_only_that_check(self, db):
        """check_name='overdue_rent' must not run incomplete_properties."""
        _prop(db, city=None)
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-6001")
        _lease(db, p, u, t, payment_status="late")
        n = run_data_audit(db, check_name="overdue_rent")
        subjects = _subjects(db)
        assert any("Overdue rent" in s for s in subjects)
        assert not any("Incomplete address" in s for s in subjects)

    def test_check_name_vacant_units_isolates(self, db):
        p = _prop(db, city=None)  # would trigger incomplete_properties
        _unit(db, p)              # would trigger vacant_units
        _tenant(db)               # would trigger missing_contact
        n = run_data_audit(db, check_name="vacant_units")
        subjects = _subjects(db)
        assert any("Vacant unit" in s for s in subjects)
        assert not any("Incomplete address" in s for s in subjects)
        assert not any("Missing contact" in s for s in subjects)

    def test_all_checks_disabled_produces_no_tasks(self, db):
        p = _prop(db, city=None)
        u = _unit(db, p)
        t = _tenant(db)
        _lease(db, p, u, t, payment_status="late")
        all_off = {
            "checks": {k: {"enabled": False} for k in [
                "incomplete_properties", "vacant_units", "missing_contact",
                "expiring_leases", "expired_leases", "overdue_rent",
            ]}
        }
        n = run_data_audit(db, config=all_off)
        assert n == 0

    def test_empty_config_defaults_to_all_enabled(self, db):
        _prop(db, city=None)
        n = run_data_audit(db, config={})
        assert n >= 1

    def test_none_config_defaults_to_all_enabled(self, db):
        _prop(db, city=None)
        n = run_data_audit(db, config=None)
        assert n >= 1

    def test_min_vacancy_days_param_respected_via_config(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-6010")
        _lease(db, p, u, t, end=TODAY - timedelta(days=5))  # 5 days vacant
        cfg = {
            "checks": {
                "vacant_units": {"enabled": True, "interval_hours": 1, "min_vacancy_days": 30},
                **{k: {"enabled": False} for k in [
                    "expiring_leases", "overdue_rent", "incomplete_properties",
                    "missing_contact", "expired_leases",
                ]},
            }
        }
        n = run_data_audit(db, config=cfg)
        assert n == 0

    def test_warn_days_param_respected_via_config(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-6011")
        _lease(db, p, u, t, end=TODAY + timedelta(days=45))  # 45 days away
        cfg = {
            "checks": {
                "expiring_leases": {"enabled": True, "interval_hours": 1, "warn_days": 30},
                **{k: {"enabled": False} for k in [
                    "vacant_units", "overdue_rent", "incomplete_properties",
                    "missing_contact", "expired_leases",
                ]},
            }
        }
        n = run_data_audit(db, config=cfg)
        assert n == 0  # 45 > warn_days=30 → not in window


# ===========================================================================
# Return value and empty DB
# ===========================================================================

class TestReturnValue:

    def test_empty_db_returns_zero(self, db):
        assert run_data_audit(db) == 0

    def test_returns_exact_count(self, db):
        _prop(db, city=None)      # 1
        _prop(db, state=None)     # 1
        _tenant(db)               # 1
        n = run_data_audit(db)
        assert n == 3

    def test_second_run_returns_zero_when_tasks_exist(self, db):
        _prop(db, city=None)
        run_data_audit(db)
        n2 = run_data_audit(db)
        assert n2 == 0

    def test_only_enabled_checks_counted(self, db):
        _prop(db, city=None)
        cfg = {
            "checks": {
                "incomplete_properties": {"enabled": True},
                "vacant_units": {"enabled": False},
                "missing_contact": {"enabled": False},
                "expiring_leases": {"enabled": False},
                "expired_leases": {"enabled": False},
                "overdue_rent": {"enabled": False},
            }
        }
        _tenant(db)           # would create missing_contact if enabled
        n = run_data_audit(db, config=cfg)
        assert n == 1         # only the incomplete_property task


# ===========================================================================
# Context messages (DSL: actions.create_task.body template)
# ===========================================================================

class TestContextMessages:
    """Every task must have exactly one context message with is_ai=True."""

    def test_incomplete_property_has_context_message(self, db):
        _prop(db, city=None)
        _audit(db, "incomplete_properties")
        task = next(t for t in _open_tasks(db) if "Incomplete address" in t.title)
        conv_id = task.conversations[0].id
        msgs = db.query(Message).filter(Message.conversation_id == conv_id).all()
        assert len(msgs) == 1
        assert msgs[0].message_type == "context"
        assert msgs[0].is_ai is True

    def test_vacant_unit_has_context_message(self, db):
        p = _prop(db)
        _unit(db, p)
        _audit(db, "vacant_units")
        task = next(t for t in _open_tasks(db) if "Vacant unit" in t.title)
        conv_id = task.conversations[0].id
        msgs = db.query(Message).filter(Message.conversation_id == conv_id).all()
        assert len(msgs) == 1
        assert msgs[0].message_type == "context"

    def test_overdue_rent_has_context_message(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-7001")
        _lease(db, p, u, t, payment_status="late")
        _audit(db, "overdue_rent")
        task = next(t for t in _open_tasks(db) if "Overdue rent" in t.title)
        conv_id = task.conversations[0].id
        msgs = db.query(Message).filter(Message.conversation_id == conv_id).all()
        assert len(msgs) == 1
        assert msgs[0].message_type == "context"

    def test_expiring_lease_has_context_message(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-7002")
        _lease(db, p, u, t, end=TODAY + timedelta(days=20))
        _audit(db, "expiring_leases", warn_days=60)
        task = next(t for t in _open_tasks(db) if "Lease expiring" in t.title)
        conv_id = task.conversations[0].id
        msgs = db.query(Message).filter(Message.conversation_id == conv_id).all()
        assert len(msgs) == 1
        assert msgs[0].message_type == "context"

    def test_expired_lease_has_context_message(self, db):
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="555-7003")
        _lease(db, p, u, t, end=TODAY - timedelta(days=5))
        _audit(db, "expired_leases")
        task = next(t for t in _open_tasks(db) if "Expired lease" in t.title)
        conv_id = task.conversations[0].id
        msgs = db.query(Message).filter(Message.conversation_id == conv_id).all()
        assert len(msgs) == 1

    def test_missing_contact_has_context_message(self, db):
        _tenant(db, first="Lone", last="Wolf")
        _audit(db, "missing_contact")
        task = next(t for t in _open_tasks(db) if "Missing contact info" in t.title)
        conv_id = task.conversations[0].id
        msgs = db.query(Message).filter(Message.conversation_id == conv_id).all()
        assert len(msgs) == 1
        assert msgs[0].sender_name == "RentMate"


# ===========================================================================
# Task shape (DSL: is_task, task_status, source, task_mode)
# ===========================================================================

class TestTaskShape:
    """Verify all generated tasks conform to the expected shape."""

    def _any_task(self, db):
        _prop(db, city=None)
        _audit(db, "incomplete_properties")
        return _open_tasks(db)[0]

    def test_is_task_model(self, db):
        task = self._any_task(db)
        assert isinstance(task, Task)

    def test_source_is_ai_suggestion(self, db):
        task = self._any_task(db)
        assert task.source == "ai_suggestion"

    def test_initial_status_is_suggested(self, db):
        task = self._any_task(db)
        assert task.task_status == "suggested"

    def test_task_mode_is_waiting_approval(self, db):
        task = self._any_task(db)
        assert task.task_mode == "waiting_approval"

    def test_task_has_created_at(self, db):
        task = self._any_task(db)
        assert task.created_at is not None
