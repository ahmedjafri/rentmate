# tests/test_dsl_runner.py
"""
Tests for the Property-Flow DSL interpreter (db/dsl_runner.py).

Covers:
- All four scope resources (property, unit, lease, tenant)
- Scope filters: standard field operators, shorthand {exists:}, {not_exists:}
- Per-record conditions including any_of (OR) and multi-condition AND
- All operators: equals, not_equals, gt, lt, gte, lte, in, exists, not_exists, contains
- Urgency conditional expressions
- Template rendering: {{field}}, {{resource.field}}, {{params.x}}, {{today}}
- Deduplication (open task blocks re-creation; resolved/cancelled allows it)
- Params dict passed through to conditions and templates
- run_data_audit integration for custom automations
- Error handling: missing resource, bad YAML, missing script
"""

import textwrap
from datetime import date, timedelta

import pytest

from db.audit import run_data_audit
from db.dsl_runner import run_script
from db.models import Conversation, Task, Lease, Message, Property, Tenant, Unit

TODAY = date.today()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _prop(db, address="1 Main St", city="Portland", state="OR", postal_code="97201"):
    p = Property(address_line1=address, city=city, state=state, postal_code=postal_code)
    db.add(p); db.flush(); return p

def _unit(db, prop, label="1A"):
    u = Unit(property_id=prop.id, label=label)
    db.add(u); db.flush(); return u

def _tenant(db, first="Alice", last="Smith", phone=None, email=None):
    t = Tenant(first_name=first, last_name=last, phone=phone, email=email)
    db.add(t); db.flush(); return t

def _lease(db, prop, unit, tenant, start=None, end=None, rent=1000.0, status="current"):
    l = Lease(
        property_id=prop.id, unit_id=unit.id, tenant_id=tenant.id,
        start_date=start or TODAY - timedelta(days=30),
        end_date=end or TODAY + timedelta(days=365),
        rent_amount=rent, payment_status=status,
    )
    db.add(l); db.flush(); return l

def _tasks(db):
    return (db.query(Task)
              .filter(Task.source == "ai_suggestion")
              .all())

def _open_tasks(db):
    return [t for t in _tasks(db) if t.task_status in {"suggested", "active", "paused"}]

def _subjects(db):
    return [t.title for t in _open_tasks(db)]

def _body(db, task):
    conv_id = task.ai_conversation_id
    if not conv_id:
        return ""
    m = (db.query(Message)
           .filter(Message.conversation_id == conv_id,
                   Message.message_type == "context")
           .first())
    return m.body if m else ""

def _script(yaml_text):
    return textwrap.dedent(yaml_text).strip()


# ===========================================================================
# Scope: property
# ===========================================================================

