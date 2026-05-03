"""Tests for ``gql/services/lease_service.py``.

Covers each LeaseService method against the real Lease + LeaseTenant
schema (multi-tenant join). Uses the same per-test transactional
fixture as the rest of the gql/services test suite.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from db.models import LeaseTenant, Property, Unit
from gql.types import CreateTenantWithLeaseInput, NewTenantForLeaseInput
from services.lease_service import LeaseService
from services.tenant_service import TenantService


def _seed_property_and_unit(db, *, prop_id="prop-ls-1", unit_id="unit-ls-1"):
    prop = Property(
        id=prop_id, org_id=1, creator_id=1,
        address_line1="100 Lease St", property_type="multi_family",
        source="manual",
    )
    unit = Unit(
        id=unit_id, org_id=1, creator_id=1,
        property_id=prop_id, label="2A",
    )
    db.add_all([prop, unit])
    db.flush()
    return prop, unit


def _seed_tenant_with_lease(db, *, first="Marcus", last="Johnson", prop_id="prop-ls-1", unit_id="unit-ls-1"):
    return TenantService.create_tenant_with_lease(
        db,
        CreateTenantWithLeaseInput(
            first_name=first,
            last_name=last,
            property_id=prop_id,
            unit_id=unit_id,
            lease_start="2026-01-01",
            lease_end="2026-12-31",
            rent_amount=1900.0,
        ),
    )


# ─── list_leases ───────────────────────────────────────────────────────


def test_list_leases_filters_by_property_and_active(db):
    prop, unit = _seed_property_and_unit(db)
    _seed_tenant_with_lease(db)

    rows = LeaseService.list_leases(db, property_id=prop.id, active_only=True)
    assert len(rows) == 1
    assert rows[0].property_id == prop.id


def test_list_leases_active_only_excludes_past_leases(db):
    prop, unit = _seed_property_and_unit(db)
    _seed_tenant_with_lease(db)
    # Force end_date into the past so the lease is no longer active.
    rows = LeaseService.list_leases(db, property_id=prop.id, active_only=False)
    rows[0].end_date = date.today() - timedelta(days=30)
    db.flush()

    active = LeaseService.list_leases(db, property_id=prop.id, active_only=True)
    assert active == []
    all_rows = LeaseService.list_leases(db, property_id=prop.id, active_only=False)
    assert len(all_rows) == 1


def test_list_leases_filters_by_tenant_via_join_table(db):
    """A tenant added via the multi-tenant join table (not the legacy
    primary ``tenant_id``) should still surface in lookup."""
    prop, unit = _seed_property_and_unit(db)
    primary, _, lease = _seed_tenant_with_lease(db)

    # Add a co-tenant via the service.
    co_primary, _, _co_lease = _seed_tenant_with_lease(
        db, first="Priya", last="Patel",
        prop_id="prop-ls-2", unit_id="unit-ls-2",
    ) if False else (None, None, None)  # placeholder, re-seeded below
    co_prop = Property(
        id="prop-ls-2", org_id=1, creator_id=1,
        address_line1="200 Other St", property_type="multi_family",
        source="manual",
    )
    co_unit = Unit(id="unit-ls-2", org_id=1, creator_id=1, property_id="prop-ls-2", label="1A")
    db.add_all([co_prop, co_unit])
    db.flush()
    co_primary = TenantService._create_tenant(
        db, NewTenantForLeaseInput(first_name="Priya", last_name="Patel"),
    )
    LeaseService.add_tenant_to_lease(db, lease_id=lease.id, tenant_id=str(co_primary.external_id))

    rows = LeaseService.list_leases(db, tenant_id=str(co_primary.external_id), active_only=True)
    assert len(rows) == 1
    assert rows[0].id == lease.id


# ─── update_lease ──────────────────────────────────────────────────────


def test_update_lease_patches_payment_status(db):
    _seed_property_and_unit(db)
    _, _, lease = _seed_tenant_with_lease(db)

    updated = LeaseService.update_lease(db, lease_id=lease.id, payment_status="late")
    assert updated.payment_status == "late"


def test_update_lease_rejects_unknown_payment_status(db):
    _seed_property_and_unit(db)
    _, _, lease = _seed_tenant_with_lease(db)

    with pytest.raises(ValueError, match="payment_status"):
        LeaseService.update_lease(db, lease_id=lease.id, payment_status="paid")


def test_update_lease_rejects_end_before_start(db):
    _seed_property_and_unit(db)
    _, _, lease = _seed_tenant_with_lease(db)

    with pytest.raises(ValueError, match="end_date"):
        LeaseService.update_lease(db, lease_id=lease.id, end_date=date(2025, 1, 1))


def test_update_lease_changes_rent_and_end_date(db):
    _seed_property_and_unit(db)
    _, _, lease = _seed_tenant_with_lease(db)

    updated = LeaseService.update_lease(
        db, lease_id=lease.id,
        end_date=date(2027, 6, 30), rent_amount=2050.0,
    )
    assert updated.end_date == date(2027, 6, 30)
    assert updated.rent_amount == 2050.0


def test_update_lease_unknown_id_raises(db):
    with pytest.raises(ValueError, match="not found"):
        LeaseService.update_lease(db, lease_id="bad-id", payment_status="late")


# ─── terminate_lease ───────────────────────────────────────────────────


def test_terminate_lease_sets_end_date_to_today_by_default(db):
    _seed_property_and_unit(db)
    _, _, lease = _seed_tenant_with_lease(db)

    terminated = LeaseService.terminate_lease(db, lease_id=lease.id)
    assert terminated.end_date == date.today()


def test_terminate_lease_rejects_effective_before_start(db):
    _seed_property_and_unit(db)
    _, _, lease = _seed_tenant_with_lease(db)

    with pytest.raises(ValueError, match="precede"):
        LeaseService.terminate_lease(db, lease_id=lease.id, effective_date=date(2025, 1, 1))


# ─── add_tenant_to_lease + remove_tenant_from_lease ───────────────────


def test_add_tenant_to_lease_appends_to_join_table(db):
    _seed_property_and_unit(db)
    primary, _, lease = _seed_tenant_with_lease(db)
    co_tenant = TenantService._create_tenant(
        db, NewTenantForLeaseInput(first_name="Priya", last_name="Patel"),
    )

    LeaseService.add_tenant_to_lease(db, lease_id=lease.id, tenant_id=str(co_tenant.external_id))

    links = db.query(LeaseTenant).filter_by(lease_id=lease.id).all()
    tenant_ids = sorted(l.tenant_id for l in links)
    assert tenant_ids == sorted([primary.id, co_tenant.id])


def test_add_tenant_to_lease_is_idempotent(db):
    _seed_property_and_unit(db)
    primary, _, lease = _seed_tenant_with_lease(db)

    # Adding the primary tenant (already on the lease) should be a no-op.
    LeaseService.add_tenant_to_lease(db, lease_id=lease.id, tenant_id=str(primary.external_id))
    LeaseService.add_tenant_to_lease(db, lease_id=lease.id, tenant_id=str(primary.external_id))
    links = db.query(LeaseTenant).filter_by(lease_id=lease.id).all()
    assert len(links) == 1


def test_remove_tenant_from_lease_drops_link(db):
    _seed_property_and_unit(db)
    primary, _, lease = _seed_tenant_with_lease(db)
    co = TenantService._create_tenant(
        db, NewTenantForLeaseInput(first_name="Priya", last_name="Patel"),
    )
    LeaseService.add_tenant_to_lease(db, lease_id=lease.id, tenant_id=str(co.external_id))

    LeaseService.remove_tenant_from_lease(db, lease_id=lease.id, tenant_id=str(co.external_id))

    remaining = db.query(LeaseTenant).filter_by(lease_id=lease.id).all()
    assert [l.tenant_id for l in remaining] == [primary.id]


def test_remove_last_tenant_from_lease_is_rejected(db):
    """Refusing to wipe the last tenant keeps the legacy
    ``Lease.tenant_id`` FK pointing at a real row so queries that join
    on it don't silently lose the lease."""
    _seed_property_and_unit(db)
    primary, _, lease = _seed_tenant_with_lease(db)

    with pytest.raises(ValueError, match="last tenant"):
        LeaseService.remove_tenant_from_lease(
            db, lease_id=lease.id, tenant_id=str(primary.external_id),
        )


