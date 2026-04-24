"""Persistent trace logging for agent operations.

Call log_trace() from anywhere to record tool calls, LLM responses,
suggestion creation/execution, and errors into the agent_traces table.
"""
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from backends.local_auth import resolve_account_id
from backends.local_auth import resolve_org_id

TRACE_ENVELOPE_VERSION = 1
TRACE_TYPE_MAX_LENGTH = 30


def make_trace_envelope(kind: str, **payload: Any) -> dict[str, Any]:
    return {
        "version": TRACE_ENVELOPE_VERSION,
        "kind": kind,
        **payload,
    }


def log_trace(
    trace_type: str,
    source: str,
    summary: str,
    *,
    task_id: str | None = None,
    conversation_id: str | None = None,
    tool_name: str | None = None,
    detail: str | dict | None = None,
    suggestion_id: str | None = None,
) -> None:
    """Persist a trace entry. Best-effort — never raises and never poisons the caller's session.

    The trace insert runs inside a nested SAVEPOINT on the caller's session so that
    a failure (e.g., FK violation against an uncommitted user under test isolation)
    rolls back only the savepoint and leaves the caller's session usable. The trace
    rides along with whatever transaction the caller eventually commits.
    """
    try:
        from db.models import AgentTrace
        from db.session import SessionLocal

        if isinstance(detail, dict):
            detail = json.dumps(detail, default=str)

        sess = SessionLocal.session_factory()
        sp = sess.begin_nested()
        try:
            sess.add(AgentTrace(
                id=str(uuid.uuid4()),
                timestamp=datetime.now(UTC),
                org_id=resolve_org_id(),
                trace_type=trace_type[:TRACE_TYPE_MAX_LENGTH],
                source=source,
                task_id=task_id,
                conversation_id=conversation_id,
                tool_name=tool_name,
                summary=summary[:500],
                detail=detail,
                suggestion_id=suggestion_id,
                creator_id=resolve_account_id(),
            ))
            sess.flush()
            sp.commit()
            sess.commit()
        except Exception:
            sp.rollback()
            sess.rollback()
    except Exception:
        pass
