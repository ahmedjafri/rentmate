import pytest

from db.models import Property, Tenant, Unit
from gql.services.tenant_service import TenantService
from gql.types import AddLeaseForTenantInput, CreateTenantWithLeaseInput


def _property_and_unit(db):
    prop = Property(
        id="prop-1",
        org_id=1,
        creator_id=1,
        address_line1="123 Main St",
        property_type="multi_family",
        source="manual",
    )
    unit = Unit(
        id="unit-1",
        org_id=1,
        creator_id=1,
        property_id="prop-1",
        label="1A",
    )
    db.add_all([prop, unit])
    db.flush()
    return prop, unit


def test_create_tenant_with_lease_creates_shadow_user_and_lease(db):
    prop, unit = _property_and_unit(db)

    tenant, created_unit, lease = TenantService.create_tenant_with_lease(
        db,
        CreateTenantWithLeaseInput(
            first_name="Jane",
            last_name="Doe",
            property_id=prop.id,
            unit_id=unit.id,
            lease_start="2026-01-01",
            lease_end="2026-12-31",
            rent_amount=2200.0,
            email="jane@example.com",
            phone="+15550004444",
        ),
    )

    assert tenant.external_id
    assert tenant.org_id == 1
    assert tenant.creator_id == 1
    assert tenant.user.email == "jane@example.com"
    assert created_unit.id == unit.id
    assert lease.tenant_id == tenant.id
    assert lease.unit_id == unit.id


def test_add_lease_delete_and_portal_auth_use_external_tenant_id(db):
    prop, unit = _property_and_unit(db)
    tenant, _, first_lease = TenantService.create_tenant_with_lease(
        db,
        CreateTenantWithLeaseInput(
            first_name="John",
            last_name="Smith",
            property_id=prop.id,
            unit_id=unit.id,
            lease_start="2026-01-01",
            lease_end="2026-06-30",
            rent_amount=1800.0,
        ),
    )

    next_lease = TenantService.add_lease_for_tenant(
        db,
        AddLeaseForTenantInput(
            tenant_id=tenant.external_id,
            property_id=prop.id,
            unit_id=unit.id,
            lease_start="2026-07-01",
            lease_end="2027-06-30",
            rent_amount=1900.0,
        ),
    )[2]

    portal_token = TenantService.ensure_portal_token(db, tenant)
    found, jwt_token = TenantService.authenticate_by_token(db, portal_token)

    assert first_lease.id != next_lease.id
    assert found.id == tenant.id
    assert TenantService.validate_tenant_token(jwt_token)["tenant_id"] == tenant.external_id
    assert TenantService.get_portal_url(tenant).endswith(portal_token)

    assert TenantService.delete_tenant(db, tenant.external_id) is True
    assert db.query(Tenant).filter_by(external_id=tenant.external_id).one_or_none() is None


def test_create_tenant_with_lease_rejects_missing_unit(db):
    prop = Property(
        id="prop-2",
        org_id=1,
        creator_id=1,
        address_line1="555 State St",
        property_type="multi_family",
        source="manual",
    )
    db.add(prop)
    db.flush()

    with pytest.raises(ValueError, match="Unit missing not found"):
        TenantService.create_tenant_with_lease(
            db,
            CreateTenantWithLeaseInput(
                first_name="Bad",
                last_name="Input",
                property_id=prop.id,
                unit_id="missing",
                lease_start="2026-01-01",
                lease_end="2026-12-31",
                rent_amount=1000.0,
            ),
        )
