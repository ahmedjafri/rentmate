import logging

from backends.local_auth import reset_fallback_request_context, set_fallback_request_context
from db.models import AgentTrace
from llm.runs import start_run
from llm.tracing import log_trace, _orphan_warned


def test_log_trace_persists_rows_inside_active_run(db):
    token = set_fallback_request_context(account_id=1, org_id=1)
    try:
        with start_run(
            source="chat",
            task_id="123",
            conversation_id="456",
            agent_version="rentmate-test",
            execution_path="local",
            trigger_input="hello",
        ) as run:
            log_trace("llm_request", "chat", "trace persistence test")
    finally:
        reset_fallback_request_context(token)

    trace = db.query(AgentTrace).order_by(AgentTrace.timestamp.desc()).first()
    assert trace is not None
    assert trace.summary == "trace persistence test"
    assert trace.source == "chat"
    assert trace.run_id == run.run_id
    assert trace.run.task_id == "123"
    assert trace.run.conversation_id == "456"
    assert trace.sequence_num == 0
    assert trace.org_id == 1
    assert trace.creator_id == 1


def test_log_trace_dropped_outside_active_run(db, caplog):
    _orphan_warned.clear()
    token = set_fallback_request_context(account_id=1, org_id=1)
    try:
        with caplog.at_level(logging.WARNING, logger="llm.tracing"):
            log_trace("llm_request", "orphan_source", "no run, no trace")
    finally:
        reset_fallback_request_context(token)

    rows = (
        db.query(AgentTrace)
        .filter_by(source="orphan_source")
        .all()
    )
    assert rows == []
    assert any("dropping trace_type=llm_request" in m for m in caplog.messages)
