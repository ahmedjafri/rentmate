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
        stored = {"checks": {"overdue_rent": {"enabled": False}}}
        result = _merge_automation_config(stored)
        assert result["checks"]["overdue_rent"]["enabled"] is False

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

    def _post(self, payload, headers=None):
        return self.client.post(
            "/automations/simulate/create-task",
            json=payload,
            headers=headers or AUTH,
        )

    def test_requires_auth(self):
        response = self.client.post("/automations/simulate/create-task", json={"subject": "x", "body": "", "category": "maintenance", "urgency": "low", "property_id": None, "unit_id": None})
        assert response.status_code == 401

    def test_creates_task_successfully(self):
        with patch("handlers.automations.SessionLocal") as mock_sl:
            mock_db = MagicMock()
            mock_sl.session_factory.return_value = mock_db
            mock_db.query.return_value.filter.return_value.first.return_value = None

            response = self._post({
                "subject": "Fix roof",
                "body": "The roof is leaking",
                "category": "maintenance",
                "urgency": "high",
                "property_id": None,
                "unit_id": None,
            })
        assert response.status_code == 200
        assert response.json() == {"ok": True}

    def test_returns_409_if_task_already_exists(self):
        with patch("handlers.automations.SessionLocal") as mock_sl:
            mock_db = MagicMock()
            mock_sl.session_factory.return_value = mock_db
            mock_db.query.return_value.filter.return_value.first.return_value = MagicMock()

            response = self._post({
                "subject": "Duplicate task",
                "body": "Body",
                "category": "maintenance",
                "urgency": "low",
                "property_id": None,
                "unit_id": None,
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
        response = self.client.delete("/automations/overdue_rent", headers=AUTH)
        assert response.status_code == 400

    def test_delete_nonexistent_returns_404(self):
        with patch("handlers.automations._delete_custom_automation", side_effect=ValueError("not found")):
            response = self.client.delete("/automations/ghost_check", headers=AUTH)
        assert response.status_code == 404
