"""Tests for gql/services/tenant_service.py."""
from datetime import date, datetime

import pytest

from db.models import Property, Tenant, Unit
from gql.services.tenant_service import TenantService
from gql.types import AddLeaseForTenantInput, CreateTenantWithLeaseInput


def _mk_property(db):
    p = Property(
        id="prop-1",
        address_line1="123 Main St",
        created_at=datetime.utcnow(),
    )
    db.add(p)
    db.flush()
    return p


def _mk_unit(db, property_id="prop-1"):
    u = Unit(
        id="unit-1",
        property_id=property_id,
        label="1A",
        created_at=datetime.utcnow(),
    )
    db.add(u)
    db.flush()
    return u


def _mk_tenant(db):
    t = Tenant(
        id="tenant-1",
        first_name="Alice",
        last_name="Smith",
        created_at=datetime.utcnow(),
    )
    db.add(t)
    db.flush()
    return t


class TestDeleteTenant:
    def test_delete_existing_tenant(self, db):
        _mk_tenant(db)
        result = TenantService.delete_tenant(db, "tenant-1")
        assert result is True
        assert db.get(Tenant, "tenant-1") is None

    def test_delete_nonexistent_tenant_raises(self, db):
        with pytest.raises(ValueError, match="not found"):
            TenantService.delete_tenant(db, "no-such-id")


class TestCreateTenantWithLease:
    def test_creates_tenant_and_lease(self, db):
        _mk_property(db)
        _mk_unit(db)
        inp = CreateTenantWithLeaseInput(
            first_name="Bob",
            last_name="Jones",
            property_id="prop-1",
            unit_id="unit-1",
            lease_start="2025-01-01",
            lease_end="2025-12-31",
            rent_amount=1200.0,
            email="bob@example.com",
            phone="5551234567",
        )
        tenant, unit, lease = TenantService.create_tenant_with_lease(db, inp)
        assert tenant.first_name == "Bob"
        assert lease.rent_amount == 1200.0
        assert lease.tenant_id == tenant.id
        assert lease.unit_id == "unit-1"

    def test_missing_unit_raises(self, db):
        _mk_property(db)
        inp = CreateTenantWithLeaseInput(
            first_name="X", last_name="Y",
            property_id="prop-1", unit_id="no-unit",
            lease_start="2025-01-01", lease_end="2025-12-31", rent_amount=1000.0,
        )
        with pytest.raises(ValueError, match="not found"):
            TenantService.create_tenant_with_lease(db, inp)


class TestAddLeaseForTenant:
    def test_adds_lease_to_existing_tenant(self, db):
        _mk_property(db)
        _mk_unit(db)
        _mk_tenant(db)
        inp = AddLeaseForTenantInput(
            tenant_id="tenant-1",
            property_id="prop-1",
            unit_id="unit-1",
            lease_start="2025-06-01",
            lease_end="2026-05-31",
            rent_amount=1500.0,
        )
        tenant, unit, lease = TenantService.add_lease_for_tenant(db, inp)
        assert tenant.id == "tenant-1"
        assert lease.rent_amount == 1500.0
        assert lease.start_date == date(2025, 6, 1)

    def test_missing_tenant_raises(self, db):
        _mk_property(db)
        _mk_unit(db)
        inp = AddLeaseForTenantInput(
            tenant_id="ghost", property_id="prop-1", unit_id="unit-1",
            lease_start="2025-01-01", lease_end="2025-12-31", rent_amount=1000.0,
        )
        with pytest.raises(ValueError, match="not found"):
            TenantService.add_lease_for_tenant(db, inp)

    def test_missing_unit_raises(self, db):
        _mk_property(db)
        _mk_tenant(db)
        inp = AddLeaseForTenantInput(
            tenant_id="tenant-1", property_id="prop-1", unit_id="no-unit",
            lease_start="2025-01-01", lease_end="2025-12-31", rent_amount=1000.0,
        )
        with pytest.raises(ValueError, match="not found"):
            TenantService.add_lease_for_tenant(db, inp)
