import bcrypt
from fastapi.testclient import TestClient

from db.enums import TaskCategory, TaskSource, Urgency
from db.models import User
from gql.services import chat_service
from gql.services.task_service import TaskService
from gql.services.vendor_service import VendorService, get_vendor_login_email
from gql.types import CreateTaskInput, CreateVendorInput
from main import app


def _vendor_headers(db):
    vendor = VendorService.create_vendor(
        db,
        CreateVendorInput(
            name="Vince Vendor",
            phone="555-0001",
            vendor_type="Plumber",
        ),
    )
    _, token = VendorService.authenticate_by_token(db, vendor.extra["portal_token"])
    return vendor, {"Authorization": f"Bearer {token}"}


def _assigned_task(db, vendor):
    task = TaskService.create_task(
        db,
        CreateTaskInput(
            title="Fix leaking sink",
            goal="Fix the leaking sink and confirm the repair is complete.",
            source=TaskSource.MANUAL,
            category=TaskCategory.MAINTENANCE,
            urgency=Urgency.MEDIUM,
        ),
    )
    ext_convo = chat_service.get_or_create_external_conversation(
        db,
        subject=task.title,
        conversation_type="vendor",
        vendor_id=vendor.id,
        parent_task_id=task.id,
    )
    TaskService.assign_vendor_to_task(db, task_id=task.id, vendor_id=vendor.id)
    db.commit()
    return task, ext_convo


def test_vendor_portal_lists_tasks_and_detail_messages(db):
    vendor, headers = _vendor_headers(db)
    task, ext_convo = _assigned_task(db, vendor)
    chat_service.send_message(
        db,
        conversation_id=ext_convo.id,
        body="Can you come by tomorrow?",
        sender_name="RentMate",
        is_ai=False,
    )
    db.commit()

    client = TestClient(app)

    tasks_response = client.get("/api/vendor/tasks", headers=headers)
    assert tasks_response.status_code == 200
    payload = tasks_response.json()
    assert len(payload) == 1
    assert payload[0]["id"] == str(task.id)
    assert payload[0]["task_number"] == task.id
    assert payload[0]["title"] == task.title

    detail_response = client.get(f"/api/vendor/tasks/{task.id}", headers=headers)
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["id"] == str(task.id)
    assert detail["task_number"] == task.id
    assert detail["messages"][0]["body"] == "Can you come by tomorrow?"


def test_vendor_can_create_account_from_portal_session(db):
    vendor, headers = _vendor_headers(db)
    client = TestClient(app)

    response = client.post(
        "/api/vendor/account",
        headers=headers,
        json={"email": "vince@example.com", "password": "pw"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["has_account"] is True
    db.refresh(vendor)
    assert vendor.email == "vince@example.com"
    assert vendor.password_hash

    me = client.get("/api/vendor/me", headers={"Authorization": f"Bearer {payload['access_token']}"})
    assert me.status_code == 200
    assert me.json()["has_account"] is True


def test_vendor_login_links_existing_account_using_portal_token(db):
    vendor = VendorService.create_vendor(
        db,
        CreateVendorInput(
            name="Linked Vendor",
            phone="555-0002",
            vendor_type="Plumber",
        ),
    )
    existing = User(
        org_id=1,
        creator_id=1,
        email="linked@example.com",
        password_hash=bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode(),
        active=True,
    )
    db.add(existing)
    db.commit()

    client = TestClient(app)
    response = client.post(
        "/api/vendor/login",
        json={
            "email": "linked@example.com",
            "password": "pw",
            "token": vendor.extra["portal_token"],
        },
    )

    assert response.status_code == 200
    db.refresh(vendor)
    assert vendor.extra["linked_user_id"] == existing.id

    token_response = client.get(f"/api/vendor-token/{vendor.extra['portal_token']}")
    assert token_response.status_code == 200
    assert token_response.json()["login_required"] is True


def test_vendor_login_reuses_existing_linked_account_without_portal_token(db):
    vendor = VendorService.create_vendor(
        db,
        CreateVendorInput(
            name="Linked Vendor",
            phone="555-0003",
            vendor_type="Plumber",
        ),
    )
    existing = User(
        org_id=1,
        creator_id=1,
        email="linked-again@example.com",
        password_hash=bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode(),
        active=True,
    )
    db.add(existing)
    db.commit()
    vendor.extra = {**(vendor.extra or {}), "linked_user_id": existing.id}
    db.commit()

    client = TestClient(app)
    response = client.post(
        "/api/vendor/login",
        json={
            "email": "linked-again@example.com",
            "password": "pw",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["vendor_id"] == str(vendor.external_id)
    assert payload["email"] == get_vendor_login_email(db, vendor)

    me = client.get("/api/vendor/me", headers={"Authorization": f"Bearer {payload['access_token']}"})
    assert me.status_code == 200
    assert me.json()["id"] == str(vendor.external_id)
    assert me.json()["email"] == "linked-again@example.com"
