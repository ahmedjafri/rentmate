"""Tests for the confidence-based action gate in agent.dispatch."""
import asyncio
import json
from unittest.mock import patch

import pytest

from agent.dispatch import dispatch
from agent.uncertainty import (
    GATED_TOOLS,
    check_action_confidence,
    confidence_threshold_for,
)
from db.enums import ActionPolicyLevel


def _set_policy(
    level: ActionPolicyLevel,
    *,
    field: str = "task_suggestion_creation",
):
    """Override one policy field's level by patching the settings reader
    at the agent.uncertainty import site (the symbol was pulled in at
    module-import time, so patching the source module wouldn't apply)."""
    return patch(
        "agent.uncertainty.get_action_policy_settings",
        return_value={
            "entity_changes": ActionPolicyLevel.BALANCED,
            "outbound_messages": ActionPolicyLevel.BALANCED,
            "task_suggestion_creation": ActionPolicyLevel.BALANCED,
            field: level,
        },
    )


def test_threshold_mapping_reads_each_policy_field():
    for field in ("entity_changes", "outbound_messages", "task_suggestion_creation"):
        with _set_policy(ActionPolicyLevel.AGGRESSIVE, field=field):
            assert confidence_threshold_for(field) == 0.3
        with _set_policy(ActionPolicyLevel.BALANCED, field=field):
            assert confidence_threshold_for(field) == 0.6
        with _set_policy(ActionPolicyLevel.STRICT, field=field):
            assert confidence_threshold_for(field) == 0.8


def test_message_person_gated_by_outbound_messages_policy():
    with _set_policy(ActionPolicyLevel.STRICT, field="outbound_messages"):
        assert check_action_confidence(
            tool_name="message_person",
            args={"confidence": 0.5},
        ) is not None
        assert check_action_confidence(
            tool_name="message_person",
            args={"confidence": 0.9},
        ) is None


def test_create_property_gated_by_entity_changes_policy():
    with _set_policy(ActionPolicyLevel.STRICT, field="entity_changes"):
        msg = check_action_confidence(
            tool_name="create_property",
            args={"confidence": 0.5},
        )
        assert msg is not None
        assert "entity_changes" in msg


def test_outbound_strict_does_not_affect_creation_calls():
    """Each tool consults its own policy field — no cross-contamination."""
    with _set_policy(ActionPolicyLevel.STRICT, field="outbound_messages"):
        # task_suggestion_creation is BALANCED in this fixture (0.6),
        # so 0.7 should pass for create_suggestion even though
        # outbound_messages is STRICT.
        assert check_action_confidence(
            tool_name="create_suggestion",
            args={"confidence": 0.7},
        ) is None


def test_non_gated_tool_never_blocked():
    with _set_policy(ActionPolicyLevel.STRICT):
        assert check_action_confidence(
            tool_name="lookup_vendors",
            args={"confidence": 0.0},
        ) is None


def test_high_confidence_passes_under_strict():
    with _set_policy(ActionPolicyLevel.STRICT):
        assert check_action_confidence(
            tool_name="propose_task",
            args={"confidence": 0.9},
        ) is None


def test_low_confidence_blocked_under_strict():
    with _set_policy(ActionPolicyLevel.STRICT):
        msg = check_action_confidence(
            tool_name="propose_task",
            args={"confidence": 0.5},
        )
        assert msg is not None
        assert "task_suggestion_creation" in msg
        assert "ask_manager" in msg  # error tells agent which tool to use instead
        assert "0.50" in msg
        assert "0.80" in msg


def test_missing_confidence_treated_as_0_5():
    with _set_policy(ActionPolicyLevel.STRICT):
        # 0.5 is below 0.8, should block
        assert check_action_confidence(
            tool_name="create_suggestion",
            args={},
        ) is not None
    with _set_policy(ActionPolicyLevel.AGGRESSIVE):
        # 0.5 is above 0.3, should pass
        assert check_action_confidence(
            tool_name="create_suggestion",
            args={},
        ) is None


def test_string_confidence_coerced():
    with _set_policy(ActionPolicyLevel.BALANCED):
        # "0.9" coerces to 0.9 ≥ 0.6
        assert check_action_confidence(
            tool_name="create_suggestion",
            args={"confidence": "0.9"},
        ) is None


def test_garbage_confidence_falls_to_default():
    with _set_policy(ActionPolicyLevel.STRICT):
        # Unparseable → default 0.5 < 0.8 → blocked
        assert check_action_confidence(
            tool_name="create_suggestion",
            args={"confidence": "high"},
        ) is not None


def test_dispatch_blocks_low_confidence_call():
    """End-to-end through dispatch — the agent's tool entrypoint."""
    with _set_policy(ActionPolicyLevel.STRICT):
        result = asyncio.run(dispatch(
            "create_suggestion",
            {"confidence": 0.2, "title": "x", "body": "y", "suggestion_type": "compliance", "risk_score": 5},
        ))
        payload = json.loads(result)
        assert payload["status"] == "error"
        assert "ask_manager" in payload["message"]
        assert "0.20" in payload["message"]


def test_dispatch_unknown_tool_unaffected():
    with _set_policy(ActionPolicyLevel.STRICT):
        result = asyncio.run(dispatch("does_not_exist", {"confidence": 0.0}))
        payload = json.loads(result)
        assert payload["status"] == "error"
        assert "unknown tool" in payload["error"]


def test_gated_tools_set_is_documented():
    # If you add a gated tool, also update its parameter schema to require
    # ``confidence`` and update this assertion.
    assert GATED_TOOLS == frozenset({
        "create_suggestion",
        "propose_task",
        "message_person",
        "create_property",
        "create_tenant",
        "create_lease",
        "update_lease",
        "create_vendor",
    })
