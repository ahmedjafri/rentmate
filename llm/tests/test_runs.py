"""Tests for the agent-run lifecycle helper."""
from decimal import Decimal

import pytest

from backends.local_auth import reset_fallback_request_context, set_fallback_request_context
from db.models import AgentRun, AgentTrace
from llm.runs import (
    _compute_cost_cents,
    accumulate_run_totals,
    current_run_id,
    derive_run_metadata,
    start_run,
)
from llm.tracing import log_trace


@pytest.fixture(autouse=True)
def _fallback_auth():
    token = set_fallback_request_context(account_id=1, org_id=1)
    try:
        yield
    finally:
        reset_fallback_request_context(token)


def _kwargs(**overrides):
    base = dict(
        source="chat",
        agent_version="rentmate-test",
        execution_path="local",
        trigger_input="hi",
    )
    base.update(overrides)
    return base


def test_start_run_writes_running_then_completed_row(db):
    with start_run(**_kwargs(task_id="42", conversation_id="100")) as run:
        accumulate_run_totals(input_tokens=10, output_tokens=5, iteration_count=1)
        run.complete(status="completed", final_response="ok")

    row = db.query(AgentRun).filter_by(id=run.run_id).one()
    assert row.status == "completed"
    assert row.task_id == "42"
    assert row.conversation_id == "100"
    assert row.total_input_tokens == 10
    assert row.total_output_tokens == 5
    assert row.iteration_count == 1
    assert row.final_response == "ok"
    assert row.error_message is None
    assert row.started_at is not None
    assert row.ended_at is not None


def test_start_run_marks_errored_when_exception_propagates(db):
    captured_id = None
    with pytest.raises(RuntimeError, match="boom"):
        with start_run(**_kwargs()) as run:
            captured_id = run.run_id
            raise RuntimeError("boom")

    row = db.query(AgentRun).filter_by(id=captured_id).one()
    assert row.status == "errored"
    assert row.error_message == "boom"
    assert row.ended_at is not None


def test_start_run_explicit_iteration_limit_status(db):
    with start_run(**_kwargs()) as run:
        accumulate_run_totals(iteration_count=40)
        run.complete(status="iteration_limit", final_response="too many calls")

    row = db.query(AgentRun).filter_by(id=run.run_id).one()
    assert row.status == "iteration_limit"
    assert row.iteration_count == 40
    assert row.final_response == "too many calls"


def test_nested_start_run_reuses_parent_run(db):
    with start_run(**_kwargs(task_id="1")) as outer:
        outer_run_id = outer.run_id
        with start_run(**_kwargs(task_id="999")) as inner:
            # Nested handle reports the parent's id and is_nested=True.
            assert inner.run_id == outer_run_id
            assert inner.is_nested is True
            # complete() on a nested handle is a no-op.
            inner.complete(status="errored", error_message="should be ignored")
            log_trace("tool_call", "chat", "inside nested run")
        outer.complete(status="completed", final_response="ok")

    rows = db.query(AgentRun).all()
    assert len(rows) == 1
    assert rows[0].id == outer_run_id
    assert rows[0].status == "completed"

    traces = db.query(AgentTrace).filter_by(run_id=outer_run_id).all()
    assert len(traces) == 1
    assert traces[0].summary == "inside nested run"


def test_run_traces_get_monotonic_sequence_numbers(db):
    with start_run(**_kwargs()) as run:
        log_trace("tool_call", "chat", "first")
        log_trace("tool_result", "chat", "second")
        log_trace("llm_reply", "chat", "third")

    traces = (
        db.query(AgentTrace)
        .filter_by(run_id=run.run_id)
        .order_by(AgentTrace.sequence_num)
        .all()
    )
    assert [t.sequence_num for t in traces] == [0, 1, 2]
    assert [t.summary for t in traces] == ["first", "second", "third"]


def test_compute_cost_cents_known_model():
    cost = _compute_cost_cents("claude-sonnet-4-6", 1_000_000, 500_000)
    # 300 cents per Mtok input, 1500 per Mtok output → 300 + 750 = 1050 cents.
    assert cost == Decimal("1050.0000")


def test_compute_cost_cents_unknown_model_returns_zero():
    assert _compute_cost_cents("some-other-model", 100, 200) == Decimal("0")
    assert _compute_cost_cents(None, 100, 200) == Decimal("0")


def test_derive_run_metadata_from_session_key():
    md = derive_run_metadata(session_key="task:42", conversation_id="conv-1")
    assert md["source"] == "chat"
    assert md["task_id"] == "42"
    assert md["conversation_id"] == "conv-1"
    assert md["execution_path"] == "local"

    eval_md = derive_run_metadata(session_key="eval:foo")
    assert eval_md["source"] == "assess"

    explicit = derive_run_metadata(session_key="task:7", source_override="task_review")
    assert explicit["source"] == "task_review"


def test_current_run_id_is_unset_outside_start_run():
    assert current_run_id.get() is None
