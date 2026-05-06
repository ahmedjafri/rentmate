"""Agent run lifecycle — group every invocation under one persisted row.

Every ``log_trace(...)`` write must happen inside an active ``start_run(...)``
context. Outside a run, traces are dropped with a warning (see
``llm/tracing.py``).

Usage::

    with start_run(
        source="chat",
        task_id="42",
        conversation_id="100",
        agent_version="rentmate-agent-2026-04",
        prompt_version="soul-v3",
        model="claude-sonnet-4-6",
        execution_path="local",
        trigger_input=user_message,
    ) as run:
        reply = await call_agent(...)
        run.complete(
            status="completed",
            final_response=reply,
            input_tokens=in_tok,
            output_tokens=out_tok,
            iteration_count=api_calls,
        )

If the block raises, the run is marked ``status="errored"`` with
``error_message=str(exc)`` before the exception propagates.
"""
from __future__ import annotations

import contextlib
import itertools
import logging
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from decimal import Decimal
from typing import Iterator

from integrations.local_auth import resolve_account_id, resolve_org_id

logger = logging.getLogger(__name__)


current_run_id: ContextVar[str | None] = ContextVar("current_run_id", default=None)
current_run_sequence: ContextVar["itertools.count[int] | None"] = ContextVar(
    "current_run_sequence", default=None
)
# Set inside start_run so any code in the run's call stack can accumulate
# per-iteration totals via ``accumulate_run_totals``.
_current_run_handle: ContextVar["_RunHandle | None"] = ContextVar(
    "_current_run_handle", default=None
)


def accumulate_run_totals(
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    iteration_count: int = 0,
    cost_cents: Decimal | float | int | None = None,
) -> None:
    """Add per-iteration totals onto the active run.

    Safe to call from inside any agent invocation; no-op outside a run.
    The final values are written to the ``agent_runs`` row when the
    surrounding ``start_run`` context exits.

    ``cost_cents`` is the actual cost reported by the LLM provider (via
    ``litellm.completion_cost``). When supplied, it overrides the
    hardcoded model-rate fallback in ``_compute_cost_cents``.
    """
    handle = _current_run_handle.get()
    if handle is None or handle.is_nested:
        return
    handle.input_tokens += int(input_tokens or 0)
    handle.output_tokens += int(output_tokens or 0)
    handle.iteration_count += int(iteration_count or 0)
    if cost_cents is not None:
        increment = Decimal(str(cost_cents))
        if handle.total_cost_cents is None:
            handle.total_cost_cents = increment
        else:
            handle.total_cost_cents += increment


# Fallback per-million-token rates in cents, used only when litellm did
# not report a cost for the response (e.g. mocked responses in tests).
# Production cost comes straight from ``litellm.completion_cost``.
_MODEL_RATES_CENTS_PER_MTOK: dict[str, tuple[float, float]] = {
    # (input_per_mtok, output_per_mtok) in cents
    "claude-haiku-4-5": (100.0, 500.0),
    "claude-sonnet-4-6": (300.0, 1500.0),
    "claude-opus-4-7": (1500.0, 7500.0),
}