class TestScopeProperty:

    SIMPLE = _script("""
        scope:
          resource: property
        actions:
          - type: create_task
            subject: "Review: {{address_line1}}"
            category: compliance
            urgency: low
            body: "Property {{address_line1}} needs review."
    """)

    def test_creates_task_for_each_property(self, db):
        _prop(db, address="100 Elm")
        _prop(db, address="200 Oak")
        n = run_script(db, self.SIMPLE)
        assert n == 2

    def test_subject_renders_address_field(self, db):
        _prop(db, address="555 Maple Ave")
        run_script(db, self.SIMPLE)
        assert any("555 Maple Ave" in s for s in _subjects(db))

    def test_body_renders_template(self, db):
        _prop(db, address="42 Pine")
        run_script(db, self.SIMPLE)
        task = _open_tasks(db)[0]
        assert "42 Pine" in _body(db, task)

    def test_category_and_urgency_set(self, db):
        _prop(db)
        run_script(db, self.SIMPLE)
        task = _open_tasks(db)[0]
        assert task.category == "compliance"
        assert task.urgency == "low"

    def test_property_id_set_on_task(self, db):
        p = _prop(db)
        run_script(db, self.SIMPLE)
        task = _open_tasks(db)[0]
        assert task.property_id == p.id
        assert task.unit_id is None

    def test_empty_db_returns_zero(self, db):
        assert run_script(db, self.SIMPLE) == 0

    def test_scope_filter_exists_relation(self, db):
        """Filter {exists: units} — only properties that have at least one unit."""
        script = _script("""
            scope:
              resource: property
              filters:
                - exists: units
            actions:
              - type: create_task
                subject: "Has units: {{address_line1}}"
                category: compliance
                urgency: low
                body: ""
        """)
        p1 = _prop(db, address="With Unit")
        p2 = _prop(db, address="No Unit")
        _unit(db, p1)
        n = run_script(db, script)
        assert n == 1
        assert any("With Unit" in s for s in _subjects(db))
        assert not any("No Unit" in s for s in _subjects(db))

    def test_scope_filter_not_exists_relation(self, db):
        """Filter {not_exists: units} — only properties with no units."""
        script = _script("""
            scope:
              resource: property
              filters:
                - not_exists: units
            actions:
              - type: create_task
                subject: "Empty: {{address_line1}}"
                category: compliance
                urgency: low
                body: ""
        """)
        p1 = _prop(db, address="With Unit")
        p2 = _prop(db, address="No Unit")
        _unit(db, p1)
        n = run_script(db, script)
        assert n == 1
        assert any("No Unit" in s for s in _subjects(db))

    def test_condition_field_not_exists(self, db):
        """conditions: city not_exists → only flagged when city is missing."""
        script = _script("""
            scope:
              resource: property
            conditions:
              - field: city
                operator: not_exists
            actions:
              - type: create_task
                subject: "No city: {{address_line1}}"
                category: compliance
                urgency: low
                body: ""
        """)
        _prop(db, address="Complete")        # has city
        _prop(db, address="Incomplete", city=None)
        n = run_script(db, script)
        assert n == 1
        assert any("Incomplete" in s for s in _subjects(db))

    def test_any_of_condition(self, db):
        """any_of: city not_exists OR state not_exists."""
        script = _script("""
            scope:
              resource: property
            conditions:
              - any_of:
                - field: city
                  operator: not_exists
                - field: state
                  operator: not_exists
            actions:
              - type: create_task
                subject: "Missing: {{address_line1}}"
                category: compliance
                urgency: low
                body: ""
        """)
        _prop(db, address="Good")
        _prop(db, address="No City", city=None)
        _prop(db, address="No State", state=None)
        n = run_script(db, script)
        assert n == 2

    def test_multiple_conditions_and_logic(self, db):
        """Two conditions both required (AND)."""
        script = _script("""
            scope:
              resource: property
            conditions:
              - field: city
                operator: not_exists
              - field: state
                operator: not_exists
            actions:
              - type: create_task
                subject: "Both missing: {{address_line1}}"
                category: compliance
                urgency: low
                body: ""
        """)
        _prop(db, address="Only City Missing", city=None)   # state present → skip
        _prop(db, address="Both Missing", city=None, state=None)
        n = run_script(db, script)
        assert n == 1
        assert any("Both Missing" in s for s in _subjects(db))

    def test_unit_count_computed_field(self, db):
        script = _script("""
            scope:
              resource: property
            conditions:
              - field: unit_count
                operator: gt
                value: 1
            actions:
              - type: create_task
                subject: "Many units: {{address_line1}}"
                category: compliance
                urgency: low
                body: ""
        """)
        p1 = _prop(db, address="One Unit")
        p2 = _prop(db, address="Two Units")
        _unit(db, p1, "A")
        _unit(db, p2, "A")
        _unit(db, p2, "B")
        n = run_script(db, script)
        assert n == 1
        assert any("Two Units" in s for s in _subjects(db))


# ===========================================================================
# Scope: unit
# ===========================================================================

