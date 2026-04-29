"""GraphQL-layer regression tests for tenant creation.

Pre-existing service tests in ``gql/services/tests/test_tenant_service.py``
exercise the service functions directly and miss the resolver/type
boundary. This file drives ``schema.execute_sync(...)`` so any signature
mismatch between ``TenantService.create_tenant_with_lease`` /
``add_lease_for_tenant`` and ``TenantType.from_new`` shows up as a
failed mutation rather than a silent service-layer pass.

Spec drift this catches today: ``TenantType.from_new`` previously
declared ``unit`` and ``lease`` as keyword-only, but both resolvers
unpack the service's ``(tenant, unit, lease)`` tuple positionally —
the mutation crashed with
``TenantType.from_new() takes 2 positional arguments but 4 were given``.
"""
from db.models import Property, Unit
from gql.schema import schema


def _context(db):
    return {
        "db_session": db,
        "user": {
            "uid": "test-user-1",
            "email": "test-admin@example.com",
            "username": "test-admin@example.com",
        },
    }


def _seed_property_and_unit(db, *, prop_id="prop-tm-1", unit_id="unit-tm-1"):
    prop = Property(
        id=prop_id, org_id=1, creator_id=1,
        address_line1="42 Tenant St", property_type="multi_family",
        source="manual",
    )
    unit = Unit(
        id=unit_id, org_id=1, creator_id=1,
        property_id=prop_id, label="2B",
    )
    db.add_all([prop, unit])
    db.flush()
    return prop, unit


def test_create_tenant_with_lease_mutation_returns_tenant(db):
    """Manually creating a tenant via the mutation should return a
    populated TenantType with the tenant + unit + lease info merged."""
    prop, unit = _seed_property_and_unit(db)

    result = schema.execute_sync(
        """
        mutation Create($input: CreateTenantWithLeaseInput!) {
          createTenantWithLease(input: $input) {
            uid
            name
            email
            unitLabel
            leaseEndDate
            rentAmount
            isActive
          }
        }
        """,
        variable_values={
            "input": {
                "firstName": "Marcus",
                "lastName": "Johnson",
                "propertyId": prop.id,
                "unitId": unit.id,
                "leaseStart": "2026-01-01",
                "leaseEnd": "2026-12-31",
                "rentAmount": 1850.0,
                "email": "marcus@example.com",
                "phone": "+15550001234",
            },
        },
        context_value=_context(db),
    )

    assert result.errors is None, result.errors
    payload = result.data["createTenantWithLease"]
    assert payload["name"] == "Marcus Johnson"
    assert payload["email"] == "marcus@example.com"
    assert payload["unitLabel"] == "2B"
    assert payload["leaseEndDate"] == "2026-12-31"
    assert payload["rentAmount"] == 1850.0
    assert payload["isActive"] is True
    assert payload["uid"]


def test_add_lease_for_tenant_mutation_returns_tenant(db):
    """Adding a follow-on lease for an existing tenant — same resolver
    pattern, same risk of the from_new signature mismatch — should
    return the tenant payload populated from the new lease."""
    prop, unit = _seed_property_and_unit(db, prop_id="prop-tm-2", unit_id="unit-tm-2")

    create = schema.execute_sync(
        """
        mutation Create($input: CreateTenantWithLeaseInput!) {
          createTenantWithLease(input: $input) { uid }
        }
        """,
        variable_values={
            "input": {
                "firstName": "Priya",
                "lastName": "Patel",
                "propertyId": prop.id,
                "unitId": unit.id,
                "leaseStart": "2026-01-01",
                "leaseEnd": "2026-06-30",
                "rentAmount": 2100.0,
            },
        },
        context_value=_context(db),
    )
    assert create.errors is None, create.errors
    tenant_uid = create.data["createTenantWithLease"]["uid"]

    renew = schema.execute_sync(
        """
        mutation AddLease($input: AddLeaseForTenantInput!) {
          addLeaseForTenant(input: $input) {
            uid
            name
            unitLabel
            leaseEndDate
            rentAmount
          }
        }
        """,
        variable_values={
            "input": {
                "tenantId": tenant_uid,
                "propertyId": prop.id,
                "unitId": unit.id,
                "leaseStart": "2026-07-01",
                "leaseEnd": "2027-06-30",
                "rentAmount": 2200.0,
            },
        },
        context_value=_context(db),
    )

    assert renew.errors is None, renew.errors
    payload = renew.data["addLeaseForTenant"]
    assert payload["uid"] == tenant_uid
    assert payload["name"] == "Priya Patel"
    assert payload["unitLabel"] == "2B"
    assert payload["leaseEndDate"] == "2027-06-30"
    assert payload["rentAmount"] == 2200.0
