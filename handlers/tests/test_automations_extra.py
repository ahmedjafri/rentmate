"""Tests for handlers/automations.py — validate, create-task, config helpers, etc."""
import json
import os
import pytest
import unittest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient
from main import app
from handlers.deps import get_db
from handlers.automations import (
    _merge_automation_config,
    _record_run,
    _run_log,
)
from backends.local_auth import DEFAULT_USER_ID


def make_token():
    import jwt
    return jwt.encode(
        {"sub": DEFAULT_USER_ID, "email": "admin@localhost"},
        os.getenv("JWT_SECRET", "rentmate-local-secret"),
        algorithm="HS256",
    )


AUTH = {"Authorization": f"Bearer {make_token()}"}

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

class TestMergeAutomationConfig:
    def test_uses_defaults_when_empty(self):
        result = _merge_automation_config({})
        assert "checks" in result
        assert len(result["checks"]) > 0

    def test_stored_overrides_default(self):
        stored = {"checks": {"lease_status": {"enabled": False}}}
        result = _merge_automation_config(stored)
        assert result["checks"]["lease_status"]["enabled"] is False

    def test_custom_checks_preserved(self):
        stored = {"checks": {"my_custom": {"enabled": True, "interval_hours": 24}}}
        result = _merge_automation_config(stored)
        assert "my_custom" in result["checks"]

    def test_custom_meta_preserved(self):
        stored = {"checks": {}, "custom_meta": {"my_check": {"label": "My Check"}}}
        result = _merge_automation_config(stored)
        assert result["custom_meta"]["my_check"]["label"] == "My Check"


class TestRecordRun:
    def test_records_successful_run(self):
        _run_log.clear()
        _record_run("test_check", tasks_created=2)
        assert "test_check" in _run_log
        entry = _run_log["test_check"][0]
        assert entry["tasks_created"] == 2
        assert entry["outcome"] == "ok"
        assert entry["error"] is None

    def test_records_error_run(self):
        _run_log.clear()
        _record_run("err_check", tasks_created=0, error="something failed")
        entry = _run_log["err_check"][0]
        assert entry["outcome"] == "error"
        assert entry["error"] == "something failed"

    def test_capped_at_max_runs(self):
        _run_log.clear()
        for i in range(15):
            _record_run("cap_check", tasks_created=i)
        assert len(_run_log["cap_check"]) == 10  # _MAX_RUNS


