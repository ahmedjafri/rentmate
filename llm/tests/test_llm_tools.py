"""Tests for llm/agent_query.py, llm/agent_action.py, and llm/agent_data.py."""
import json
import os
import sys
import tempfile
import uuid
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# agent_query.py — _validate_sql
# ---------------------------------------------------------------------------
from llm.agent_query import _validate_sql


class TestValidateSql:
    def test_allows_select(self):
        assert _validate_sql("SELECT * FROM tenants") is None

    def test_allows_with_select(self):
        assert _validate_sql("WITH cte AS (SELECT 1) SELECT * FROM cte") is None

    def test_rejects_insert(self):
        err = _validate_sql("INSERT INTO tenants VALUES (1)")
        assert err is not None
        assert "SELECT" in err

    def test_rejects_update(self):
        err = _validate_sql("UPDATE tenants SET name='x'")
        assert err is not None

    def test_rejects_delete(self):
        err = _validate_sql("DELETE FROM tenants")
        assert err is not None

    def test_rejects_information_schema(self):
        err = _validate_sql("SELECT * FROM information_schema.tables")
        assert err is not None
        assert "blocked" in err.lower()

    def test_rejects_sqlite_master(self):
        err = _validate_sql("SELECT * FROM sqlite_master")
        assert err is not None

    def test_rejects_semicolon(self):
        err = _validate_sql("SELECT 1; DROP TABLE tenants")
        assert err is not None

    def test_case_insensitive_select(self):
        assert _validate_sql("select id from tenants") is None

    def test_case_insensitive_information_schema(self):
        err = _validate_sql("SELECT * FROM INFORMATION_SCHEMA.tables")
        assert err is not None

    def test_leading_whitespace_allowed(self):
        assert _validate_sql("   SELECT 1") is None


# ---------------------------------------------------------------------------
# agent_action.py — _queue_action
# ---------------------------------------------------------------------------

from llm.agent_action import _queue_action


class TestQueueAction:
    def test_writes_to_file_when_workspace_set(self, tmp_path):
        action = {"action": "propose_task", "title": "Fix leak"}
        with patch.dict(os.environ, {"RENTMATE_AGENT_WORKSPACE": str(tmp_path)}):
            _queue_action(action)
        actions_file = tmp_path / "pending_actions.jsonl"
        assert actions_file.exists()
        lines = actions_file.read_text().strip().split("\n")
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["action"] == "propose_task"
        assert parsed["title"] == "Fix leak"

    def test_appends_multiple_actions(self, tmp_path):
        with patch.dict(os.environ, {"RENTMATE_AGENT_WORKSPACE": str(tmp_path)}):
            _queue_action({"action": "a1"})
            _queue_action({"action": "a2"})
        actions_file = tmp_path / "pending_actions.jsonl"
        lines = actions_file.read_text().strip().split("\n")
        assert len(lines) == 2

    def test_no_op_when_workspace_not_set(self, tmp_path):
        env = {k: v for k, v in os.environ.items() if k != "RENTMATE_AGENT_WORKSPACE"}
        with patch.dict(os.environ, env, clear=True):
            _queue_action({"action": "test"})  # Should not raise


# ---------------------------------------------------------------------------
# agent_data.py — serializers
# ---------------------------------------------------------------------------

from db.models import ParticipantType
from llm.agent_data import (
    _serialize_leases,
    _serialize_messages,
    _serialize_properties,
    _serialize_task,
    _serialize_tasks,
    _serialize_tenants,
)


def _mock_property(prop_id="prop-1", name="Test Property"):
    p = MagicMock()
    p.id = prop_id
    p.name = name
    p.address_line1 = "123 Main St"
    p.address_line2 = None
    p.city = "Toronto"
    p.state = None
    p.postal_code = "M5V 1A1"
    p.country = None
    p.property_type = "residential"
    p.units = []
    p.leases = []
    return p


def _mock_tenant(tenant_id="tenant-1"):
    t = MagicMock()
    t.id = tenant_id
    t.first_name = "Alice"
    t.last_name = "Smith"
    t.email = "alice@example.com"
    t.phone = "+14165551234"
    t.leases = []
    return t


def _mock_lease(lease_id="lease-1", end_date=None):
    l = MagicMock()
    l.id = lease_id
    l.start_date = date(2024, 1, 1)
    l.end_date = end_date or date(2099, 12, 31)
    l.rent_amount = 1500.0
    l.payment_status = "current"
    l.tenant = _mock_tenant()
    l.unit = MagicMock()
    l.unit.id = "unit-1"
    l.unit.label = "1A"
    l.property = _mock_property()
    l.property_id = "prop-1"
    l.unit_id = "unit-1"
    return l