def test_remove_tenant_repoints_legacy_primary_id_when_dropping_primary(db):
    """If the primary tenant is removed but co-tenants remain, the
    legacy ``Lease.tenant_id`` column should rotate to a surviving
    tenant so historical joins still resolve."""
    _seed_property_and_unit(db)
    primary, _, lease = _seed_tenant_with_lease(db)
    co = TenantService._create_tenant(
        db, NewTenantForLeaseInput(first_name="Priya", last_name="Patel"),
    )
    LeaseService.add_tenant_to_lease(db, lease_id=lease.id, tenant_id=str(co.external_id))

    LeaseService.remove_tenant_from_lease(
        db, lease_id=lease.id, tenant_id=str(primary.external_id),
    )

    db.refresh(lease)
    assert lease.tenant_id == co.id


# ─── lease_to_payload ──────────────────────────────────────────────────


def test_lease_to_payload_includes_property_unit_and_tenants(db):
    _seed_property_and_unit(db)
    primary, _, lease = _seed_tenant_with_lease(db)

    payload = LeaseService.lease_to_payload(db, lease)
    assert payload["lease_id"] == lease.id
    assert payload["unit_label"] == "2A"
    assert payload["active"] is True
    tenant_ids = [t["tenant_id"] for t in payload["tenants"]]
    assert str(primary.external_id) in tenant_ids
