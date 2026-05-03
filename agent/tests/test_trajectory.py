"""Tests for ``llm/trajectory.py`` — ATIF v1.4 step writers + serializer."""
from __future__ import annotations

from decimal import Decimal

from agent.runs import accumulate_run_totals, start_run
from agent.trajectory import (
    ATIF_SCHEMA_VERSION,
    begin_agent_step,
    record_step,
    to_trajectory,
)
from db.models import AgentRun, AgentStep
from integrations.local_auth import (
    reset_fallback_request_context,
    set_fallback_request_context,
)


def _ctx():
    return set_fallback_request_context(account_id=1, org_id=1)


def test_to_trajectory_emits_atif_v14_envelope(db):
    """A run with one user step + one agent step renders the canonical
    ATIF v1.4 envelope: schema_version, session_id, agent block, ordered
    steps[], and final_metrics with total_cost_usd in dollars."""
    token = _ctx()
    try:
        with start_run(
            source="chat",
            agent_version="rentmate-test",
            execution_path="local",
            model="claude-haiku-4-5",
            trigger_input="hello",
        ) as run:
            with begin_agent_step("hi back", model_name="claude-haiku-4-5") as step:
                # Simulate a litellm call that bumped the run handle.
                accumulate_run_totals(input_tokens=100, output_tokens=50, iteration_count=1)
                if step is not None:
                    step.add_tool_call(
                        tool_call_id="call_1",
                        function_name="lookup_vendors",
                        arguments={"vendor_type": "plumber"},
                    )
                    step.add_observation(
                        source_call_id="call_1",
                        content="Found 3 vendors.",
                    )
            run.complete(status="completed", final_response="hi back")
        run_id = run.run_id
    finally:
        reset_fallback_request_context(token)

    traj = to_trajectory(db, run_id)
    assert traj is not None
    assert traj["schema_version"] == ATIF_SCHEMA_VERSION
    assert traj["session_id"] == run_id
    assert traj["agent"]["model_name"] == "claude-haiku-4-5"

    steps = traj["steps"]
    assert [s["step_id"] for s in steps] == [1, 2]
    assert steps[0]["source"] == "user"
    assert steps[0]["message"] == "hello"
    assert steps[1]["source"] == "agent"
    assert steps[1]["message"] == "hi back"
    assert steps[1]["tool_calls"][0]["function_name"] == "lookup_vendors"
    assert steps[1]["observation"]["results"][0]["content"] == "Found 3 vendors."

    fm = traj["final_metrics"]
    assert fm["total_prompt_tokens"] == 100
    assert fm["total_completion_tokens"] == 50
    assert fm["total_steps"] == 2
    # cost_usd is cents/100 — _compute_cost_cents for haiku at 100/Mtok
    # input + 500/Mtok output = (100*100 + 50*500)/1M = 0.035 cents
    # → 0.00035 USD.
    assert fm["total_cost_usd"] == 0.00035


def test_step_builder_collapses_tool_calls_into_one_step(db):
    """Multiple tools fired inside one ``begin_agent_step`` collapse
    into a single AgentStep with all tool_calls and observation results,
    matching ATIF's per-turn model."""
    token = _ctx()
    try:
        with start_run(
            source="chat",
            agent_version="rentmate-test",
            execution_path="local",
            trigger_input="trigger",
        ) as run:
            with begin_agent_step("done", model_name="m") as step:
                if step is not None:
                    step.add_tool_call(tool_call_id="a", function_name="t1", arguments={})
                    step.add_observation(source_call_id="a", content="r1")
                    step.add_tool_call(tool_call_id="b", function_name="t2", arguments={})
                    step.add_observation(source_call_id="b", content="r2")
        run_id = run.run_id
    finally:
        reset_fallback_request_context(token)

    rows = db.query(AgentStep).filter_by(run_id=run_id).order_by(AgentStep.step_id).all()
    # step_id=1 (user) + step_id=2 (agent with both tools).
    assert [r.step_id for r in rows] == [1, 2]
    agent_step = rows[1]
    assert [tc["function_name"] for tc in agent_step.tool_calls] == ["t1", "t2"]
    assert [r["content"] for r in agent_step.observation["results"]] == ["r1", "r2"]