class TestScopeUnit:

    def test_vacant_unit_script(self, db):
        script = _script("""
            scope:
              resource: unit
            conditions:
              - field: active_lease_count
                operator: equals
                value: 0
            actions:
              - type: create_task
                subject: "Vacant: {{unit.label}}"
                category: leasing
                urgency: medium
                body: "Unit {{unit.label}} is vacant."
        """)
        p = _prop(db)
        _unit(db, p, "2A")       # no lease → vacant
        u2 = _unit(db, p, "2B")
        t = _tenant(db, phone="555-1")
        _lease(db, p, u2, t)     # active lease
        n = run_script(db, script)
        assert n == 1
        assert any("2A" in s for s in _subjects(db))
        assert not any("2B" in s for s in _subjects(db))

    def test_days_vacant_condition(self, db):
        script = _script("""
            scope:
              resource: unit
            conditions:
              - field: active_lease_count
                operator: equals
                value: 0
              - field: days_vacant
                operator: gte
                value: 30
            actions:
              - type: create_task
                subject: "Long vacant: {{unit.label}}"
                category: leasing
                urgency: medium
                body: ""
        """)
        p = _prop(db)
        u1 = _unit(db, p, "Short")
        u2 = _unit(db, p, "Long")
        t1 = _tenant(db, first="A", phone="1")
        t2 = _tenant(db, first="B", phone="2")
        _lease(db, p, u1, t1, end=TODAY - timedelta(days=5))   # 5 days vacant → skip
        _lease(db, p, u2, t2, end=TODAY - timedelta(days=40))  # 40 days → trigger
        n = run_script(db, script)
        assert n == 1
        assert any("Long" in s for s in _subjects(db))

    def test_unit_task_links_to_unit_and_property(self, db):
        script = _script("""
            scope:
              resource: unit
            conditions:
              - field: active_lease_count
                operator: equals
                value: 0
            actions:
              - type: create_task
                subject: "Vacant: {{label}}"
                category: leasing
                urgency: low
                body: ""
        """)
        p = _prop(db)
        u = _unit(db, p)
        run_script(db, script)
        task = _open_tasks(db)[0]
        assert task.unit_id == u.id
        assert task.property_id == p.id

    def test_urgency_conditional_days_vacant(self, db):
        script = _script("""
            scope:
              resource: unit
            conditions:
              - field: active_lease_count
                operator: equals
                value: 0
            actions:
              - type: create_task
                subject: "Vacant: {{label}}"
                category: leasing
                urgency: >
                  high if days_vacant > 60
                  medium if days_vacant > 14
                  low otherwise
                body: ""
        """)
        p = _prop(db)
        u_high = _unit(db, p, "H")
        u_med  = _unit(db, p, "M")
        u_low  = _unit(db, p, "L")
        for u, days in [(u_high, 70), (u_med, 20), (u_low, 5)]:
            t = _tenant(db, first=u.label, phone=u.label)
            _lease(db, p, u, t, end=TODAY - timedelta(days=days))

        run_script(db, script)
        tasks_by_label = {t.title.split(": ")[1]: t.urgency for t in _open_tasks(db)}
        assert tasks_by_label["H"] == "high"
        assert tasks_by_label["M"] == "medium"
        assert tasks_by_label["L"] == "low"

    def test_params_in_condition(self, db):
        script = _script("""
            scope:
              resource: unit
            conditions:
              - field: active_lease_count
                operator: equals
                value: 0
              - field: days_vacant
                operator: gte
                value: "{{params.min_vacancy_days}}"
            actions:
              - type: create_task
                subject: "Param vacant: {{label}}"
                category: leasing
                urgency: low
                body: ""
        """)
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="x")
        _lease(db, p, u, t, end=TODAY - timedelta(days=10))  # 10 days vacant

        # threshold=14 → skip
        n = run_script(db, script, params={"min_vacancy_days": 14})
        assert n == 0

        # threshold=5 → trigger
        n = run_script(db, script, params={"min_vacancy_days": 5})
        assert n == 1

    def test_template_uses_property_dot_notation(self, db):
        script = _script("""
            scope:
              resource: unit
            conditions:
              - field: active_lease_count
                operator: equals
                value: 0
            actions:
              - type: create_task
                subject: "Vacant {{unit.label}} at {{unit.property.address_line1}}"
                category: leasing
                urgency: low
                body: ""
        """)
        p = _prop(db, address="99 Spruce")
        _unit(db, p, "3C")
        run_script(db, script)
        assert any("3C" in s and "99 Spruce" in s for s in _subjects(db))


# ===========================================================================
# Scope: lease
# ===========================================================================