def _mock_task(task_id="task-1"):
    c = MagicMock()
    c.id = task_id
    c.subject = "Fix roof"
    c.task_status = "open"
    c.category = "maintenance"
    c.urgency = "high"
    c.priority = 1
    c.source = "manual"
    c.property_id = "prop-1"
    c.created_at = datetime.utcnow()
    c.last_message_at = None
    c.lease = None
    c.unit = None
    c.messages = []
    return c


class TestSerializeProperties:
    def test_empty_list(self):
        result = _serialize_properties([])
        assert result == []

    def test_basic_property(self):
        p = _mock_property()
        result = _serialize_properties([p])
        assert len(result) == 1
        assert result[0]["id"] == "prop-1"
        assert result[0]["total_units"] == 0
        assert result[0]["occupied_units"] == 0
        assert result[0]["monthly_revenue"] == 0.0

    def test_property_with_active_lease(self):
        p = _mock_property()
        lease = _mock_lease()
        lease.unit_id = "unit-1"
        p.leases = [lease]
        unit = MagicMock()
        unit.id = "unit-1"
        unit.label = "1A"
        p.units = [unit]
        result = _serialize_properties([p])
        assert result[0]["occupied_units"] == 1
        assert result[0]["monthly_revenue"] == 1500.0

    def test_property_with_expired_lease(self):
        p = _mock_property()
        lease = _mock_lease(end_date=date(2020, 1, 1))
        p.leases = [lease]
        result = _serialize_properties([p])
        assert result[0]["occupied_units"] == 0
        assert result[0]["monthly_revenue"] == 0.0


class TestSerializeTenants:
    def test_empty_list(self):
        assert _serialize_tenants([]) == []

    def test_tenant_without_leases(self):
        t = _mock_tenant()
        t.leases = []
        result = _serialize_tenants([t])
        assert result[0]["id"] == "tenant-1"
        assert result[0]["is_active"] is False
        assert result[0]["unit"] is None

    def test_tenant_with_active_lease(self):
        t = _mock_tenant()
        lease = _mock_lease()
        t.leases = [lease]
        result = _serialize_tenants([t])
        assert result[0]["is_active"] is True
        assert result[0]["rent_amount"] == 1500.0

    def test_tenant_with_expired_lease(self):
        t = _mock_tenant()
        lease = _mock_lease(end_date=date(2020, 1, 1))
        t.leases = [lease]
        result = _serialize_tenants([t])
        assert result[0]["is_active"] is False


class TestSerializeLeases:
    def test_empty_list(self):
        assert _serialize_leases([]) == []

    def test_active_lease(self):
        l = _mock_lease()
        result = _serialize_leases([l])
        assert result[0]["id"] == "lease-1"
        assert result[0]["is_active"] is True
        assert result[0]["rent_amount"] == 1500.0

    def test_expired_lease(self):
        l = _mock_lease(end_date=date(2020, 1, 1))
        result = _serialize_leases([l])
        assert result[0]["is_active"] is False


class TestSerializeTasks:
    def test_empty_list(self):
        assert _serialize_tasks([]) == []

    def test_basic_task(self):
        c = _mock_task()
        result = _serialize_tasks([c])
        assert result[0]["id"] == "task-1"
        assert result[0]["title"] == "Fix roof"
        assert result[0]["status"] == "open"
        assert result[0]["category"] == "maintenance"


class TestSerializeTask:
    def test_none_returns_error(self):
        result = _serialize_task(None)
        assert "error" in result

    def test_basic_task(self):
        c = _mock_task()
        result = _serialize_task(c)
        assert result["id"] == "task-1"
        assert result["messages"] == []

    def test_task_with_messages(self):
        c = _mock_task()
        msg = MagicMock()
        msg.id = "msg-1"
        msg.body = "Hello"
        msg.message_type = "text"
        msg.sender_name = "Alice"
        msg.is_ai = False
        msg.sent_at = datetime.utcnow()
        msg.sender_type = ParticipantType.TENANT
        c.messages = [msg]
        result = _serialize_task(c)
        assert len(result["messages"]) == 1
        assert result["messages"][0]["body"] == "Hello"


class TestSerializeMessages:
    def test_empty_returns_error(self):
        result = _serialize_messages([], "conv-1")
        assert "error" in result

    def test_messages_serialized(self):
        msg = MagicMock()
        msg.id = "msg-1"
        msg.body = "Test"
        msg.message_type = "text"
        msg.sender_name = "Bob"
        msg.is_ai = False
        msg.sent_at = datetime.utcnow()
        result = _serialize_messages([msg], "conv-1")
        assert isinstance(result, list)
        assert result[0]["body"] == "Test"
