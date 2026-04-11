"""Tests for gql/services/tenant_service.py."""

from datetime import UTC, date, datetime

import pytest

from db.models import Property, Tenant, Unit, User
from gql.services.tenant_service import TenantService
from gql.types import AddLeaseForTenantInput, CreateTenantWithLeaseInput


def _mk_property(db):
    prop = Property(
        id="prop-1",
        org_id=1,
        creator_id=1,
        address_line1="123 Main St",
        created_at=datetime.now(UTC),
    )
    db.add(prop)
    db.flush()
    return prop


def _mk_unit(db, property_id="prop-1"):
    unit = Unit(
        id="unit-1",
        org_id=1,
        creator_id=1,
        property_id=property_id,
        label="1A",
        created_at=datetime.now(UTC),
    )
    db.add(unit)
    db.flush()
    return unit


def _mk_tenant(db):
    shadow_user = User(
        org_id=1,
        first_name="Alice",
        last_name="Smith",
        active=True,
        created_at=datetime.now(UTC),
    )
    db.add(shadow_user)
    db.flush()

    tenant = Tenant(
        org_id=1,
        creator_id=1,
        user_id=shadow_user.id,
        created_at=datetime.now(UTC),
    )
    db.add(tenant)
    db.flush()
    return tenant


class TestDeleteTenant:
    def test_delete_existing_tenant(self, db):
        tenant = _mk_tenant(db)

        result = TenantService.delete_tenant(db, tenant.external_id)

        assert result is True
        assert db.get(Tenant, tenant.id) is None

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

        assert tenant.user.first_name == "Bob"
        assert tenant.user.last_name == "Jones"
        assert tenant.user.email == "bob@example.com"
        assert lease.rent_amount == 1200.0
        assert lease.tenant_id == tenant.id
        assert unit.id == "unit-1"

    def test_missing_unit_raises(self, db):
        _mk_property(db)
        inp = CreateTenantWithLeaseInput(
            first_name="X",
            last_name="Y",
            property_id="prop-1",
            unit_id="no-unit",
            lease_start="2025-01-01",
            lease_end="2025-12-31",
            rent_amount=1000.0,
        )

        with pytest.raises(ValueError, match="not found"):
            TenantService.create_tenant_with_lease(db, inp)


class TestAddLeaseForTenant:
    def test_adds_lease_to_existing_tenant(self, db):
        _mk_property(db)
        _mk_unit(db)
        tenant = _mk_tenant(db)
        inp = AddLeaseForTenantInput(
            tenant_id=tenant.external_id,
            property_id="prop-1",
            unit_id="unit-1",
            lease_start="2025-06-01",
            lease_end="2026-05-31",
            rent_amount=1500.0,
        )

        loaded_tenant, unit, lease = TenantService.add_lease_for_tenant(db, inp)

        assert loaded_tenant.id == tenant.id
        assert unit.id == "unit-1"
        assert lease.rent_amount == 1500.0
        assert lease.start_date == date(2025, 6, 1)

    def test_missing_tenant_raises(self, db):
        _mk_property(db)
        _mk_unit(db)
        inp = AddLeaseForTenantInput(
            tenant_id="ghost",
            property_id="prop-1",
            unit_id="unit-1",
            lease_start="2025-01-01",
            lease_end="2025-12-31",
            rent_amount=1000.0,
        )

        with pytest.raises(ValueError, match="not found"):
            TenantService.add_lease_for_tenant(db, inp)

    def test_missing_unit_raises(self, db):
        _mk_property(db)
        tenant = _mk_tenant(db)
        inp = AddLeaseForTenantInput(
            tenant_id=tenant.external_id,
            property_id="prop-1",
            unit_id="no-unit",
            lease_start="2025-01-01",
            lease_end="2025-12-31",
            rent_amount=1000.0,
        )

        with pytest.raises(ValueError, match="not found"):
            TenantService.add_lease_for_tenant(db, inp)