class TestScopeLease:

    def test_overdue_rent_script(self, db):
        script = _script("""
            scope:
              resource: lease
              filters:
                - field: payment_status
                  operator: in
                  value: [late, overdue]
            actions:
              - type: create_task
                subject: "Overdue: {{lease.tenant.first_name}} {{lease.tenant.last_name}}"
                category: rent
                urgency: high
                body: "Status: {{payment_status}}"
        """)
        p = _prop(db)
        u = _unit(db, p)
        t_late = _tenant(db, first="Late", last="Payer", phone="1")
        t_ok   = _tenant(db, first="Good", last="Payer", phone="2")
        _lease(db, p, u, t_late, status="late")
        u2 = _unit(db, p, "2A")
        _lease(db, p, u2, t_ok, status="current")
        n = run_script(db, script)
        assert n == 1
        assert any("Late Payer" in s for s in _subjects(db))
        assert not any("Good Payer" in s for s in _subjects(db))

    def test_expiring_lease_with_warn_days_param(self, db):
        script = _script("""
            scope:
              resource: lease
              filters:
                - field: end_date
                  operator: gte
                  value: "{{today}}"
                - field: end_date
                  operator: lte
                  value: "{{today + params.warn_days}}"
            actions:
              - type: create_task
                subject: "Expiring: {{lease.tenant.first_name}} {{days_until_end}}d"
                category: leasing
                urgency: medium
                body: ""
        """)
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, first="Soon", phone="x")
        _lease(db, p, u, t, end=TODAY + timedelta(days=20))

        # warn_days=30 → catches 20-day lease
        n = run_script(db, script, params={"warn_days": 30})
        assert n == 1
        assert any("Soon" in s and "20d" in s for s in _subjects(db))

    def test_expiring_lease_outside_window_skipped(self, db):
        script = _script("""
            scope:
              resource: lease
              filters:
                - field: end_date
                  operator: gte
                  value: "{{today}}"
                - field: end_date
                  operator: lte
                  value: "{{today + params.warn_days}}"
            actions:
              - type: create_task
                subject: "Expiring: {{days_until_end}}d"
                category: leasing
                urgency: medium
                body: ""
        """)
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="x")
        _lease(db, p, u, t, end=TODAY + timedelta(days=40))
        n = run_script(db, script, params={"warn_days": 30})
        assert n == 0

    def test_days_until_end_in_urgency(self, db):
        script = _script("""
            scope:
              resource: lease
              filters:
                - field: end_date
                  operator: gte
                  value: "{{today}}"
            actions:
              - type: create_task
                subject: "Expiring: {{days_until_end}}d"
                category: leasing
                urgency: >
                  high if days_until_end <= 30
                  medium otherwise
                body: ""
        """)
        p = _prop(db)
        for label, days in [("soon", 20), ("far", 50)]:
            u = _unit(db, p, label)
            t = _tenant(db, first=label, phone=label)
            _lease(db, p, u, t, end=TODAY + timedelta(days=days))

        run_script(db, script)
        tasks = {t.title: t.urgency for t in _open_tasks(db)}
        assert tasks["Expiring: 20d"] == "high"
        assert tasks["Expiring: 50d"] == "medium"

    def test_lease_task_links_unit_and_property(self, db):
        script = _script("""
            scope:
              resource: lease
              filters:
                - field: payment_status
                  operator: equals
                  value: overdue
            actions:
              - type: create_task
                subject: "Overdue lease"
                category: rent
                urgency: high
                body: ""
        """)
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="x")
        _lease(db, p, u, t, status="overdue")
        run_script(db, script)
        task = _open_tasks(db)[0]
        assert task.unit_id == u.id
        assert task.property_id == p.id

    def test_tenant_dot_notation_in_body(self, db):
        script = _script("""
            scope:
              resource: lease
              filters:
                - field: payment_status
                  operator: equals
                  value: late
            actions:
              - type: create_task
                subject: "Late payment"
                category: rent
                urgency: high
                body: "Tenant: {{lease.tenant.first_name}} {{lease.tenant.last_name}}"
        """)
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, first="Omar", last="Hassan", phone="x")
        _lease(db, p, u, t, status="late")
        run_script(db, script)
        task = _open_tasks(db)[0]
        assert "Omar Hassan" in _body(db, task)


