"""Tests for the in-house AgentLoop and dispatch module."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent.dispatch import dispatch, get_tool, tool_definitions
from agent.loop import AgentLoop, TenantIsolationError
from integrations.local_auth import (
    reset_request_context,
    resolve_account_id,
    set_request_context,
)

# ─── Dispatch tests ──────────────────────────────────────────────────────────


def test_tool_definitions_shape():
    defs = tool_definitions()
    assert defs, "should have registered tools"
    for d in defs:
        assert d["type"] == "function"
        fn = d["function"]
        assert "name" in fn and "description" in fn and "parameters" in fn


def test_get_tool_returns_known_tool():
    tool = get_tool("has_happened")
    assert tool is not None
    assert tool.name == "has_happened"


def test_dispatch_unknown_tool_returns_error():
    result = json.loads(asyncio.run(dispatch("does_not_exist", {})))
    assert result["status"] == "error"
    assert "unknown tool" in result["error"]


def test_dispatch_executes_read_only_tool():
    # has_happened is a pure read-only tool — no DB, no contextvars required.
    result = json.loads(
        asyncio.run(dispatch("has_happened", {"timestamp": "2099-01-01T00:00:00Z"}))
    )
    assert result["has_happened"] is False


def test_dispatch_simulation_gate_blackholes_read_write():
    from agent.tools._common import simulation_suggestions

    token = simulation_suggestions.set([])
    try:
        result = json.loads(asyncio.run(dispatch("propose_task", {"title": "x"})))
    finally:
        simulation_suggestions.reset(token)
    assert result["status"] == "ok"
    assert "simulation" in result["message"].lower()


# ─── AgentLoop helpers: build a fake litellm.acompletion ─────────────────────


def _msg(content: str, tool_calls=None):
    return SimpleNamespace(
        content=content,
        tool_calls=tool_calls,
    )


def _tc(call_id: str, name: str, args: dict | str):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(
            name=name,
            arguments=args if isinstance(args, str) else json.dumps(args),
        ),
    )


def _resp(message, prompt_tokens=10, completion_tokens=5):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        ),
    )


@pytest.fixture
def auth_ctx():
    tokens = set_request_context(account_id=1, org_id=1)
    yield 1
    reset_request_context(tokens)


# ─── AgentLoop tests ─────────────────────────────────────────────────────────


def test_loop_returns_final_text_when_no_tool_calls(auth_ctx):
    async def fake_acompletion(**kwargs):
        return _resp(_msg("Hello world"))

    with patch("agent.loop.litellm.acompletion", side_effect=fake_acompletion):
        loop = AgentLoop(
            model="anthropic/test",
            system_message="sys",
            account_id=auth_ctx,
        )
        result = asyncio.run(loop.run(user_message="hi"))

    assert result["final_response"] == "Hello world"
    assert result["api_calls"] == 1
    assert result["input_tokens"] == 10
    assert result["output_tokens"] == 5
    assert result["completed"] == []


def test_loop_dispatches_tool_then_returns_final_text(auth_ctx):
    seq = iter(
        [
            _resp(
                _msg(
                    "thinking…",
                    tool_calls=[
                        _tc("call_1", "has_happened", {"timestamp": "2099-01-01T00:00:00Z"})
                    ],
                )
            ),
            _resp(_msg("Done — that's in the future.")),
        ]
    )

    async def fake_acompletion(**kwargs):
        return next(seq)

    with patch("agent.loop.litellm.acompletion", side_effect=fake_acompletion):
        loop = AgentLoop(
            model="anthropic/test",
            system_message="sys",
            account_id=auth_ctx,
        )
        result = asyncio.run(loop.run(user_message="is 2099 in the past?"))

    assert result["api_calls"] == 2
    assert result["completed"] == ["has_happened"]
    assert result["final_response"] == "Done — that's in the future."
    tool_msg = next(m for m in result["messages"] if m.get("role") == "tool")
    payload = json.loads(tool_msg["content"])
    assert payload["has_happened"] is False


def test_loop_progress_and_complete_callbacks_fire(auth_ctx):
    progress_events = []
    complete_events = []

    seq = iter(
        [
            _resp(
                _msg(
                    None,
                    tool_calls=[_tc("c1", "has_happened", {"timestamp": "2099-01-01T00:00:00Z"})],
                )
            ),
            _resp(_msg("ok")),
        ]
    )

    async def fake_acompletion(**kwargs):
        return next(seq)

    def on_progress(event_type, tool_name, preview, args, **kwargs):
        progress_events.append((event_type, tool_name))

    def on_complete(call_id, name, args, result):
        complete_events.append((call_id, name, json.loads(result)["has_happened"]))

    with patch("agent.loop.litellm.acompletion", side_effect=fake_acompletion):
        loop = AgentLoop(
            model="anthropic/test",
            system_message="sys",
            account_id=auth_ctx,
            tool_progress_callback=on_progress,
            tool_complete_callback=on_complete,
        )
        asyncio.run(loop.run(user_message="x"))

    assert ("tool.started", "has_happened") in progress_events
    assert ("tool.completed", "has_happened") in progress_events
    assert complete_events == [("c1", "has_happened", False)]


def test_loop_max_iterations_returns_apology(auth_ctx):
    # Always return tool calls so the loop never terminates naturally.
    async def fake_acompletion(**kwargs):
        return _resp(
            _msg(
                None,
                tool_calls=[_tc("c", "has_happened", {"timestamp": "2099-01-01T00:00:00Z"})],
            )
        )

    with patch("agent.loop.litellm.acompletion", side_effect=fake_acompletion):
        loop = AgentLoop(
            model="anthropic/test",
            system_message="sys",
            account_id=auth_ctx,
            max_iterations=3,
        )
        result = asyncio.run(loop.run(user_message="x"))

    assert result["api_calls"] == 3
    assert result["final_response"].startswith("I apologize")


def test_loop_handles_unparseable_tool_args(auth_ctx):
    seq = iter(
        [
            _resp(
                _msg(None, tool_calls=[_tc("c", "has_happened", "{not json}")])
            ),
            _resp(_msg("done")),
        ]
    )

    async def fake_acompletion(**kwargs):
        return next(seq)

    with patch("agent.loop.litellm.acompletion", side_effect=fake_acompletion):
        loop = AgentLoop(
            model="anthropic/test",
            system_message="sys",
            account_id=auth_ctx,
        )
        result = asyncio.run(loop.run(user_message="x"))

    # Tool dispatched with empty args; has_happened returns an error JSON for missing timestamp
    tool_msg = next(m for m in result["messages"] if m.get("role") == "tool")
    payload = json.loads(tool_msg["content"])
    assert "error" in payload


# ─── Tenant isolation tests ──────────────────────────────────────────────────


def test_loop_raises_when_no_account_context_set():
    # No set_request_context — loop should raise TenantIsolationError up front.
    loop = AgentLoop(
        model="anthropic/test",
        system_message="sys",
        account_id=42,
    )
    with pytest.raises(TenantIsolationError):
        asyncio.run(loop.run(user_message="x"))


def test_loop_raises_when_account_context_mismatches():
    tokens = set_request_context(account_id=1, org_id=1)
    try:
        loop = AgentLoop(
            model="anthropic/test",
            system_message="sys",
            account_id=999,  # mismatched on purpose
        )
        with pytest.raises(TenantIsolationError):
            asyncio.run(loop.run(user_message="x"))
    finally:
        reset_request_context(tokens)


def test_concurrent_loops_see_their_own_account():
    """Two parallel AgentLoop runs in different account contexts must not cross.

    Each run sets its own account contextvar inside an asyncio task. The
    step_callback records `resolve_account_id()` at each iteration; we
    assert each run only sees its own ID.
    """

    async def fake_acompletion(**kwargs):
        return _resp(_msg("ok"))

    async def run_for_account(account_id: int) -> list[int]:
        observed: list[int] = []

        def step_cb(iteration, prev_tools, **kwargs):
            observed.append(resolve_account_id())

        tokens = set_request_context(account_id=account_id, org_id=1)
        try:
            with patch("agent.loop.litellm.acompletion", side_effect=fake_acompletion):
                loop = AgentLoop(
                    model="anthropic/test",
                    system_message="sys",
                    account_id=account_id,
                    step_callback=step_cb,
                )
                await loop.run(user_message="hi")
        finally:
            reset_request_context(tokens)
        return observed

    async def harness():
        return await asyncio.gather(run_for_account(1), run_for_account(2))

    a, b = asyncio.run(harness())
    assert a == [1]
    assert b == [2]
