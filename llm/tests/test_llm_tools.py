"""Tests for llm/agent_query.py, llm/agent_action.py, and llm/agent_data.py."""
import asyncio
import json
import os
import sys
import types
from datetime import UTC, date, datetime
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

    def test_rejects_pg_catalog(self):
        err = _validate_sql("SELECT * FROM pg_catalog.pg_tables")
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


def test_chat_with_agent_propagates_simulation_context_into_executor_thread():
    from llm.client import chat_with_agent
    from llm.tools import simulation_suggestions

    class FakeAIAgent:
        def __init__(self, *args, **kwargs):
            self.tools = []

        def _build_api_kwargs(self, messages):
            return {}

        def run_conversation(self, **kwargs):
            pending = simulation_suggestions.get()
            assert pending is not None
            pending.append({"title": "simulated from thread"})
            return {"final_response": "ok"}

    fake_module = types.SimpleNamespace(AIAgent=FakeAIAgent)
    token = simulation_suggestions.set([])
    try:
        with patch.dict(sys.modules, {"run_agent": fake_module}), \
             patch("llm.client.agent_registry.build_system_prompt", return_value="system"):
            reply = asyncio.run(chat_with_agent("agent-1", "simulate:test", [{"role": "user", "content": "hi"}]))
        assert reply == "ok"
        assert simulation_suggestions.get() == [{"title": "simulated from thread"}]
    finally:
        simulation_suggestions.reset(token)


def test_chat_with_agent_logs_tool_traces_with_conversation_id():
    from llm.client import chat_with_agent

    logged: list[dict] = []

    class FakeAIAgent:
        def __init__(self, *args, **kwargs):
            self.tools = []
            self.tool_progress_callback = kwargs["tool_progress_callback"]

        def _build_api_kwargs(self, messages):
            return {}

        def run_conversation(self, **kwargs):
            self.tool_progress_callback(
                "tool.started",
                "create_suggestion",
                None,
                {"title": "Draft notice"},
            )
            self.tool_progress_callback(
                "tool.completed",
                "create_suggestion",
                None,
                {"title": "Draft notice"},
                result='{"status":"ok"}',
                is_error=False,
            )
            return {"final_response": "ok"}

    fake_module = types.SimpleNamespace(AIAgent=FakeAIAgent)
    try:
        with patch.dict(sys.modules, {"run_agent": fake_module}), \
             patch("llm.client.agent_registry.build_system_prompt", return_value="system"), \
             patch("llm.client.log_trace", side_effect=lambda *args, **kwargs: logged.append({"args": args, "kwargs": kwargs})):
            reply = asyncio.run(chat_with_agent(
                "agent-1",
                "chat:21",
                [{"role": "user", "content": "hi"}],
                trace_context={"conversation_id": "21"},
            ))
        assert reply == "ok"
        assert len(logged) == 2
        assert logged[0]["args"][0] == "tool_call"
        assert logged[0]["kwargs"]["conversation_id"] == "21"
        assert logged[1]["args"][0] == "tool_result"
        assert logged[1]["kwargs"]["conversation_id"] == "21"
    finally:
        sys.modules.pop("run_agent", None)


def test_chat_with_agent_passes_workspace_scoped_hermes_home(tmp_path):
    from llm.client import chat_with_agent

    captured: dict[str, object] = {}

    class FakeAIAgent:
        def __init__(self, *args, **kwargs):
            self.tools = []
            captured["hermes_home"] = kwargs.get("hermes_home")

        def _build_api_kwargs(self, messages):
            return {}

        def run_conversation(self, **kwargs):
            return {"final_response": "ok"}

    fake_module = types.SimpleNamespace(AIAgent=FakeAIAgent)
    with (
        patch.dict(sys.modules, {"run_agent": fake_module}),
        patch("llm.client.agent_registry.build_system_prompt", return_value="system"),
        patch("llm.client.ensure_agent_runtime_dirs") as mock_runtime_dirs,
    ):
        hermes_home = tmp_path / "agent-1"
        mock_runtime_dirs.return_value = {
            "workspace": hermes_home,
            "hermes_home": hermes_home,
            "working_dir": tmp_path / "agent-1" / "home",
        }
        reply = asyncio.run(chat_with_agent("agent-1", "chat:21", [{"role": "user", "content": "hi"}]))

    assert reply == "ok"
    assert captured["hermes_home"] == hermes_home


def test_load_onboarding_prompt_only_for_active_chat_sessions():
    from llm.client import _load_onboarding_prompt

    fake_db = MagicMock()
    fake_session_local = MagicMock(return_value=fake_db)

    with (
        patch("llm.client._ONBOARDING_PROMPT_PATH.read_text", return_value="## Onboarding Mode\nUse onboarding rules."),
        patch("db.session.SessionLocal", fake_session_local),
        patch("gql.services.settings_service.get_onboarding_state", return_value={"status": "active"}),
    ):
        prompt = _load_onboarding_prompt(session_key="chat:21")

    assert "Onboarding Mode" in prompt
    fake_db.close.assert_called_once()


