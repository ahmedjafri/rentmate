from backends.local_auth import reset_fallback_request_context, set_fallback_request_context
from db.models import AgentTrace
from llm.tracing import log_trace


def test_log_trace_persists_rows_with_fallback_context(db):
    token = set_fallback_request_context(account_id=1, org_id=1)
    try:
        log_trace("llm_request", "chat", "trace persistence test", task_id="123")
    finally:
        reset_fallback_request_context(token)

    trace = db.query(AgentTrace).order_by(AgentTrace.timestamp.desc()).first()
    assert trace is not None
    assert trace.summary == "trace persistence test"
    assert trace.source == "chat"
    assert trace.task_id == "123"
    assert trace.org_id == 1
    assert trace.creator_id == 1