# ===========================================================================
# Scope: tenant
# ===========================================================================

class TestScopeTenant:

    def test_missing_contact_script(self, db):
        script = _script("""
            scope:
              resource: tenant
            conditions:
              - field: phone
                operator: not_exists
              - field: email
                operator: not_exists
            actions:
              - type: create_task
                subject: "No contact: {{tenant.first_name}} {{tenant.last_name}}"
                category: compliance
                urgency: low
                body: "Add contact info for {{first_name}}."
        """)
        _tenant(db, first="Bad",  last="A")          # no contact → flag
        _tenant(db, first="Good", last="B", phone="1") # has phone → skip
        n = run_script(db, script)
        assert n == 1
        assert any("Bad A" in s for s in _subjects(db))

    def test_tenant_body_uses_top_level_field(self, db):
        """{{first_name}} (not {{tenant.first_name}}) should also work via top-level ctx."""
        script = _script("""
            scope:
              resource: tenant
            conditions:
              - field: phone
                operator: not_exists
              - field: email
                operator: not_exists
            actions:
              - type: create_task
                subject: "Contact missing"
                category: compliance
                urgency: low
                body: "Missing: {{first_name}} {{last_name}}"
        """)
        _tenant(db, first="Wei", last="Zhang")
        run_script(db, script)
        task = _open_tasks(db)[0]
        assert "Wei Zhang" in _body(db, task)

    def test_tenant_email_exists_condition(self, db):
        script = _script("""
            scope:
              resource: tenant
            conditions:
              - field: email
                operator: exists
            actions:
              - type: create_task
                subject: "Has email: {{first_name}}"
                category: compliance
                urgency: low
                body: ""
        """)
        _tenant(db, first="NoEmail")
        _tenant(db, first="HasEmail", email="e@x.com")
        n = run_script(db, script)
        assert n == 1
        assert any("HasEmail" in s for s in _subjects(db))


# ===========================================================================
# All operators
# ===========================================================================

class TestOperators:

    def _script_for_op(self, field, operator, value=""):
        val_line = f"\n                value: {value}" if value else ""
        return _script(f"""
            scope:
              resource: property
            conditions:
              - field: {field}
                operator: {operator}{val_line}
            actions:
              - type: create_task
                subject: "Match: {{{{address_line1}}}}"
                category: compliance
                urgency: low
                body: ""
        """)

    def test_equals(self, db):
        _prop(db, address="A", city="Portland")
        _prop(db, address="B", city="Seattle")
        n = run_script(db, self._script_for_op("city", "equals", "Portland"))
        assert n == 1
        assert any("A" in s for s in _subjects(db))

    def test_not_equals(self, db):
        _prop(db, address="A", city="Portland")
        _prop(db, address="B", city="Seattle")
        n = run_script(db, self._script_for_op("city", "not_equals", "Portland"))
        assert n == 1
        assert any("B" in s for s in _subjects(db))

    def test_exists_with_value(self, db):
        _prop(db, address="HasCity", city="X")
        _prop(db, address="NoCity", city=None)
        n = run_script(db, self._script_for_op("city", "exists"))
        assert n == 1
        assert any("HasCity" in s for s in _subjects(db))

    def test_not_exists_with_null(self, db):
        _prop(db, address="HasCity", city="X")
        _prop(db, address="NoCity", city=None)
        n = run_script(db, self._script_for_op("city", "not_exists"))
        assert n == 1
        assert any("NoCity" in s for s in _subjects(db))

    def test_in_operator(self, db):
        script = _script("""
            scope:
              resource: lease
              filters:
                - field: payment_status
                  operator: in
                  value: [late, overdue]
            actions:
              - type: create_task
                subject: "In: {{payment_status}}"
                category: rent
                urgency: high
                body: ""
        """)
        p = _prop(db)
        for label, status in [("A", "late"), ("B", "overdue"), ("C", "current")]:
            u = _unit(db, p, label)
            t = _tenant(db, first=label, phone=label)
            _lease(db, p, u, t, status=status)
        n = run_script(db, script)
        assert n == 2

    def test_gt_operator(self, db):
        script = _script("""
            scope:
              resource: unit
            conditions:
              - field: active_lease_count
                operator: gt
                value: 0
            actions:
              - type: create_task
                subject: "Active: {{label}}"
                category: compliance
                urgency: low
                body: ""
        """)
        p = _prop(db)
        u_with = _unit(db, p, "With")
        _unit(db, p, "Without")
        t = _tenant(db, phone="x")
        _lease(db, p, u_with, t)
        n = run_script(db, script)
        assert n == 1
        assert any("With" in s for s in _subjects(db))

    def test_lte_operator_on_date(self, db):
        script = _script("""
            scope:
              resource: lease
              filters:
                - field: end_date
                  operator: lte
                  value: "{{today}}"
            actions:
              - type: create_task
                subject: "Expired or today"
                category: leasing
                urgency: high
                body: ""
        """)
        p = _prop(db)
        u1 = _unit(db, p, "Past")
        u2 = _unit(db, p, "Future")
        t1 = _tenant(db, first="P", phone="1")
        t2 = _tenant(db, first="F", phone="2")
        _lease(db, p, u1, t1, end=TODAY - timedelta(days=1))
        _lease(db, p, u2, t2, end=TODAY + timedelta(days=10))
        n = run_script(db, script)
        assert n == 1

    def test_contains_operator_string(self, db):
        script = _script("""
            scope:
              resource: property
            conditions:
              - field: address_line1
                operator: contains
                value: "Maple"
            actions:
              - type: create_task
                subject: "Maple: {{address_line1}}"
                category: compliance
                urgency: low
                body: ""
        """)
        _prop(db, address="100 Maple Ave")
        _prop(db, address="200 Oak St")
        n = run_script(db, script)
        assert n == 1
        assert any("Maple" in s for s in _subjects(db))