def test_load_onboarding_prompt_skips_non_chat_or_inactive_sessions():
    from llm.client import _load_onboarding_prompt

    fake_db = MagicMock()
    fake_session_local = MagicMock(return_value=fake_db)

    with (
        patch("llm.client._ONBOARDING_PROMPT_PATH.read_text", return_value="## Onboarding Mode\nUse onboarding rules."),
        patch("db.session.SessionLocal", fake_session_local),
        patch("gql.services.settings_service.get_onboarding_state", return_value={"status": "completed"}),
    ):
        inactive_prompt = _load_onboarding_prompt(session_key="chat:21")
        task_prompt = _load_onboarding_prompt(session_key="task:abc")

    assert inactive_prompt == ""
    assert task_prompt == ""


def test_local_fallback_retries_when_reply_claims_document_without_tool_call():
    from llm.client import _local_fallback
    from llm.tools import pending_suggestion_messages

    calls: list[list[dict]] = []

    async def fake_chat_with_agent(agent_id, session_key, messages, on_progress=None, trace_context=None):
        calls.append(messages)
        if len(calls) == 1:
            return "I've created a new notice document. It is available in your Documents area."
        pending_suggestion_messages.set([
            {
                "type": "chat_message",
                "body": "Created document: Notice-1.pdf",
                "meta": {
                    "action_card": {
                        "kind": "document",
                        "title": "Notice-1.pdf",
                    },
                },
            },
        ])
        return "Created document: Notice-1.pdf"

    with patch("llm.client.chat_with_agent", side_effect=fake_chat_with_agent), \
         patch("llm.client.set_fallback_request_context", return_value=object()), \
         patch("llm.client.reset_fallback_request_context"), \
         patch("llm.client.resolve_account_id", return_value=1), \
         patch("llm.client.resolve_org_id", return_value=1):
        result = asyncio.run(_local_fallback(
            "agent-1",
            "chat:21",
            [{"role": "user", "content": "Create a brand new 14-day notice document"}],
            trace_context={"conversation_id": "21"},
        ))

    assert len(calls) == 2
    assert "System correction:" in calls[1][-1]["content"]
    assert result.reply == "Created document: Notice-1.pdf"
    assert result.side_effects[0]["meta"]["action_card"]["kind"] == "document"


def test_local_fallback_does_not_retry_after_non_document_side_effects_already_exist():
    from llm.client import _local_fallback
    from llm.tools import pending_suggestion_messages

    calls: list[list[dict]] = []

    async def fake_chat_with_agent(agent_id, session_key, messages, on_progress=None, trace_context=None):
        calls.append(messages)
        pending_suggestion_messages.set([
            {
                "type": "chat_message",
                "body": "Created property 1234 Acme Lane",
                "meta": {
                    "action_card": {
                        "kind": "property",
                        "title": "1234 Acme Lane",
                    },
                },
            },
        ])
        return "I've created a new notice document. It is available in your Documents area."

    with patch("llm.client.chat_with_agent", side_effect=fake_chat_with_agent), \
         patch("llm.client.set_fallback_request_context", return_value=object()), \
         patch("llm.client.reset_fallback_request_context"), \
         patch("llm.client.resolve_account_id", return_value=1), \
         patch("llm.client.resolve_org_id", return_value=1):
        result = asyncio.run(_local_fallback(
            "agent-1",
            "chat:21",
            [{"role": "user", "content": "Create the records from this lease"}],
            trace_context={"conversation_id": "21"},
        ))

    assert len(calls) == 1
    assert result.reply == "I created the property record from the lease."
    assert result.side_effects[0]["meta"]["action_card"]["kind"] == "property"


def test_local_fallback_blocks_switch_to_different_mutating_tool_after_failure():
    from llm.client import _local_fallback, current_completed_tools, current_failed_tools
    from llm.tools import pending_suggestion_messages

    calls: list[list[dict]] = []

    async def fake_chat_with_agent(agent_id, session_key, messages, on_progress=None, trace_context=None):
        calls.append(messages)
        if len(calls) == 1:
            current_failed_tools.set([
                {"tool_name": "create_document", "error": "renderer crashed", "args": {"title": "Notice"}},
            ])
            current_completed_tools.set(["create_suggestion"])
            pending_suggestion_messages.set([
                {
                    "type": "chat_message",
                    "body": "Create 14-Day Pay or Vacate Notice",
                    "related_task_ids": {"suggestion_id": 11},
                    "meta": {
                        "action_card": {
                            "kind": "suggestion",
                            "title": "Create 14-Day Pay or Vacate Notice",
                        },
                    },
                },
            ])
            return "I've created a suggestion for you to review."
        current_failed_tools.set([
            {"tool_name": "create_document", "error": "renderer crashed", "args": {"title": "Notice"}},
        ])
        current_completed_tools.set([])
        pending_suggestion_messages.set([])
        return "The document tool failed, so there is no new document."

    with patch("llm.client.chat_with_agent", side_effect=fake_chat_with_agent), \
         patch("llm.client.set_fallback_request_context", return_value=object()), \
         patch("llm.client.reset_fallback_request_context"), \
         patch("llm.client.resolve_account_id", return_value=1), \
         patch("llm.client.resolve_org_id", return_value=1):
        result = asyncio.run(_local_fallback(
            "agent-1",
            "chat:21",
            [{"role": "user", "content": "Create a brand new 14-day notice document"}],
            trace_context={"conversation_id": "21"},
        ))

    assert len(calls) == 2
    assert "Do not switch from create_document to a different mutating tool such as create_suggestion" in calls[1][-1]["content"]
    assert result.reply == "The document tool failed, so there is no new document."
    assert result.side_effects == []


