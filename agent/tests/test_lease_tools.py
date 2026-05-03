"""Tests for the five lease tools in ``llm/tools/leases.py``.

Each tool is exercised end-to-end: real DB rows seeded via
``TenantService``, tool invoked through ``Tool.execute()`` (no
mock-the-service), JSON response shape asserted, and the underlying
``Lease`` / ``LeaseTenant`` rows checked for the expected mutation.

Tools open their own session via ``tool_session()`` which imports
``SessionLocal`` from ``rentmate.app``. We patch that name (plus
``db.session`` for safety) to redirect to the per-test ``db`` fixture
so tool writes hit the same transaction the assertions read from.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from datetime import date
from unittest.mock import patch

from agent.tools import (
    AddTenantToLeaseTool,
    CreateLeaseTool,
    LookupLeasesTool,
    RemoveTenantFromLeaseTool,
    TerminateLeaseTool,
    UpdateLeaseTool,
)
from db.models import LeaseTenant, Property, Unit
from gql.types import CreateTenantWithLeaseInput, NewTenantForLeaseInput
from services.tenant_service import TenantService


@contextmanager
def _bind_session(db):
    with patch("rentmate.app.SessionLocal.session_factory", return_value=db), \
         patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        yield


def _run(tool, **kwargs):
    return json.loads(asyncio.run(tool.execute(**kwargs)))


def _seed(db, *, prop_id="prop-lt-1", unit_id="unit-lt-1"):
    prop = Property(
        id=prop_id, org_id=1, creator_id=1,
        address_line1="42 Tool St", property_type="multi_family",
        source="manual",
    )
    unit = Unit(
        id=unit_id, org_id=1, creator_id=1,
        property_id=prop_id, label="3B",
    )
    db.add_all([prop, unit])
    db.flush()
    tenant, _, lease = TenantService.create_tenant_with_lease(
        db,
        CreateTenantWithLeaseInput(
            first_name="Marcus",
            last_name="Johnson",
            property_id=prop_id,
            unit_id=unit_id,
            lease_start="2026-01-01",
            lease_end="2026-12-31",
            rent_amount=1900.0,
        ),
    )
    return prop, unit, tenant, lease


# ─── LookupLeasesTool ──────────────────────────────────────────────────


def test_lookup_leases_returns_lease_for_property(db):
    prop, _, tenant, lease = _seed(db)
    db.commit()

    with _bind_session(db):
        result = _run(LookupLeasesTool(), property_id=prop.id)

    assert result["count"] == 1
    payload = result["leases"][0]
    assert payload["lease_id"] == lease.id
    assert payload["unit_label"] == "3B"
    assert payload["active"] is True
    assert any(t["tenant_id"] == str(tenant.external_id) for t in payload["tenants"])


def test_lookup_leases_active_only_excludes_expired(db):
    prop, _, _, lease = _seed(db)
    lease.end_date = date(2020, 1, 1)
    db.commit()

    with _bind_session(db):
        active = _run(LookupLeasesTool(), property_id=prop.id, active_only=True)
        all_rows = _run(LookupLeasesTool(), property_id=prop.id, active_only=False)

    assert active["count"] == 0
    assert all_rows["count"] == 1


def test_lookup_leases_no_match_returns_message(db):
    db.commit()
    with _bind_session(db):
        result = _run(LookupLeasesTool(), property_id="prop-does-not-exist")
    assert result["count"] == 0
    assert "No leases" in result["message"]


# ─── UpdateLeaseTool ───────────────────────────────────────────────────


def test_update_lease_payment_status_persists(db):
    _, _, _, lease = _seed(db)
    db.commit()

    with _bind_session(db):
        result = _run(UpdateLeaseTool(), lease_id=lease.id, payment_status="late")

    assert result["status"] == "ok"
    assert result["lease"]["payment_status"] == "late"
    db.refresh(lease)
    assert lease.payment_status == "late"


def test_update_lease_rejects_bad_status_with_error_payload(db):
    _, _, _, lease = _seed(db)
    db.commit()

    with _bind_session(db):
        result = _run(UpdateLeaseTool(), lease_id=lease.id, payment_status="paid")

    assert result["status"] == "error"
    assert "payment_status" in result["message"]


def test_update_lease_rejects_bad_iso_date(db):
    _, _, _, lease = _seed(db)
    db.commit()

    with _bind_session(db):
        result = _run(UpdateLeaseTool(), lease_id=lease.id, end_date="not-a-date")

    assert result["status"] == "error"
    assert "end_date" in result["message"]


def test_update_lease_changes_rent_and_end_date(db):
    _, _, _, lease = _seed(db)
    db.commit()

    with _bind_session(db):
        result = _run(
            UpdateLeaseTool(),
            lease_id=lease.id,
            end_date="2027-06-30",
            rent_amount=2050,
        )

    assert result["status"] == "ok"
    assert result["lease"]["end_date"] == "2027-06-30"
    assert result["lease"]["rent_amount"] == 2050.0


def test_update_lease_missing_id_returns_error(db):
    db.commit()
    with _bind_session(db):
        result = _run(UpdateLeaseTool(), payment_status="late")
    assert result["status"] == "error"
    assert "lease_id" in result["message"]


# ─── TerminateLeaseTool ───────────────────────────────────────────────


def test_terminate_lease_defaults_to_today(db):
    _, _, _, lease = _seed(db)
    db.commit()

    with _bind_session(db):
        result = _run(TerminateLeaseTool(), lease_id=lease.id)

    assert result["status"] == "ok"
    assert result["lease"]["end_date"] == date.today().isoformat()


def test_terminate_lease_with_custom_effective_date(db):
    _, _, _, lease = _seed(db)
    db.commit()

    with _bind_session(db):
        result = _run(
            TerminateLeaseTool(),
            lease_id=lease.id,
            effective_date="2026-03-15",
        )
    assert result["lease"]["end_date"] == "2026-03-15"


# ─── AddTenantToLeaseTool / RemoveTenantFromLeaseTool ─────────────────


def test_add_tenant_to_lease_attaches_co_tenant(db):
    _, _, primary, lease = _seed(db)
    co = TenantService._create_tenant(
        db, NewTenantForLeaseInput(first_name="Priya", last_name="Patel"),
    )
    db.commit()

    with _bind_session(db):
        result = _run(
            AddTenantToLeaseTool(),
            lease_id=lease.id,
            tenant_id=str(co.external_id),
        )

    assert result["status"] == "ok"
    tenant_ids = [t["tenant_id"] for t in result["lease"]["tenants"]]
    assert str(primary.external_id) in tenant_ids
    assert str(co.external_id) in tenant_ids
    links = db.query(LeaseTenant).filter_by(lease_id=lease.id).all()
    assert sorted(l.tenant_id for l in links) == sorted([primary.id, co.id])


def test_remove_last_tenant_returns_error_payload(db):
    _, _, primary, lease = _seed(db)
    db.commit()

    with _bind_session(db):
        result = _run(
            RemoveTenantFromLeaseTool(),
            lease_id=lease.id,
            tenant_id=str(primary.external_id),
        )

    assert result["status"] == "error"
    assert "last tenant" in result["message"].lower()


def test_placeholder_lease_id_returns_actionable_error(db):
    """Production failure: the agent passed the literal string
    ``"lease_id_from_context"`` to add_tenant_to_lease and the service
    surfaced a confusing "Lease lease_id_from_context not found".
    The guard should reject this *before* the DB lookup with a message
    pointing the agent at lookup_leases."""
    db.commit()

    with _bind_session(db):
        result = _run(
            AddTenantToLeaseTool(),
            lease_id="lease_id_from_context",
            tenant_id="some-real-uuid",
        )

    assert result["status"] == "error"
    assert "placeholder" in result["message"].lower()
    assert "lookup_leases" in result["message"]


def test_placeholder_id_is_rejected_in_update_and_terminate(db):
    db.commit()
    with _bind_session(db):
        update = _run(UpdateLeaseTool(), lease_id="<lease_id>", payment_status="late")
        terminate = _run(TerminateLeaseTool(), lease_id="[lease_id]")

    assert update["status"] == "error"
    assert "placeholder" in update["message"].lower()
    assert terminate["status"] == "error"
    assert "placeholder" in terminate["message"].lower()


def test_create_lease_attaches_existing_tenants(db):
    """Sister to create_tenant_with_lease for the case where the tenant(s)
    already exist — gives the agent a real tool instead of falling back
    to create_suggestion with a hand-rolled action_payload."""
    prop, unit, primary, _ = _seed(db)
    co = TenantService._create_tenant(
        db, NewTenantForLeaseInput(first_name="Priya", last_name="Patel"),
    )
    db.commit()

    with _bind_session(db):
        result = _run(
            CreateLeaseTool(),
            property_id=prop.id,
            unit_id=unit.id,
            tenant_ids=[str(primary.external_id), str(co.external_id)],
            start_date="2027-01-01",
            end_date="2027-12-31",
            rent_amount=2000,
        )

    assert result["status"] == "ok"
    payload = result["lease"]
    assert payload["unit_label"] == "3B"
    assert payload["rent_amount"] == 2000.0
    tenant_ids = sorted(t["tenant_id"] for t in payload["tenants"])
    assert sorted([str(primary.external_id), str(co.external_id)]) == tenant_ids


def test_create_lease_rejects_empty_tenant_ids(db):
    prop, unit, _, _ = _seed(db)
    db.commit()
    with _bind_session(db):
        result = _run(
            CreateLeaseTool(),
            property_id=prop.id,
            unit_id=unit.id,
            tenant_ids=[],
            start_date="2027-01-01",
            end_date="2027-12-31",
            rent_amount=2000,
        )
    assert result["status"] == "error"
    assert "tenant_id" in result["message"]


def test_create_lease_rejects_unknown_unit(db):
    prop, _, primary, _ = _seed(db)
    db.commit()
    with _bind_session(db):
        result = _run(
            CreateLeaseTool(),
            property_id=prop.id,
            unit_id="missing-unit-uuid",
            tenant_ids=[str(primary.external_id)],
            start_date="2027-01-01",
            end_date="2027-12-31",
            rent_amount=2000,
        )
    assert result["status"] == "error"
    assert "Unit" in result["message"]


def test_remove_co_tenant_drops_link_only(db):
    _, _, primary, lease = _seed(db)
    co = TenantService._create_tenant(
        db, NewTenantForLeaseInput(first_name="Priya", last_name="Patel"),
    )
    # Attach the co-tenant via the service so the tool removal path is
    # the only mutation under test.
    from services.lease_service import LeaseService
    LeaseService.add_tenant_to_lease(db, lease_id=lease.id, tenant_id=str(co.external_id))
    db.commit()

    with _bind_session(db):
        result = _run(
            RemoveTenantFromLeaseTool(),
            lease_id=lease.id,
            tenant_id=str(co.external_id),
        )

    assert result["status"] == "ok"
    remaining = db.query(LeaseTenant).filter_by(lease_id=lease.id).all()
    assert [l.tenant_id for l in remaining] == [primary.id]
