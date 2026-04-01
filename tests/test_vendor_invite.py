"""
End-to-end tests for the vendor invite system (token-only auth).

Covers:
- create_vendor stores invite_token and invite_status in extra
- create_vendor requires phone or email
- VendorType.from_sql exposes invite_token / invite_status
- _find_by_invite_token locates vendor by token
- accept_invite transitions status to accepted and returns JWT
- GET /vendor-invite/{token} returns vendor info (+ JWT for accepted vendors)
- POST /vendor-invite/{token}/accept accepts invite and returns JWT
- assign_vendor_to_task blocks pending rentmate vendors
- assign_vendor_to_task allows accepted rentmate vendors
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
from handlers.vendor_invite import get_invite_info, accept_invite


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
        inp = CreateVendorInput(name="Alice Plumber", phone="555-0001", contact_method="rentmate")
        vendor = VendorService.create_vendor(db, inp)
        extra = vendor.extra or {}
        assert extra.get("contact_method") == "rentmate"
        assert extra.get("invite_token") is not None
        assert len(extra["invite_token"]) > 10
        assert extra.get("invite_status") == "pending"

    def test_create_email_vendor_has_no_token(self, db):
        inp = CreateVendorInput(name="Bob Email", email="bob@test.com", contact_method="email")
        vendor = VendorService.create_vendor(db, inp)
        extra = vendor.extra or {}
        assert extra.get("invite_token") is None
        assert extra.get("invite_status") == "n/a"

    def test_create_phone_vendor_has_no_token(self, db):
        inp = CreateVendorInput(name="Carol SMS", phone="555-0002", contact_method="phone")
        vendor = VendorService.create_vendor(db, inp)
        extra = vendor.extra or {}
        assert extra.get("invite_token") is None
        assert extra.get("invite_status") == "n/a"

    def test_each_rentmate_vendor_gets_unique_token(self, db):
        a = VendorService.create_vendor(db, CreateVendorInput(name="A", phone="555-0003", contact_method="rentmate"))
        b = VendorService.create_vendor(db, CreateVendorInput(name="B", phone="555-0004", contact_method="rentmate"))
        assert a.extra["invite_token"] != b.extra["invite_token"]

    def test_create_vendor_without_phone_or_email_raises(self, db):
        with pytest.raises(ValueError, match="phone or email"):
            VendorService.create_vendor(db, CreateVendorInput(name="No Contact", contact_method="rentmate"))

    def test_create_vendor_with_only_email_succeeds(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Email Only", email="e@t.com", contact_method="rentmate"))
        assert vendor.email == "e@t.com"

    def test_create_vendor_with_only_phone_succeeds(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Phone Only", phone="555-9999", contact_method="rentmate"))
        assert vendor.phone == "555-9999"


class TestFindByInviteToken:

    def test_finds_vendor_by_token(self, db):
        inp = CreateVendorInput(name="Dave", phone="555-0010", contact_method="rentmate")
        vendor = VendorService.create_vendor(db, inp)
        token = vendor.extra["invite_token"]

        found = VendorService._find_by_invite_token(db, token)
        assert found is not None
        assert found.id == vendor.id

    def test_returns_none_for_unknown_token(self, db):
        result = VendorService._find_by_invite_token(db, "totally-fake-token")
        assert result is None

    def test_does_not_find_email_vendor_by_bogus_token(self, db):
        VendorService.create_vendor(db, CreateVendorInput(name="Eve", email="eve@t.com", contact_method="email"))
        result = VendorService._find_by_invite_token(db, "no-token")
        assert result is None


class TestAcceptInvite:

    def test_accept_transitions_status_and_returns_jwt(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Frank", phone="555-0020", contact_method="rentmate"))
        token = vendor.extra["invite_token"]

        returned_vendor, jwt_token = VendorService.accept_invite(db, token)

        assert returned_vendor.id == vendor.id
        assert jwt_token is not None
        db.expire(vendor)
        assert vendor.extra["invite_status"] == "accepted"

        # JWT is valid
        info = VendorService.validate_vendor_token(jwt_token)
        assert info["vendor_id"] == str(vendor.id)
        assert info["type"] == "vendor"

    def test_accept_is_idempotent(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Grace", phone="555-0021", contact_method="rentmate"))
        token = vendor.extra["invite_token"]
        VendorService.accept_invite(db, token)
        # Second call should not raise
        _, jwt2 = VendorService.accept_invite(db, token)
        assert jwt2 is not None
        db.expire(vendor)
        assert vendor.extra["invite_status"] == "accepted"

    def test_accept_invalid_token_raises(self, db):
        with pytest.raises(ValueError, match="Invalid or expired"):
            VendorService.accept_invite(db, "bad-token")


class TestGetJwtForToken:

    def test_returns_jwt_for_accepted_vendor(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Hank", phone="555-0030", contact_method="rentmate"))
        token = vendor.extra["invite_token"]
        VendorService.accept_invite(db, token)

        _, jwt_token = VendorService.get_jwt_for_token(db, token)
        info = VendorService.validate_vendor_token(jwt_token)
        assert info["vendor_id"] == str(vendor.id)

    def test_raises_for_unknown_token(self, db):
        with pytest.raises(ValueError):
            VendorService.get_jwt_for_token(db, "nonexistent")


class TestVendorTypeFromSql:

    def test_rentmate_vendor_exposes_token_and_pending(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Henry", phone="555-0040", contact_method="rentmate"))
        vt = VendorType.from_sql(vendor)
        assert vt.invite_token == vendor.extra["invite_token"]
        assert vt.invite_status == "pending"

    def test_accepted_vendor_shows_accepted(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Iris", phone="555-0041", contact_method="rentmate"))
        VendorService.accept_invite(db, vendor.extra["invite_token"])
        db.expire(vendor)
        vt = VendorType.from_sql(vendor)
        assert vt.invite_status == "accepted"
        assert vt.invite_token is not None

    def test_email_vendor_has_no_token_and_na_status(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Jack", email="jack@t.com", contact_method="email"))
        vt = VendorType.from_sql(vendor)
        assert vt.invite_token is None
        assert vt.invite_status == "n/a"


# ---------------------------------------------------------------------------
# REST endpoint tests
# ---------------------------------------------------------------------------

class TestGetInviteInfoEndpoint:

    def test_returns_vendor_info_for_valid_token(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(
            name="Kelly", company="Kelly Plumbing", vendor_type="Plumber", phone="555-0050", contact_method="rentmate"
        ))
        token = vendor.extra["invite_token"]

        result = get_invite_info(token, MockRequest(db))

        assert result["name"] == "Kelly"
        assert result["company"] == "Kelly Plumbing"
        assert result["vendor_type"] == "Plumber"
        assert result["invite_status"] == "pending"
        assert "access_token" not in result

    def test_returns_404_for_unknown_token(self, db):
        with pytest.raises(HTTPException) as exc_info:
            get_invite_info("nonexistent-token", MockRequest(db))
        assert exc_info.value.status_code == 404

    def test_returns_jwt_for_accepted_vendor(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Leo", phone="555-0051", contact_method="rentmate"))
        token = vendor.extra["invite_token"]
        VendorService.accept_invite(db, token)
        db.expire(vendor)

        result = get_invite_info(token, MockRequest(db))
        assert result["invite_status"] == "accepted"
        assert "access_token" in result
        info = VendorService.validate_vendor_token(result["access_token"])
        assert info["vendor_id"] == str(vendor.id)


class TestAcceptInviteEndpoint:

    def test_accept_returns_ok_and_jwt(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Mia", phone="555-0060", contact_method="rentmate"))
        token = vendor.extra["invite_token"]

        result = accept_invite(token, MockRequest(db))
        assert result["ok"] is True
        assert "access_token" in result
        assert result["name"] == "Mia"

    def test_accept_updates_status_in_db(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Ned", phone="555-0061", contact_method="rentmate"))
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
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Oscar", phone="555-0070", contact_method="rentmate"))
        task = self._mk_task(db)
        with pytest.raises(ValueError, match="has not accepted their invite"):
            TaskService.assign_vendor_to_task(db, str(task.id), str(vendor.id))

    def test_accepted_rentmate_vendor_can_be_assigned(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Pam", phone="555-0071", contact_method="rentmate"))
        VendorService.accept_invite(db, vendor.extra["invite_token"])
        db.expire(vendor)
        task = self._mk_task(db)

        result = TaskService.assign_vendor_to_task(db, str(task.id), str(vendor.id))
        ai_convo = db.get(Conversation, result.ai_conversation_id)
        assert (ai_convo.extra or {}).get("assigned_vendor_id") == str(vendor.id)
        assert (ai_convo.extra or {}).get("assigned_vendor_name") == "Pam"

    def test_email_vendor_can_always_be_assigned(self, db):
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Quinn", email="quinn@t.com", contact_method="email"))
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
              createVendor(input: {name: "Rita", phone: "555-0080", contactMethod: "rentmate"}) {
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
              createVendor(input: {name: "Sam", email: "sam@t.com", contactMethod: "email"}) {
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
        vendor = VendorService.create_vendor(db, CreateVendorInput(name="Tara", phone="555-0090", contact_method="rentmate"))
        token = vendor.extra["invite_token"]

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
