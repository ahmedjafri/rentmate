from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from db.enums import TaskCategory, TaskSource, Urgency
from db.models import ConversationType, Lease, Notification, User
from gql.services import chat_service
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


def _tenant_conversation(db, tenant, task):
    convo = chat_service.get_or_create_external_conversation(
        db,
        subject=task.title,
        conversation_type=ConversationType.TENANT,
        tenant_id=tenant.id,
        parent_task_id=task.id,
    )
    task.parent_conversation_id = convo.id
    chat_service.send_message(
        db,
        conversation_id=convo.id,
        body="We can stop by tomorrow morning.",
        sender_name="RentMate",
        is_ai=False,
    )
    db.commit()
    return convo


def test_tenant_portal_uses_external_tenant_id_from_jwt(db):
    tenant, prop, unit, _lease, headers = _tenant_headers(db)
    task = _tenant_task(db, prop, unit)
    convo = _tenant_conversation(db, tenant, task)

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
    assert detail.json()["messages"][0]["body"] == "We can stop by tomorrow morning."

    conversations = client.get("/api/tenant/conversations", headers=headers)
    assert conversations.status_code == 200
    conv_payload = conversations.json()
    assert len(conv_payload) == 1
    assert conv_payload[0]["id"] == str(convo.id)
    assert conv_payload[0]["linked_task"]["id"] == str(task.id)

    conv_detail = client.get(f"/api/tenant/conversations/{convo.id}", headers=headers)
    assert conv_detail.status_code == 200
    assert conv_detail.json()["messages"][0]["body"] == "We can stop by tomorrow morning."


def test_tenant_portal_lists_standalone_conversation_without_task(db):
    """Conversations the tenant participates in that aren't tied to any task
    should still appear in the tenant portal's conversation list."""
    tenant, prop, unit, _lease, headers = _tenant_headers(db)

    standalone = chat_service.get_or_create_external_conversation(
        db,
        subject="Quick check-in",
        conversation_type=ConversationType.TENANT,
        tenant_id=tenant.id,
    )
    chat_service.send_message(
        db,
        conversation_id=standalone.id,
        body="Hi Alice, just checking in — everything good at the unit?",
        sender_name="RentMate",
        is_ai=True,
    )
    db.commit()

    client = TestClient(app)
    response = client.get("/api/tenant/conversations", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    row = payload[0]
    assert row["id"] == str(standalone.id)
    assert row["linked_task"] is None
    assert row["title"] == "Quick check-in"


def test_tenant_portal_message_creates_pm_notification(db):
    tenant, prop, unit, _lease, headers = _tenant_headers(db)
    task = _tenant_task(db, prop, unit)
    convo = _tenant_conversation(db, tenant, task)

    client = TestClient(app)
    response = client.post(
        f"/api/tenant/conversations/{convo.id}/messages",
        headers=headers,
        json={"body": "Can you confirm the plumber is still coming?"},
    )

    assert response.status_code == 200
    notification = db.query(Notification).filter(Notification.task_id == task.id).order_by(Notification.id.desc()).first()
    assert notification is not None
    assert notification.recipient_user_id == task.creator_id
    assert notification.kind == "conversation_update"
    assert notification.title == "New tenant message"
    assert "plumber is still coming" in (notification.body or "")
