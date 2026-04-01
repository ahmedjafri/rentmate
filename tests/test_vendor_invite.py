"""
End-to-end tests for the vendor invite system.

Covers:
- create_vendor stores invite_token and invite_status in extra
- VendorType.from_sql exposes invite_token / invite_status
- _find_by_invite_token locates vendor by token
- accept_vendor_invite transitions status to accepted (idempotent)
- GET /vendor-invite/{token} returns vendor info or 404
- POST /vendor-invite/{token}/accept accepts the invite
- POST /vendor-invite/{token}/register creates account and returns JWT
- POST /api/vendor/login authenticates returning vendor
- assign_vendor_to_task blocks pending rentmate vendors
- assign_vendor_to_task allows accepted and registered rentmate vendors
- email/phone vendors get invite_status "n/a" and are always assignable
- GraphQL createVendor mutation returns inviteToken / inviteStatus
- GraphQL acceptVendorInvite mutation works without auth
"""

import pytest
from fastapi import HTTPException
from gql.schema import schema
from gql.services.vendor_service import VendorService
from gql.services.task_service import TaskService
from gql.types import CreateVendorInput, CreateTaskInput, VendorType
from db.models import ExternalContact, Conversation, Task
from handlers.vendor_invite import get_invite_info, accept_invite, register_vendor, RegisterBody
from handlers.vendor_portal import vendor_login, vendor_me, vendor_tasks, LoginBody


FAKE_USER = {"id": "test-user-id", "email": "admin@test.com"}


def _gql_context(db):
    return {"db_session": db, "user": FAKE_USER}


def _gql_context_no_auth(db):
    return {"db_session": db, "user": None}


class MockRequest:
    """Minimal stand-in for a FastAPI Request that exposes a db session."""
    def __init__(self, db):
        class _State:
            pass
        self.state = _State()
        self.state.db_session = db


# ---------------------------------------------------------------------------
# Service-layer tests
# ---------------------------------------------------------------------------

class TestVendorServiceCreate:

    def test_create_rentmate_vendor_generates_token(self, db):
        inp = CreateVendorInput(name="Alice Plumber", contact_method="rentmate")
        vendor = VendorService.create_vendor(db, inp)
        extra = vendor.extra or {}
        assert extra.get("contact_method") == "rentmate"
        assert extra.get("invite_token") is not None
        assert len(extra["invite_token"]) > 10
        assert extra.get("invite_status") == "pending"

    def test_create_email_vendor_has_no_token(self, db):
        inp = CreateVendorInput(name="Bob Email", contact_method="email")
        vendor = VendorService.create_vendor(db, inp)
        extra = vendor.extra or {}
        assert extra.get("invite_token") is None
        assert extra.get("invite_status") == "n/a"

    def test_create_phone_vendor_has_no_token(self, db):
        inp = CreateVendorInput(name="Carol SMS", contact_method="phone")
        vendor = VendorService.create_vendor(db, inp)
        extra = vendor.extra or {}
        assert extra.get("invite_token") is None
        assert extra.get("invite_status") == "n/a"

    def test_each_rentmate_vendor_gets_unique_token(self, db):
        a = VendorService.create_vendor(db, CreateVendorInput(name="A", contact_method="rentmate"))
        b = VendorService.create_vendor(db, CreateVendorInput(name="B", contact_method="rentmate"))
        assert a.extra["invite_token"] != b.extra["invite_token"]


class TestFindByInviteToken:

    def test_finds_vendor_by_token(self, db):
        inp = CreateVendorInput(name="Dave", contact_method="rentmate")
        vendor = VendorService.create_vendor(db, inp)
        token = vendor.extra["invite_token"]

        found = VendorService._find_by_invite_token(db, token)
        assert found is not None
        assert found.id == vendor.id

    def test_returns_none_for_unknown_token(self, db):
        result = VendorService._find_by_invite_token(db, "totally-fake-token")
        assert result is None

    def test_does_not_find_email_vendor_by_bogus_token(self, db):
        VendorService.create_vendor(db, CreateVendorInput(name="Eve", contact_method="email"))
        result = VendorService._find_by_invite_token(db, "no-token")
        assert result is None