# ===========================================================================
# Template rendering
# ===========================================================================

class TestTemplateRendering:

    def test_today_in_template(self, db):
        script = _script("""
            scope:
              resource: property
            actions:
              - type: create_task
                subject: "Review on {{today}}"
                category: compliance
                urgency: low
                body: ""
        """)
        _prop(db)
        run_script(db, script)
        assert any(str(TODAY) in s for s in _subjects(db))

    def test_params_in_template(self, db):
        script = _script("""
            scope:
              resource: property
            actions:
              - type: create_task
                subject: "Check every {{params.interval_hours}}h"
                category: compliance
                urgency: low
                body: ""
        """)
        _prop(db)
        run_script(db, script, params={"interval_hours": 24})
        assert any("24h" in s for s in _subjects(db))

    def test_nested_dot_notation_lease_tenant(self, db):
        script = _script("""
            scope:
              resource: lease
              filters:
                - field: payment_status
                  operator: equals
                  value: late
            actions:
              - type: create_task
                subject: "Late: {{lease.tenant.first_name}} {{lease.tenant.last_name}}"
                category: rent
                urgency: high
                body: ""
        """)
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, first="Priya", last="Patel", phone="x")
        _lease(db, p, u, t, status="late")
        run_script(db, script)
        assert any("Priya Patel" in s for s in _subjects(db))

    def test_missing_template_var_renders_empty(self, db):
        script = _script("""
            scope:
              resource: property
            actions:
              - type: create_task
                subject: "Review {{nonexistent_field}}"
                category: compliance
                urgency: low
                body: ""
        """)
        _prop(db)
        run_script(db, script)
        assert any("Review " in s for s in _subjects(db))

    def test_today_plus_arithmetic(self, db):
        """{{today + params.warn_days}} resolves to a future date string."""
        script = _script("""
            scope:
              resource: lease
              filters:
                - field: end_date
                  operator: lte
                  value: "{{today + params.warn_days}}"
                - field: end_date
                  operator: gte
                  value: "{{today}}"
            actions:
              - type: create_task
                subject: "Expiring soon"
                category: leasing
                urgency: medium
                body: ""
        """)
        p = _prop(db)
        u = _unit(db, p)
        t = _tenant(db, phone="x")
        _lease(db, p, u, t, end=TODAY + timedelta(days=25))
        assert run_script(db, script, params={"warn_days": 30}) == 1
        assert run_script(db, script, params={"warn_days": 20}) == 0  # already deduped or outside


