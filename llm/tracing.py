"""Persistent trace logging for agent operations.

Call log_trace() from anywhere to record tool calls, LLM responses,
suggestion creation/execution, and errors into the agent_traces table.
"""
import json
import uuid
from datetime import UTC, datetime

from backends.local_auth import resolve_account_id


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
    """Persist a trace entry. Best-effort — never raises."""
    try:
        from db.models import AgentTrace
        from db.session import SessionLocal

        if isinstance(detail, dict):
            detail = json.dumps(detail, default=str)

        db = SessionLocal.session_factory()
        try:
            db.add(AgentTrace(
                id=str(uuid.uuid4()),
                timestamp=datetime.now(UTC),
                trace_type=trace_type,
                source=source,
                task_id=task_id,
                conversation_id=conversation_id,
                tool_name=tool_name,
                summary=summary[:500],
                detail=detail,
                suggestion_id=suggestion_id,
                creator_id=resolve_account_id(),
            ))
            db.commit()
        finally:
            db.close()
    except Exception:
        pass  # tracing must never break the main flow
