from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from db.enums import TaskCategory, TaskSource, Urgency
from db.models import Lease, User
from gql.services.task_service import TaskService
from gql.services.tenant_service import TenantService
from gql.types import CreateTaskInput, CreateTenantWithLeaseInput
from main import app


def _tenant_headers(db):
    property_owner = User(
        org_id=1,
        creator_id=1,
        email="owner@example.com",
        active=True,
    )
    db.add(property_owner)
    db.flush()

    from db.models import Property, Unit

    prop = Property(
        org_id=1,
        creator_id=1,
        name="Portal Property",
        address_line1="123 Main St",
        city="Seattle",
        state="WA",
        postal_code="98101",
        property_type="multi_family",
        source="manual",
    )
    db.add(prop)
    db.flush()

    unit = Unit(
        org_id=1,
        creator_id=1,
        property_id=prop.id,
        label="A",
    )
    db.add(unit)
    db.flush()

    tenant, _, lease = TenantService.create_tenant_with_lease(
        db,
        CreateTenantWithLeaseInput(
            first_name="Alice",
            last_name="Renter",
            email="alice@example.com",
            phone="206-555-0100",
            property_id=prop.id,
            unit_id=unit.id,
            lease_start="2026-01-01",
            lease_end="2026-12-31",
            rent_amount=1800,
        ),
    )
    portal_token = TenantService.ensure_portal_token(db, tenant)
    _, token = TenantService.authenticate_by_token(db, portal_token)
    return tenant, prop, unit, lease, {"Authorization": f"Bearer {token}"}


def _tenant_task(db, prop, unit):
    task = TaskService.create_task(
        db,
        CreateTaskInput(
            title="Fix squeaky door",
            goal="Fix the squeaky door and confirm it closes quietly.",
            source=TaskSource.MANUAL,
            category=TaskCategory.MAINTENANCE,
            urgency=Urgency.MEDIUM,
            property_id=prop.id,
            unit_id=unit.id,
        ),
    )
    db.commit()
    return task


def test_tenant_portal_uses_external_tenant_id_from_jwt(db):
    tenant, prop, unit, _lease, headers = _tenant_headers(db)
    task = _tenant_task(db, prop, unit)

    client = TestClient(app)

    me = client.get("/api/tenant/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["id"] == str(tenant.external_id)
    assert me.json()["name"] == "Alice Renter"

    tasks = client.get("/api/tenant/tasks", headers=headers)
    assert tasks.status_code == 200
    payload = tasks.json()
    assert len(payload) == 1
    assert payload[0]["id"] == str(task.id)

    detail = client.get(f"/api/tenant/tasks/{task.id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["id"] == str(task.id)