# ===========================================================================
# Urgency expressions
# ===========================================================================

class TestUrgencyExpressions:

    def _urgency_script(self, urgency_block):
        return _script(f"""
            scope:
              resource: property
            actions:
              - type: create_task
                subject: "U test"
                category: compliance
                urgency: {urgency_block}
                body: ""
        """)

    def test_static_urgency_low(self, db):
        _prop(db)
        run_script(db, self._urgency_script("low"))
        assert _open_tasks(db)[0].urgency == "low"

    def test_static_urgency_critical(self, db):
        _prop(db)
        run_script(db, self._urgency_script("critical"))
        assert _open_tasks(db)[0].urgency == "critical"

    def test_conditional_urgency_using_unit_count(self, db):
        script = _script("""
            scope:
              resource: property
            actions:
              - type: create_task
                subject: "U {{address_line1}}"
                category: compliance
                urgency: >
                  high if unit_count > 2
                  medium otherwise
                body: ""
        """)
        p_small = _prop(db, address="Small")
        p_large = _prop(db, address="Large")
        _unit(db, p_small, "A")
        for lbl in ("A", "B", "C"):
            _unit(db, p_large, lbl)
        run_script(db, script)
        tasks = {t.title: t.urgency for t in _open_tasks(db)}
        assert tasks["U Small"] == "medium"
        assert tasks["U Large"] == "high"


# ===========================================================================
# Deduplication
# ===========================================================================

class TestDeduplication:

    SCRIPT = _script("""
        scope:
          resource: property
        actions:
          - type: create_task
            subject: "Review: {{address_line1}}"
            category: compliance
            urgency: low
            body: ""
    """)

    def test_same_script_twice_no_duplicate(self, db):
        _prop(db)
        run_script(db, self.SCRIPT)
        n2 = run_script(db, self.SCRIPT)
        assert n2 == 0
        assert len(_open_tasks(db)) == 1

    def test_suggested_status_blocks(self, db):
        _prop(db)
        run_script(db, self.SCRIPT)
        task = _open_tasks(db)[0]
        assert task.task_status == "suggested"
        assert run_script(db, self.SCRIPT) == 0

    def test_resolved_allows_recreation(self, db):
        _prop(db)
        run_script(db, self.SCRIPT)
        _open_tasks(db)[0].task_status = "resolved"
        db.flush()
        n = run_script(db, self.SCRIPT)
        assert n == 1

    def test_cancelled_allows_recreation(self, db):
        _prop(db)
        run_script(db, self.SCRIPT)
        _open_tasks(db)[0].task_status = "cancelled"
        db.flush()
        assert run_script(db, self.SCRIPT) == 1

    def test_different_subjects_not_deduped(self, db):
        _prop(db, address="A")
        _prop(db, address="B")
        n = run_script(db, self.SCRIPT)
        assert n == 2


# ===========================================================================
# Error handling
# ===========================================================================

class TestErrorHandling:

    def test_bad_yaml_returns_zero(self, db):
        _prop(db)
        n = run_script(db, "::invalid: [yaml")
        assert n == 0

    def test_unknown_resource_returns_zero(self, db):
        script = _script("""
            scope:
              resource: spaceship
            actions:
              - type: create_task
                subject: "X"
                category: compliance
                urgency: low
                body: ""
        """)
        _prop(db)
        n = run_script(db, script)
        assert n == 0

    def test_missing_resource_key_returns_zero(self, db):
        script = _script("""
            scope: {}
            actions:
              - type: create_task
                subject: "X"
                category: compliance
                urgency: low
                body: ""
        """)
        _prop(db)
        n = run_script(db, script)
        assert n == 0

    def test_unknown_action_type_creates_no_tasks(self, db):
        script = _script("""
            scope:
              resource: property
            actions:
              - type: send_notification
                to: tenant
        """)
        _prop(db)
        n = run_script(db, script)
        assert n == 0

    def test_no_actions_returns_zero(self, db):
        script = _script("""
            scope:
              resource: property
            actions: []
        """)
        _prop(db)
        n = run_script(db, script)
        assert n == 0


