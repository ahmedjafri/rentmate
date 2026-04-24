"""
Tests for vendor portal token auth system.

Covers:
- create_vendor generates a short portal_token
- create_vendor requires phone
- VendorType.from_sql exposes portal_url
- _find_by_portal_token locates vendor by token (and legacy invite_token)
- authenticate_by_token returns JWT without accept step
- GET /api/vendor-token/{token} returns JWT
- Vendors can always be assigned to tasks (no gating)
- GraphQL createVendor mutation returns portalUrl
"""

import bcrypt
import pytest
from fastapi import HTTPException

from db.models import Conversation, User
from gql.schema import schema
from gql.services.task_service import TaskService
from gql.services.vendor_service import VendorService
from gql.types import CreateTaskInput, CreateVendorInput, VendorType
from handlers.portals.vendor_invite import get_vendor_token

FAKE_USER = {"id": "test-user-id", "email": "admin@test.com"}


def _gql_context(db):
    return {"db_session": db, "user": FAKE_USER}


class MockRequest:
    def __init__(self, db):
        class _State:
            pass
        self.state = _State()
        self.state.db_session = db


# ---------------------------------------------------------------------------
# Service-layer tests
# ---------------------------------------------------------------------------

class TestVendorServiceCreate:

    def test_create_vendor_generates_short_portal_token(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Alice Plumber", phone="555-0001", vendor_type="Plumber"))
        extra = vendor.extra or {}
        assert extra.get("portal_token") is not None
        assert 6 <= len(extra["portal_token"]) <= 12  # token_urlsafe(6) ≈ 8 chars

    def test_each_vendor_gets_unique_token(self, db):
        a = VendorService.create_vendor(db, CreateVendorInput(name="A", phone="555-0003", vendor_type="Plumber"))
        b = VendorService.create_vendor(db, CreateVendorInput(name="B", phone="555-0004", vendor_type="Plumber"))
        assert a.extra["portal_token"] != b.extra["portal_token"]

    def test_create_vendor_without_phone_raises(self, db):
        with pytest.raises(ValueError, match="Phone number is required"):
            VendorService.create_vendor(db, CreateVendorInput(name="No Phone", phone="", vendor_type="Plumber"))

    def test_create_vendor_with_phone_succeeds(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Phone Only", phone="555-9999", vendor_type="Plumber"))
        assert vendor.phone == "555-9999"


class TestFindByPortalToken:

    def test_finds_vendor_by_portal_token(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Dave", phone="555-0010", vendor_type="Plumber"))
        token = vendor.extra["portal_token"]
        found = VendorService._find_by_portal_token(db, token)
        assert found is not None
        assert found.id == vendor.id

    def test_returns_none_for_unknown_token(self, db):
        result = VendorService._find_by_portal_token(db, "totally-fake-token")
        assert result is None

    def test_backward_compat_finds_by_legacy_invite_token(self, db):
        """Vendors created before the portal_token migration should still be findable."""
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Legacy", phone="555-0011", vendor_type="Plumber"))
        # Simulate old vendor with only invite_token
        extra = dict(vendor.extra or {})
        old_token = "old-legacy-invite-token-abc123"
        extra["invite_token"] = old_token
        vendor.extra = extra
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(vendor, "extra")
        db.flush()

        found = VendorService._find_by_portal_token(db, old_token)
        assert found is not None
        assert found.id == vendor.id


class TestAuthenticateByToken:

    def test_returns_jwt_for_valid_token(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Frank", phone="555-0020", vendor_type="Plumber"))
        token = vendor.extra["portal_token"]

        returned_vendor, jwt_token = VendorService.authenticate_by_token(db, token)
        assert returned_vendor.id == vendor.id
        assert jwt_token is not None

        info = VendorService.validate_vendor_token(jwt_token)
        assert info["vendor_id"] == str(vendor.external_id)
        assert info["type"] == "vendor"

    def test_invalid_token_raises(self, db):
        with pytest.raises(ValueError, match="Invalid portal link"):
            VendorService.authenticate_by_token(db, "bad-token")


class TestVendorTypeFromSql:

    def test_vendor_exposes_portal_url(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Henry", phone="555-0040", vendor_type="Plumber"))
        vt = VendorType.from_sql(vendor)
        assert vt.portal_url is not None
        assert "/t/" in vt.portal_url
        assert vendor.extra["portal_token"] in vt.portal_url


class TestGetPortalUrl:

    def test_local_url_format(self, db, monkeypatch):
        monkeypatch.delenv("RENTMATE_PUBLIC_URL", raising=False)
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Ivy", phone="555-0045", vendor_type="Plumber"))
        url = VendorService.get_portal_url(vendor)
        assert url.startswith("http://localhost:")
        assert f"/t/{vendor.extra['portal_token']}" in url


# ---------------------------------------------------------------------------
# REST endpoint tests
# ---------------------------------------------------------------------------

class TestVendorTokenEndpoint:

    def test_returns_jwt_for_valid_token(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Kelly", phone="555-0050", vendor_type="Plumber"))
        token = vendor.extra["portal_token"]

        result = get_vendor_token(token, MockRequest(db))
        assert result["name"] == "Kelly"
        assert result["login_required"] is False
        assert "access_token" in result
        info = VendorService.validate_vendor_token(result["access_token"])
        assert info["vendor_id"] == str(vendor.external_id)

    def test_returns_404_for_unknown_token(self, db):
        with pytest.raises(HTTPException) as exc_info:
            get_vendor_token("nonexistent-token", MockRequest(db))
        assert exc_info.value.status_code == 404

    def test_returns_login_required_after_vendor_has_account(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Kelly", phone="555-0050", vendor_type="Plumber"))
        vendor.email = "kelly@example.com"
        vendor.password_hash = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
        db.commit()

        result = get_vendor_token(vendor.extra["portal_token"], MockRequest(db))
        assert result["login_required"] is True
        assert "access_token" not in result
        assert result["email"] == "kelly@example.com"

    def test_returns_login_required_for_linked_existing_account(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Kelly", phone="555-0050", vendor_type="Plumber"))
        existing = User(
            org_id=1,
            creator_id=1,
            email="linked@example.com",
            password_hash=bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode(),
            active=True,
        )
        db.add(existing)
        db.commit()
        extra = dict(vendor.extra or {})
        extra["linked_user_id"] = existing.id
        vendor.extra = extra
        db.commit()

        result = get_vendor_token(vendor.extra["portal_token"], MockRequest(db))
        assert result["login_required"] is True
        assert "access_token" not in result
        assert result["email"] == "linked@example.com"


# ---------------------------------------------------------------------------
# Task assignment (no gating)
# ---------------------------------------------------------------------------

class TestAssignVendorToTask:

    def _mk_task(self, db):
        return TaskService.create_task(db, CreateTaskInput(title="Fix sink", goal="Fix the sink and confirm it is working.", source="manual"))

    def test_vendor_can_be_assigned(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Oscar", phone="555-0070", vendor_type="Plumber"))
        task = self._mk_task(db)
        result = TaskService.assign_vendor_to_task(db, task_id=str(task.id), vendor_id=str(vendor.id))
        ai_convo = db.get(Conversation, result.ai_conversation_id)
        assert (ai_convo.extra or {}).get("assigned_vendor_id") == str(vendor.id)
        assert (ai_convo.extra or {}).get("assigned_vendor_name") == "Oscar"


# ---------------------------------------------------------------------------
# GraphQL mutation tests
# ---------------------------------------------------------------------------

class TestCreateVendorMutation:

    def test_create_vendor_returns_portal_url(self, db):
        result = schema.execute_sync(
            """
            mutation {
              createVendor(input: {name: "Rita", phone: "555-0080", vendorType: "Plumber"}) {
                uid name portalUrl
              }
            }
            """,
            context_value=_gql_context(db),
        )
        assert result.errors is None
        v = result.data["createVendor"]
        assert v["portalUrl"] is not None
        assert "/t/" in v["portalUrl"]