def test_step_builder_records_tool_error_in_observation(db):
    """Tool errors land in observation.results as ``ERROR: …`` content
    plus ``extra.error_kind="tool_error"`` so analytics can flag failed
    turns without parsing free-text messages."""
    token = _ctx()
    try:
        with start_run(
            source="chat",
            agent_version="rentmate-test",
            execution_path="local",
            trigger_input="t",
        ) as run:
            with begin_agent_step("oops", model_name="m") as step:
                if step is not None:
                    step.add_tool_call(tool_call_id="x", function_name="boom", arguments={})
                    step.add_observation(source_call_id="x", content="ERROR: vendor 500")
                    step.add_extra("error_kind", "tool_error")
        run_id = run.run_id
    finally:
        reset_fallback_request_context(token)

    agent_step = (
        db.query(AgentStep)
        .filter_by(run_id=run_id, source="agent")
        .one()
    )
    assert agent_step.observation["results"][0]["content"].startswith("ERROR: ")
    assert agent_step.extra["error_kind"] == "tool_error"


def test_cost_usd_emitted_as_cents_div_100(db):
    """The serializer divides ``total_cost_cents`` (Numeric 10,4) by 100
    so ATIF gets ``total_cost_usd`` in dollars, not cents."""
    token = _ctx()
    try:
        with start_run(
            source="chat",
            agent_version="rentmate-test",
            execution_path="local",
            trigger_input="t",
        ) as run:
            run.total_cost_cents = Decimal("1.2345")  # 1.2345 cents = $0.012345
        run_id = run.run_id
    finally:
        reset_fallback_request_context(token)

    # Bypass the start_run finalizer's cost computation by writing direct.
    row = db.query(AgentRun).filter_by(id=run_id).one()
    row.total_cost_cents = Decimal("1.2345")
    db.flush()

    traj = to_trajectory(db, run_id)
    assert traj is not None
    assert traj["final_metrics"]["total_cost_usd"] == 0.012345


def test_legacy_adapter_groups_tool_call_and_result_traces(db):
    """A pre-cutover run with only ``agent_traces`` rows renders a valid
    ATIF trajectory via the legacy adapter — tool_call + tool_result
    traces collapse into a single agent step's tool_calls + observation."""
    token = _ctx()
    try:
        # Don't open a step builder — write traces directly to mimic an
        # old run created before this rewrite.
        from agent.tracing import log_trace, make_trace_envelope
        with start_run(
            source="chat",
            agent_version="rentmate-test",
            execution_path="local",
            trigger_input="legacy run",
        ) as run:
            run_id = run.run_id
            log_trace(
                "tool_call", "chat", "lookup_vendors",
                tool_name="lookup_vendors",
                detail=make_trace_envelope("tool_call", tool_name="lookup_vendors", args={"q": "plumber"}),
            )
            log_trace(
                "tool_result", "chat", "lookup_vendors completed",
                tool_name="lookup_vendors",
                detail=make_trace_envelope("tool_result", tool_name="lookup_vendors", result="3 vendors"),
            )
    finally:
        reset_fallback_request_context(token)

    # Wipe the auto-generated user step from start_run so we exercise the
    # legacy-only path. Also wipe nothing else — agent_traces stays.
    db.query(AgentStep).filter_by(run_id=run_id).delete()
    db.flush()

    traj = to_trajectory(db, run_id)
    assert traj is not None
    steps = traj["steps"]
    # Adapter emits: synthesized user step, then one agent step with
    # both tool_call + tool_result rolled in.
    sources = [s["source"] for s in steps]
    assert sources == ["user", "agent"]
    assert steps[0]["message"] == "legacy run"
    agent = steps[1]
    assert agent["tool_calls"][0]["function_name"] == "lookup_vendors"
    assert agent["observation"]["results"][0]["content"] == "3 vendors"


def test_record_step_outside_run_drops_with_warning(db, caplog):
    """``record_step`` outside an active ``start_run`` is a best-effort
    no-op so stray callers can't crash background tasks."""
    import logging

    from agent.trajectory import _orphan_warned
    _orphan_warned.clear()
    token = _ctx()
    try:
        with caplog.at_level(logging.WARNING, logger="agent.trajectory"):
            assert record_step("system", "no run here") is None
    finally:
        reset_fallback_request_context(token)

    assert any("trajectory write outside agent run" in m for m in caplog.messages)
    assert db.query(AgentStep).filter_by(message="no run here").count() == 0


def test_trajectory_returns_none_for_missing_run(db):
    """Unknown run id returns ``None`` so the dev API can 404 cleanly."""
    assert to_trajectory(db, "00000000-0000-0000-0000-000000000bad") is None