class TestAcceptVendorInvite:

    def test_accept_transitions_status_to_accepted(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Frank", contact_method="rentmate"))
        token = vendor.extra["invite_token"]

        VendorService.accept_vendor_invite(db, token)

        db.expire(vendor)
        assert vendor.extra["invite_status"] == "accepted"

    def test_accept_is_idempotent(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Grace", contact_method="rentmate"))
        token = vendor.extra["invite_token"]
        VendorService.accept_vendor_invite(db, token)
        # Second call should not raise
        VendorService.accept_vendor_invite(db, token)
        db.expire(vendor)
        assert vendor.extra["invite_status"] == "accepted"

    def test_accept_invalid_token_raises(self, db):
        with pytest.raises(ValueError, match="Invalid or expired"):
            VendorService.accept_vendor_invite(db, "bad-token")


class TestVendorTypeFromSql:

    def test_rentmate_vendor_exposes_token_and_pending(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Henry", contact_method="rentmate"))
        vt = VendorType.from_sql(vendor)
        assert vt.invite_token == vendor.extra["invite_token"]
        assert vt.invite_status == "pending"

    def test_accepted_vendor_shows_accepted(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Iris", contact_method="rentmate"))
        VendorService.accept_vendor_invite(db, vendor.extra["invite_token"])
        db.expire(vendor)
        vt = VendorType.from_sql(vendor)
        assert vt.invite_status == "accepted"
        # Token still returned so admin can re-share if needed
        assert vt.invite_token is not None

    def test_email_vendor_has_no_token_and_na_status(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Jack", contact_method="email"))
        vt = VendorType.from_sql(vendor)
        assert vt.invite_token is None
        assert vt.invite_status == "n/a"


# ---------------------------------------------------------------------------
# REST endpoint tests
# ---------------------------------------------------------------------------

class TestGetInviteInfoEndpoint:

    def test_returns_vendor_info_for_valid_token(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(
            name="Kelly", company="Kelly Plumbing", vendor_type="Plumber", contact_method="rentmate"
        ))
        token = vendor.extra["invite_token"]

        result = get_invite_info(token, MockRequest(db))

        assert result["name"] == "Kelly"
        assert result["company"] == "Kelly Plumbing"
        assert result["vendor_type"] == "Plumber"
        assert result["invite_status"] == "pending"

    def test_returns_404_for_unknown_token(self, db):
        with pytest.raises(HTTPException) as exc_info:
            get_invite_info("nonexistent-token", MockRequest(db))
        assert exc_info.value.status_code == 404

    def test_shows_accepted_status_after_accept(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Leo", contact_method="rentmate"))
        token = vendor.extra["invite_token"]
        VendorService.accept_vendor_invite(db, token)
        db.expire(vendor)

        result = get_invite_info(token, MockRequest(db))
        assert result["invite_status"] == "accepted"


class TestAcceptInviteEndpoint:

    def test_accept_returns_ok(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Mia", contact_method="rentmate"))
        token = vendor.extra["invite_token"]

        result = accept_invite(token, MockRequest(db))
        assert result == {"ok": True}

    def test_accept_updates_status_in_db(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Ned", contact_method="rentmate"))
        token = vendor.extra["invite_token"]

        accept_invite(token, MockRequest(db))
        db.expire(vendor)

        assert vendor.extra["invite_status"] == "accepted"

    def test_accept_invalid_token_returns_404(self, db):
        with pytest.raises(HTTPException) as exc_info:
            accept_invite("bad-token", MockRequest(db))
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Task assignment gating
# ---------------------------------------------------------------------------

class TestAssignVendorToTask:

    def _mk_task(self, db):
        task = TaskService.create_task(db, CreateTaskInput(title="Fix sink", source="manual"))
        return task

    def test_pending_rentmate_vendor_cannot_be_assigned(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Oscar", contact_method="rentmate"))
        task = self._mk_task(db)
        with pytest.raises(ValueError, match="has not accepted their invite"):
            TaskService.assign_vendor_to_task(db, str(task.id), str(vendor.id))

    def test_accepted_rentmate_vendor_can_be_assigned(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Pam", contact_method="rentmate"))
        VendorService.accept_vendor_invite(db, vendor.extra["invite_token"])
        db.expire(vendor)
        task = self._mk_task(db)

        result = TaskService.assign_vendor_to_task(db, str(task.id), str(vendor.id))
        ai_convo = db.get(Conversation, result.ai_conversation_id)
        assert (ai_convo.extra or {}).get("assigned_vendor_id") == str(vendor.id)
        assert (ai_convo.extra or {}).get("assigned_vendor_name") == "Pam"

    def test_email_vendor_can_always_be_assigned(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Quinn", contact_method="email"))
        task = self._mk_task(db)
        result = TaskService.assign_vendor_to_task(db, str(task.id), str(vendor.id))
        ai_convo = db.get(Conversation, result.ai_conversation_id)
        assert (ai_convo.extra or {}).get("assigned_vendor_id") == str(vendor.id)


# ---------------------------------------------------------------------------
# GraphQL mutation tests
# ---------------------------------------------------------------------------

class TestCreateVendorMutation:

    def test_create_vendor_returns_invite_token(self, db):
        result = schema.execute_sync(
            """
            mutation {
              createVendor(input: {name: "Rita", contactMethod: "rentmate"}) {
                uid name contactMethod inviteToken inviteStatus
              }
            }
            """,
            context_value=_gql_context(db),
        )
        assert result.errors is None
        v = result.data["createVendor"]
        assert v["contactMethod"] == "rentmate"
        assert v["inviteToken"] is not None
        assert v["inviteStatus"] == "pending"

    def test_create_email_vendor_has_no_token(self, db):
        result = schema.execute_sync(
            """
            mutation {
              createVendor(input: {name: "Sam", contactMethod: "email"}) {
                uid contactMethod inviteToken inviteStatus
              }
            }
            """,
            context_value=_gql_context(db),
        )
        assert result.errors is None
        v = result.data["createVendor"]
        assert v["inviteToken"] is None
        assert v["inviteStatus"] == "n/a"


class TestAcceptVendorInviteMutation:

    def test_accept_via_gql_no_auth_needed(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Tara", contact_method="rentmate"))
        token = vendor.extra["invite_token"]

        # No user in context — public mutation
        result = schema.execute_sync(
            """
            mutation AcceptInvite($token: String!) {
              acceptVendorInvite(token: $token)
            }
            """,
            variable_values={"token": token},
            context_value=_gql_context_no_auth(db),
        )
        assert result.errors is None
        assert result.data["acceptVendorInvite"] is True

        db.expire(vendor)
        assert vendor.extra["invite_status"] == "accepted"

    def test_accept_bad_token_returns_error(self, db):
        result = schema.execute_sync(
            """
            mutation { acceptVendorInvite(token: "bad") }
            """,
            context_value=_gql_context_no_auth(db),
        )
        assert result.errors is not None


# ---------------------------------------------------------------------------
# Register endpoint tests
# ---------------------------------------------------------------------------

class TestRegisterVendorEndpoint:

    def test_register_sets_status_and_returns_token(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Uma", contact_method="rentmate"))
        token = vendor.extra["invite_token"]

        result = register_vendor(token, RegisterBody(email="uma@test.com", password="password123"), MockRequest(db))

        assert "access_token" in result
        assert result["name"] == "Uma"
        db.expire(vendor)
        assert vendor.extra["invite_status"] == "registered"
        assert vendor.extra["vendor_email"] == "uma@test.com"

    def test_register_bad_token_raises_400(self, db):
        with pytest.raises(HTTPException) as exc_info:
            register_vendor("bad-token", RegisterBody(email="x@test.com", password="password123"), MockRequest(db))
        assert exc_info.value.status_code == 400

    def test_register_duplicate_email_raises_400(self, db):
        v1 = VendorService.create_vendor(db, CreateVendorInput(name="Vera", contact_method="rentmate"))
        v2 = VendorService.create_vendor(db, CreateVendorInput(name="Vince", contact_method="rentmate"))
        register_vendor(v1.extra["invite_token"], RegisterBody(email="shared@test.com", password="password123"), MockRequest(db))
        with pytest.raises(HTTPException) as exc_info:
            register_vendor(v2.extra["invite_token"], RegisterBody(email="shared@test.com", password="password123"), MockRequest(db))
        assert exc_info.value.status_code == 400

    def test_register_token_is_valid_vendor_jwt(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Walt", contact_method="rentmate"))
        result = register_vendor(
            vendor.extra["invite_token"],
            RegisterBody(email="walt@test.com", password="secretpass"),
            MockRequest(db),
        )
        info = VendorService.validate_vendor_token(result["access_token"])
        assert info["vendor_id"] == str(vendor.id)
        assert info["email"] == "walt@test.com"


# ---------------------------------------------------------------------------
# Login endpoint tests
# ---------------------------------------------------------------------------

class TestVendorLogin:

    def _register(self, db, name="Xena", email="xena@test.com", password="testpass1"):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name=name, contact_method="rentmate"))
        register_vendor(vendor.extra["invite_token"], RegisterBody(email=email, password=password), MockRequest(db))
        return vendor

    def test_login_returns_token(self, db):
        self._register(db)
        result = vendor_login(LoginBody(email="xena@test.com", password="testpass1"), MockRequest(db))
        assert "access_token" in result
        assert result["name"] == "Xena"

    def test_login_wrong_password_raises_401(self, db):
        self._register(db)
        with pytest.raises(HTTPException) as exc_info:
            vendor_login(LoginBody(email="xena@test.com", password="wrongpass"), MockRequest(db))
        assert exc_info.value.status_code == 401

    def test_login_unknown_email_raises_401(self, db):
        with pytest.raises(HTTPException) as exc_info:
            vendor_login(LoginBody(email="nobody@test.com", password="whatever"), MockRequest(db))
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# Task assignment gate — registered vendor
# ---------------------------------------------------------------------------

class TestAssignRegisteredVendor:

    def _mk_task(self, db):
        task = TaskService.create_task(db, CreateTaskInput(title="Pipe leak", source="manual"))
        return task

    def test_registered_vendor_can_be_assigned(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Yara", contact_method="rentmate"))
        register_vendor(
            vendor.extra["invite_token"],
            RegisterBody(email="yara@test.com", password="password1"),
            MockRequest(db),
        )
        db.expire(vendor)
        task = self._mk_task(db)
        result = TaskService.assign_vendor_to_task(db, str(task.id), str(vendor.id))
        ai_convo = db.get(Conversation, result.ai_conversation_id)
        assert (ai_convo.extra or {}).get("assigned_vendor_id") == str(vendor.id)