# ---------------------------------------------------------------------------
# /automations/validate endpoint
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("db")
class TestValidateEndpoint(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        app.dependency_overrides[get_db] = lambda: self.db

    def tearDown(self):
        app.dependency_overrides = {}

    def test_requires_auth(self):
        response = self.client.post("/automations/validate", json={"script": ""})
        assert response.status_code == 401

    def test_valid_script(self):
        script = """
scope:
  resource: property
actions:
  - type: create_task
    subject: "Test task"
    category: compliance
"""
        response = self.client.post(
            "/automations/validate", json={"script": script}, headers=AUTH
        )
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert data["errors"] == []

    def test_invalid_yaml(self):
        response = self.client.post(
            "/automations/validate",
            json={"script": "{{not: valid: yaml:"},
            headers=AUTH,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert any("YAML" in e for e in data["errors"])

    def test_missing_scope(self):
        script = "actions:\n  - type: create_task\n    subject: x\n    category: rent\n"
        response = self.client.post(
            "/automations/validate", json={"script": script}, headers=AUTH
        )
        data = response.json()
        assert data["valid"] is False
        assert any("scope" in e for e in data["errors"])

    def test_invalid_resource(self):
        script = "scope:\n  resource: spaceship\nactions:\n  - type: create_task\n    subject: x\n    category: rent\n"
        response = self.client.post(
            "/automations/validate", json={"script": script}, headers=AUTH
        )
        data = response.json()
        assert data["valid"] is False
        assert any("resource" in e for e in data["errors"])

    def test_missing_actions(self):
        script = "scope:\n  resource: property\n"
        response = self.client.post(
            "/automations/validate", json={"script": script}, headers=AUTH
        )
        data = response.json()
        assert data["valid"] is False
        assert any("actions" in e for e in data["errors"])

    def test_invalid_category(self):
        script = """
scope:
  resource: lease
actions:
  - type: create_task
    subject: "X"
    category: space_travel
"""
        response = self.client.post(
            "/automations/validate", json={"script": script}, headers=AUTH
        )
        data = response.json()
        assert data["valid"] is False
        assert any("category" in e for e in data["errors"])

    def test_wrong_action_type(self):
        script = """
scope:
  resource: property
actions:
  - type: send_email
    subject: "X"
    category: compliance
"""
        response = self.client.post(
            "/automations/validate", json={"script": script}, headers=AUTH
        )
        data = response.json()
        assert data["valid"] is False
        assert any("create_task" in e for e in data["errors"])


# ---------------------------------------------------------------------------
# POST /automations/simulate/create-task
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("db")
class TestCreateSimulatedTask(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        app.dependency_overrides[get_db] = lambda: self.db

    def tearDown(self):
        app.dependency_overrides = {}

    def _post(self, payload, headers=None, endpoint="/automations/simulate/create-suggestion"):
        return self.client.post(
            endpoint,
            json=payload,
            headers=headers or AUTH,
        )

    def test_requires_auth(self):
        response = self.client.post("/automations/simulate/create-suggestion", json={"subject": "x", "body": "", "category": "maintenance", "urgency": "low", "property_id": None, "unit_id": None})
        assert response.status_code == 401

    def test_creates_suggestion_successfully(self):
        with patch("handlers.automations.SessionLocal") as mock_sl:
            mock_sl.session_factory.return_value = self.db

            response = self._post({
                "subject": "Fix roof",
                "body": "The roof is leaking",
                "category": "maintenance",
                "urgency": "high",
                "property_id": None,
                "unit_id": None,
            })
        assert response.status_code == 200, response.json()
        data = response.json()
        assert data["ok"] is True
        assert "suggestion_id" in data

    def test_create_suggestion_seeds_context_message(self):
        """The context message must be persisted on the suggestion's AI conversation."""
        from db.models import Suggestion, Message

        with patch("handlers.automations.SessionLocal") as mock_sl:
            mock_sl.session_factory.return_value = self.db

            response = self._post({
                "subject": "Gutter cleaning due",
                "body": "Gutters at 123 Main St are due for cleaning.",
                "category": "maintenance",
                "urgency": "low",
                "property_id": None,
                "unit_id": None,
            })
        assert response.status_code == 200, response.json()

        suggestion = self.db.query(Suggestion).filter(Suggestion.title == "Gutter cleaning due").first()
        assert suggestion is not None, "Suggestion should exist in DB"
        assert suggestion.status == "pending"
        assert suggestion.ai_conversation_id is not None, "AI conversation should be set"

        msgs = self.db.query(Message).filter(
            Message.conversation_id == suggestion.ai_conversation_id,
            Message.message_type == "context",
        ).all()
        assert len(msgs) == 1, f"Expected 1 context message, got {len(msgs)}"
        assert "Gutters at 123 Main St" in msgs[0].body

    @patch("llm.vendor_outreach.generate_vendor_outreach", return_value=None)
    @patch("gql.services.settings_service.get_autonomy_for_category", return_value="manual")
    def test_suggestion_with_vendor_stores_payload(self, *_):
        """Vendor info should be stored in the suggestion's action_payload."""
        from db.models import Suggestion, ExternalContact

        vendor = ExternalContact(name="Ace Plumbing", role_label="plumber", extra={"contact_method": "email"})
        self.db.add(vendor)
        self.db.flush()
        vendor_id = vendor.id

        auto_cfg = {"checks": {"test_plumbing": {"preferred_vendor_id": vendor_id}}}

        with patch("handlers.automations.SessionLocal") as mock_sl, \
             patch("handlers.automations._load_automation_config", return_value=auto_cfg):
            mock_sl.session_factory.return_value = self.db

            response = self._post({
                "subject": "Fix kitchen faucet",
                "body": "The kitchen faucet is dripping.",
                "category": "plumbing",
                "urgency": "high",
                "automation_key": "test_plumbing",
            })
        assert response.status_code == 200, response.json()

        suggestion = self.db.query(Suggestion).filter(Suggestion.title == "Fix kitchen faucet").first()
        assert suggestion is not None
        assert suggestion.action_payload is not None
        assert suggestion.action_payload.get("vendor_id") == vendor_id
        assert suggestion.action_payload.get("vendor_name") == "Ace Plumbing"

    @patch("llm.vendor_outreach.generate_vendor_outreach", return_value="Hi, can you take this job?")
    @patch("gql.services.settings_service.get_autonomy_for_category", return_value="suggest")
    def test_suggest_mode_creates_approval_with_draft(self, *_):
        """In suggest mode, vendor draft should be in action_payload and approval message."""
        from db.models import Suggestion, Message, ExternalContact

        vendor = ExternalContact(name="Draft Vendor", extra={"contact_method": "email"})
        self.db.add(vendor)
        self.db.flush()
        vendor_id = vendor.id

        auto_cfg = {"checks": {"test_draft": {"preferred_vendor_id": vendor_id}}}

        with patch("handlers.automations.SessionLocal") as mock_sl, \
             patch("handlers.automations._load_automation_config", return_value=auto_cfg):
            mock_sl.session_factory.return_value = self.db
            response = self._post({
                "subject": "Suggest mode test",
                "body": "Test body.",
                "category": "maintenance",
                "urgency": "low",
                "automation_key": "test_draft",
            })
        assert response.status_code == 200, response.json()

        suggestion = self.db.query(Suggestion).filter(Suggestion.title == "Suggest mode test").first()
        assert suggestion is not None
        assert suggestion.status == "pending"

        # Draft should be stored in action_payload
        assert suggestion.action_payload.get("draft_message") == "Hi, can you take this job?"

        # Should have suggestion message
        suggestion_msg = self.db.query(Message).filter(
            Message.conversation_id == suggestion.ai_conversation_id,
            Message.message_type.in_(["suggestion", "approval"]),
        ).first()
        assert suggestion_msg is not None, "Expected a suggestion message"
        assert suggestion_msg.draft_reply == "Hi, can you take this job?"

        # Options should include vendor draft actions
        option_keys = [o["key"] for o in suggestion.options]
        assert "send" in option_keys
        assert "edit" in option_keys
        assert "skip" in option_keys

    @patch("llm.vendor_outreach.generate_vendor_outreach", return_value=None)
    @patch("gql.services.settings_service.get_autonomy_for_category", return_value="manual")
    def test_manual_mode_default_options(self, *_):
        """In manual mode, suggestion should have accept/reject options."""
        from db.models import Suggestion, Message, ExternalContact

        vendor = ExternalContact(name="Manual Vendor", extra={"contact_method": "email"})
        self.db.add(vendor)
        self.db.flush()
        vendor_id = vendor.id

        auto_cfg = {"checks": {"test_manual": {"preferred_vendor_id": vendor_id}}}

        with patch("handlers.automations.SessionLocal") as mock_sl, \
             patch("handlers.automations._load_automation_config", return_value=auto_cfg):
            mock_sl.session_factory.return_value = self.db
            response = self._post({
                "subject": "Manual mode test",
                "body": "Test body.",
                "category": "maintenance",
                "urgency": "low",
                "automation_key": "test_manual",
            })
        assert response.status_code == 200

        suggestion = self.db.query(Suggestion).filter(Suggestion.title == "Manual mode test").first()
        assert suggestion is not None

        # Only context message, no approval
        msgs = self.db.query(Message).filter(
            Message.conversation_id == suggestion.ai_conversation_id,
        ).all()
        assert len(msgs) == 1, f"Expected only context message, got {len(msgs)}"

        # Default accept/reject options
        option_keys = [o["key"] for o in suggestion.options]
        assert "accept" in option_keys
        assert "reject" in option_keys

    def test_suggestion_without_vendor_has_no_payload(self):
        """A suggestion without a vendor should have no action_payload."""
        from db.models import Suggestion

        with patch("handlers.automations.SessionLocal") as mock_sl:
            mock_sl.session_factory.return_value = self.db

            response = self._post({
                "subject": "Gutter cleaning seasonal",
                "body": "Schedule gutter cleaning for spring.",
                "category": "maintenance",
                "urgency": "low",
            })
        assert response.status_code == 200

        suggestion = self.db.query(Suggestion).filter(Suggestion.title == "Gutter cleaning seasonal").first()
        assert suggestion is not None
        assert suggestion.action_payload is None

    def test_returns_409_if_suggestion_already_exists(self):
        with patch("handlers.automations.SessionLocal") as mock_sl:
            mock_sl.session_factory.return_value = self.db

            # Create the first suggestion
            self._post({
                "subject": "Duplicate suggestion",
                "body": "Body",
                "category": "maintenance",
                "urgency": "low",
            })
            # Second call with same subject should 409
            response = self._post({
                "subject": "Duplicate suggestion",
                "body": "Body",
                "category": "maintenance",
                "urgency": "low",
            })
        assert response.status_code == 409


# ---------------------------------------------------------------------------
# GET /automations
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("db")
class TestGetAutomations(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        app.dependency_overrides[get_db] = lambda: self.db

    def tearDown(self):
        app.dependency_overrides = {}

    def test_requires_auth(self):
        assert self.client.get("/automations").status_code == 401

    def test_returns_checks_and_history(self):
        with patch("handlers.automations._load_automation_config") as mock_cfg:
            mock_cfg.return_value = {"checks": {}, "custom_meta": {}}
            response = self.client.get("/automations", headers=AUTH)
        assert response.status_code == 200
        data = response.json()
        assert "automations" in data


# ---------------------------------------------------------------------------
# DELETE /automations/{key}
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("db")
class TestDeleteAutomation(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        app.dependency_overrides[get_db] = lambda: self.db

    def tearDown(self):
        app.dependency_overrides = {}

    def test_cannot_delete_builtin(self):
        response = self.client.delete("/automations/lease_status", headers=AUTH)
        assert response.status_code == 400

    def test_delete_nonexistent_returns_404(self):
        with patch("handlers.automations._delete_custom_automation", side_effect=ValueError("not found")):
            response = self.client.delete("/automations/ghost_check", headers=AUTH)
        assert response.status_code == 404