def _compute_cost_cents(
    model: str | None, input_tokens: int, output_tokens: int
) -> Decimal:
    if not model:
        return Decimal("0")
    rates = _MODEL_RATES_CENTS_PER_MTOK.get(model)
    if rates is None:
        return Decimal("0")
    in_rate, out_rate = rates
    cost = (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000.0
    return Decimal(f"{cost:.4f}")


class _RunHandle:
    """Mutable buffer the caller fills in; flushed by the context manager.

    A handle returned from a *nested* ``start_run`` is a no-op — its
    ``complete`` calls are silently dropped because the parent run owns
    the totals.
    """

    def __init__(self, run_id: str | None, *, is_nested: bool = False):
        self.run_id = run_id
        self.is_nested = is_nested
        self.status: str | None = None
        self.final_response: str | None = None
        self.error_message: str | None = None
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.iteration_count: int = 0
        self.total_cost_cents: Decimal | None = None

    def complete(
        self,
        *,
        status: str = "completed",
        final_response: str | None = None,
        error_message: str | None = None,
        total_cost_cents: Decimal | None = None,
    ) -> None:
        """Mark the run's terminal state.

        Per-iteration token / iteration totals are pushed in throughout
        the run via ``accumulate_run_totals`` and persisted on context
        exit. Only call this for the high-level outcome (status, final
        reply, terminal error).
        """
        if self.is_nested:
            return
        self.status = status
        self.final_response = final_response
        self.error_message = error_message
        if total_cost_cents is not None:
            self.total_cost_cents = total_cost_cents


@contextlib.contextmanager
def start_run(
    *,
    source: str,
    agent_version: str,
    execution_path: str,
    task_id: str | None = None,
    conversation_id: str | None = None,
    prompt_version: str | None = None,
    model: str | None = None,
    trigger_input: str | None = None,
    creator_id: int | None = None,
    org_id: int | None = None,
) -> Iterator[_RunHandle]:
    """Wrap an agent invocation in a persisted ``AgentRun`` row.

    Nested calls (``current_run_id`` already set) yield a no-op handle so
    handlers can wrap ``call_agent`` without ``_local_fallback`` starting
    a second run.
    """
    if current_run_id.get() is not None:
        # Reuse the parent run.
        yield _RunHandle(current_run_id.get(), is_nested=True)
        return

    from db.models import AgentRun
    from db.session import SessionLocal

    run_id = str(uuid.uuid4())
    started_at = datetime.now(UTC)
    resolved_org_id = org_id if org_id is not None else _safe_resolve_org_id()
    resolved_creator_id = (
        creator_id if creator_id is not None else _safe_resolve_account_id()
    )

    sess = SessionLocal.session_factory()
    try:
        sess.add(AgentRun(
            id=run_id,
            org_id=resolved_org_id,
            creator_id=resolved_creator_id,
            started_at=started_at,
            status="running",
            source=source,
            trigger_input=trigger_input,
            agent_version=agent_version,
            prompt_version=prompt_version,
            model=model,
            execution_path=execution_path,
            conversation_id=conversation_id,
            task_id=task_id,
            total_input_tokens=0,
            total_output_tokens=0,
            total_cost_cents=Decimal("0"),
            iteration_count=0,
        ))
        sess.commit()
    except Exception:
        sess.rollback()
        sess.close()
        # The run row is the FK target for every trace this invocation
        # writes — without it traces would silently drop. Surface the
        # failure rather than continuing in a broken state.
        raise
    finally:
        sess.close()

    handle = _RunHandle(run_id)
    run_token = current_run_id.set(run_id)
    seq_token = current_run_sequence.set(itertools.count(start=0))
    handle_token = _current_run_handle.set(handle)
    # ATIF step_id is 1-indexed per spec — distinct from the legacy
    # 0-indexed sequence_num used by the in-flight log_trace shim.
    from agent.trajectory import (
        init_run_step_counter,
        record_step,
        reset_run_step_counter,
    )
    step_token = init_run_step_counter()

    # ATIF Step 1 = the user's trigger input. Recording at run start
    # gives every trajectory a deterministic first step regardless of
    # whether the loop produces any agent steps (e.g. fast-path errors).
    if trigger_input:
        try:
            record_step("user", trigger_input)
        except Exception:
            logger.exception("failed to record initial user step run_id=%s", run_id)
    raised: BaseException | None = None
    try:
        yield handle
    except BaseException as exc:
        raised = exc
        if handle.status is None:
            handle.status = "errored"
            handle.error_message = str(exc)
        raise
    finally:
        reset_run_step_counter(step_token)
        _current_run_handle.reset(handle_token)
        current_run_sequence.reset(seq_token)
        current_run_id.reset(run_token)

        final_status = handle.status or ("errored" if raised else "completed")
        cost = handle.total_cost_cents
        if cost is None:
            cost = _compute_cost_cents(
                model, handle.input_tokens, handle.output_tokens
            )

        sess2 = SessionLocal.session_factory()
        try:
            row = sess2.query(AgentRun).filter_by(id=run_id).first()
            if row is not None:
                row.status = final_status
                row.ended_at = datetime.now(UTC)
                row.final_response = handle.final_response
                row.error_message = handle.error_message
                row.total_input_tokens = handle.input_tokens
                row.total_output_tokens = handle.output_tokens
                row.iteration_count = handle.iteration_count
                row.total_cost_cents = cost
                sess2.commit()
        except Exception:
            sess2.rollback()
            logger.exception(
                "failed to finalize agent_run id=%s status=%s",
                run_id, final_status,
            )
        finally:
            sess2.close()


def derive_run_metadata(
    *,
    session_key: str | None = None,
    task_id: str | None = None,
    conversation_id: str | None = None,
    source_override: str | None = None,
) -> dict[str, str | None]:
    """Build the standard kwargs for ``start_run`` from a handler's context.

    Handlers and ``_local_fallback`` derive run metadata the same way so a
    handler that wraps the call in ``start_run`` produces an identical row
    to one ``_local_fallback`` would have created on its own.
    """
    import os

    if source_override:
        source = source_override
    elif session_key and session_key.startswith("eval:"):
        source = "assess"
    elif session_key and session_key.startswith("task:"):
        source = "chat"
    else:
        source = "chat"

    derived_task_id = task_id
    if derived_task_id is None and session_key and session_key.startswith("task:"):
        derived_task_id = session_key.split(":", 1)[1]

    return {
        "source": source,
        "task_id": derived_task_id,
        "conversation_id": conversation_id,
        "agent_version": os.getenv("RENTMATE_AGENT_VERSION", "rentmate-agent"),
        "prompt_version": os.getenv("RENTMATE_PROMPT_VERSION") or None,
        "model": os.getenv("LLM_MODEL", "anthropic/claude-haiku-4-5-20251001"),
        "execution_path": "local",
    }


def _safe_resolve_org_id() -> int:
    try:
        return resolve_org_id()
    except RuntimeError:
        from db.models.base import DEFAULT_ORG_ID
        return DEFAULT_ORG_ID


def _safe_resolve_account_id() -> int | None:
    try:
        return resolve_account_id()
    except RuntimeError:
        return None