def test_local_fallback_allows_retry_of_same_mutating_tool_after_failure():
    from llm.client import _local_fallback, current_completed_tools, current_failed_tools
    from llm.tools import pending_suggestion_messages

    calls: list[list[dict]] = []

    async def fake_chat_with_agent(agent_id, session_key, messages, on_progress=None, trace_context=None):
        calls.append(messages)
        current_failed_tools.set([
            {"tool_name": "create_document", "error": "temporary renderer error", "args": {"title": "Notice"}},
        ])
        current_completed_tools.set(["create_document"])
        pending_suggestion_messages.set([
            {
                "type": "chat_message",
                "body": "Created document: Notice-2.pdf",
                "meta": {
                    "action_card": {
                        "kind": "document",
                        "title": "Notice-2.pdf",
                    },
                },
            },
        ])
        return "Created document: Notice-2.pdf"

    with patch("llm.client.chat_with_agent", side_effect=fake_chat_with_agent), \
         patch("llm.client.set_fallback_request_context", return_value=object()), \
         patch("llm.client.reset_fallback_request_context"), \
         patch("llm.client.resolve_account_id", return_value=1), \
         patch("llm.client.resolve_org_id", return_value=1):
        result = asyncio.run(_local_fallback(
            "agent-1",
            "chat:21",
            [{"role": "user", "content": "Create a brand new 14-day notice document"}],
            trace_context={"conversation_id": "21"},
        ))

    assert len(calls) == 1
    assert result.reply == "Created document: Notice-2.pdf"
    assert result.side_effects[0]["meta"]["action_card"]["kind"] == "document"


def test_local_fallback_synthesizes_reply_when_tool_fails_and_model_returns_no_message():
    from llm.client import _local_fallback, current_completed_tools, current_failed_tools
    from llm.tools import pending_suggestion_messages

    async def fake_chat_with_agent(agent_id, session_key, messages, on_progress=None, trace_context=None):
        current_failed_tools.set([
            {
                "tool_name": "create_document",
                "error": "browserType.launch: chromium crashed with signal SIGTRAP",
                "args": {"title": "Notice"},
            },
        ])
        current_completed_tools.set([])
        pending_suggestion_messages.set([])
        return ""

    with patch("llm.client.chat_with_agent", side_effect=fake_chat_with_agent), \
         patch("llm.client.set_fallback_request_context", return_value=object()), \
         patch("llm.client.reset_fallback_request_context"), \
         patch("llm.client.resolve_account_id", return_value=1), \
         patch("llm.client.resolve_org_id", return_value=1):
        result = asyncio.run(_local_fallback(
            "agent-1",
            "chat:21",
            [{"role": "user", "content": "Create a brand new 14-day notice document"}],
            trace_context={"conversation_id": "21"},
        ))

    assert result.reply == "Creating document failed: browserType.launch: chromium crashed with signal SIGTRAP"
    assert result.side_effects == []


def test_local_fallback_synthesizes_reply_when_model_returns_only_tool_progress():
    from llm.client import _local_fallback, current_completed_tools, current_failed_tools
    from llm.tools import pending_suggestion_messages

    async def fake_chat_with_agent(agent_id, session_key, messages, on_progress=None, trace_context=None):
        current_failed_tools.set([
            {
                "tool_name": "create_document",
                "error": "browserType.launch: chromium crashed with signal SIGTRAP",
                "args": {"title": "Notice"},
            },
        ])
        current_completed_tools.set([])
        pending_suggestion_messages.set([])
        return "Creating document\nCreating document: error"

    with patch("llm.client.chat_with_agent", side_effect=fake_chat_with_agent), \
         patch("llm.client.set_fallback_request_context", return_value=object()), \
         patch("llm.client.reset_fallback_request_context"), \
         patch("llm.client.resolve_account_id", return_value=1), \
         patch("llm.client.resolve_org_id", return_value=1):
        result = asyncio.run(_local_fallback(
            "agent-1",
            "chat:21",
            [{"role": "user", "content": "Create a brand new 14-day notice document"}],
            trace_context={"conversation_id": "21"},
        ))

    assert result.reply == "Creating document failed: browserType.launch: chromium crashed with signal SIGTRAP"
    assert result.side_effects == []


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
    c.created_at = datetime.now(UTC)
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
        msg.sent_at = datetime.now(UTC)
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
        msg.sent_at = datetime.now(UTC)
        result = _serialize_messages([msg], "conv-1")
        assert isinstance(result, list)
        assert result[0]["body"] == "Test"