# ===========================================================================
# Integration: run_data_audit runs custom scripts
# ===========================================================================

class TestAuditIntegration:
    """run_data_audit must execute DSL scripts stored in custom_meta."""

    SCRIPT = textwrap.dedent("""\
        scope:
          resource: property
        actions:
          - type: create_task
            subject: "Custom review: {{address_line1}}"
            category: compliance
            urgency: low
            body: ""
    """)

    def _cfg(self, key, enabled=True, script=None):
        return {
            "checks": {
                key: {"enabled": enabled, "interval_hours": 1},
                # disable all built-ins so they don't pollute task counts
                **{k: {"enabled": False} for k in [
                    "lease_status",
                    "incomplete_properties", "missing_contact",
                ]},
            },
            "custom_meta": {
                key: {
                    "label": "Test Custom",
                    "description": "Test",
                    "simulation_run": True,
                    **({"script": script} if script else {}),
                }
            },
        }

    def test_enabled_custom_automation_runs_script(self, db):
        _prop(db)
        cfg = self._cfg("my_check", enabled=True, script=self.SCRIPT)
        n = run_data_audit(db, config=cfg)
        assert n == 1
        assert any("Custom review" in s for s in [t.title for t in _open_tasks(db)])

    def test_disabled_custom_automation_skipped(self, db):
        _prop(db)
        cfg = self._cfg("my_check", enabled=False, script=self.SCRIPT)
        n = run_data_audit(db, config=cfg)
        assert n == 0

    def test_custom_automation_without_script_skipped(self, db):
        _prop(db)
        cfg = self._cfg("my_check", enabled=True, script=None)
        n = run_data_audit(db, config=cfg)
        assert n == 0

    def test_check_name_targets_custom_automation(self, db):
        """Passing check_name should run only the matching custom script."""
        _prop(db)
        cfg = self._cfg("my_check", enabled=False, script=self.SCRIPT)
        # Disabled but explicitly targeted via check_name
        n = run_data_audit(db, config=cfg, check_name="my_check")
        assert n == 1

    def test_check_name_does_not_run_other_custom(self, db):
        _prop(db)
        cfg = {
            "checks": {
                "check_a": {"enabled": True},
                "check_b": {"enabled": True},
                **{k: {"enabled": False} for k in [
                    "lease_status",
                    "incomplete_properties", "missing_contact",
                ]},
            },
            "custom_meta": {
                "check_a": {"label": "A", "simulation_run": True, "script": self.SCRIPT},
                "check_b": {"label": "B", "simulation_run": True, "script": self.SCRIPT},
            },
        }
        # Only run check_a
        n = run_data_audit(db, config=cfg, check_name="check_a")
        assert n == 1

    def test_multiple_custom_automations_all_run(self, db):
        _prop(db, address="100 A")
        _prop(db, address="200 B")
        script_a = textwrap.dedent("""\
            scope:
              resource: property
            conditions:
              - field: address_line1
                operator: contains
                value: "100"
            actions:
              - type: create_task
                subject: "A: {{address_line1}}"
                category: compliance
                urgency: low
                body: ""
        """)
        script_b = textwrap.dedent("""\
            scope:
              resource: property
            conditions:
              - field: address_line1
                operator: contains
                value: "200"
            actions:
              - type: create_task
                subject: "B: {{address_line1}}"
                category: compliance
                urgency: low
                body: ""
        """)
        cfg = {
            "checks": {
                "check_a": {"enabled": True},
                "check_b": {"enabled": True},
                **{k: {"enabled": False} for k in [
                    "lease_status",
                    "incomplete_properties", "missing_contact",
                ]},
            },
            "custom_meta": {
                "check_a": {"label": "A", "simulation_run": True, "script": script_a},
                "check_b": {"label": "B", "simulation_run": True, "script": script_b},
            },
        }
        n = run_data_audit(db, config=cfg)
        assert n == 2
        subjects = [t.title for t in _open_tasks(db)]
        assert any("100 A" in s for s in subjects)
        assert any("200 B" in s for s in subjects)
