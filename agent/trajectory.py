"""Harbor Framework ATIF v1.4 trajectory writer + serializer.

Replaces the trace-row-per-event ``llm/tracing.py`` model with a
step-row-per-turn model that maps 1:1 to the Agent Trajectory
Interchange Format. One ``AgentStep`` row per ATIF Step; a full ATIF
trajectory is composed at read time by ``to_trajectory(run_id)``.

Spec reference: https://www.harborframework.com/docs/agents/trajectory-format

Two writer entry points:

- ``record_step(source, message, …)`` — one-shot for simple turns
  (user message, system note, plain agent reply with no tool calls).
- ``begin_agent_step(message, model_name)`` — context manager for an
  agent turn that fans out to tool calls + observations + metrics.
  Buffers everything in memory and commits one ``AgentStep`` row at
  context exit.

Both write inside a nested SAVEPOINT on the caller's session so a step
insert that fails (FK violation under test isolation, etc.) rolls back
only the savepoint and leaves the caller's transaction usable. Steps
fire-and-forget commit the savepoint; they ride along whatever
transaction the caller eventually completes.

Outside an active ``start_run(...)``, all writes are dropped with a
deduped warning — the same "no run, no trace" rule as ``log_trace``.

# TODO(atif): a streaming/replay API (consume an ATIF trajectory and
# reproduce the run) belongs in a separate module — see follow-up.
"""
from __future__ import annotations

import contextlib
import itertools
import logging
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Iterator

from agent.runs import current_run_id
from integrations.local_auth import resolve_account_id, resolve_org_id

logger = logging.getLogger(__name__)

ATIF_SCHEMA_VERSION = "ATIF-v1.4"

_VALID_SOURCES = ("user", "agent", "system")

# Per-run ATIF step_id allocator (1-indexed per ATIF spec). Distinct from
# ``llm.runs.current_run_sequence`` which stays 0-indexed for the legacy
# trace shim during the cutover window.
current_step_id_counter: ContextVar["itertools.count[int] | None"] = ContextVar(
    "current_step_id_counter", default=None
)

# Active builder for the in-flight agent turn (set by ``begin_agent_step``).
# Tool-dispatch callbacks pull this off the context to attach tool_calls
# + observations onto the same step row instead of fanning them out.
_current_step_builder: ContextVar["StepBuilder | None"] = ContextVar(
    "_current_step_builder", default=None
)

_orphan_warned: set[tuple[str, str]] = set()


def _warn_orphan(source: str, kind: str) -> None:
    key = (source, kind)
    if key in _orphan_warned:
        return
    _orphan_warned.add(key)
    logger.warning(
        "trajectory write outside agent run; dropping source=%s kind=%s "
        "(further occurrences silenced for this pair).",
        source, kind,
    )


def _next_step_id() -> int | None:
    counter = current_step_id_counter.get()
    if counter is None:
        return None
    return next(counter)


def _persist_step(
    *,
    run_id: str,
    step_id: int,
    source: str,
    message: str,
    model_name: str | None = None,
    reasoning_content: str | None = None,
    tool_calls: list[dict] | None = None,
    observation: dict | None = None,
    metrics: dict | None = None,
    extra: dict | None = None,
) -> None:
    """Insert one ``AgentStep`` row inside a savepoint on the caller's session."""
    try:
        from db.models import AgentStep
        from db.session import SessionLocal

        sess = SessionLocal.session_factory()
        sp = sess.begin_nested()
        try:
            sess.add(AgentStep(
                id=str(uuid.uuid4()),
                org_id=resolve_org_id(),
                creator_id=resolve_account_id(),
                run_id=run_id,
                step_id=step_id,
                timestamp=datetime.now(UTC),
                source=source,
                message=message,
                model_name=model_name,
                reasoning_content=reasoning_content,
                tool_calls=tool_calls or None,
                observation=observation or None,
                metrics=metrics or None,
                extra=extra or {},
            ))
            sess.flush()
            sp.commit()
            sess.commit()
        except Exception:
            sp.rollback()
            sess.rollback()
    except Exception:
        # Best-effort — never raise into the caller.
        pass


def record_step(
    source: str,
    message: str,
    *,
    model_name: str | None = None,
    reasoning_content: str | None = None,
    tool_calls: list[dict] | None = None,
    observation: dict | None = None,
    metrics: dict | None = None,
    extra: dict | None = None,
) -> int | None:
    """One-shot step writer. Returns the assigned ``step_id`` or ``None``
    if dropped (no active run)."""
    if source not in _VALID_SOURCES:
        raise ValueError(f"source must be one of {_VALID_SOURCES}, got {source!r}")
    run_id = current_run_id.get()
    if run_id is None:
        _warn_orphan(source, "record_step")
        return None
    step_id = _next_step_id()
    if step_id is None:
        _warn_orphan(source, "record_step")
        return None
    _persist_step(
        run_id=run_id,
        step_id=step_id,
        source=source,
        message=message,
        model_name=model_name,
        reasoning_content=reasoning_content,
        tool_calls=tool_calls,
        observation=observation,
        metrics=metrics,
        extra=extra,
    )
    return step_id


class StepBuilder:
    """In-memory accumulator for a single agent step.

    Tool-dispatch callbacks reach for the active builder via
    ``current_step_builder()`` and call ``add_tool_call`` /
    ``add_observation`` on it. On context exit the buffered state is
    flushed to one ``AgentStep`` row.
    """

    def __init__(self, *, run_id: str, step_id: int, message: str, model_name: str | None):
        self._run_id = run_id
        self._step_id = step_id
        self._message = message
        self._model_name = model_name
        self._reasoning_content: str | None = None
        self._tool_calls: list[dict] = []
        self._results: list[dict] = []
        self._metrics: dict[str, Any] | None = None
        self._extra: dict[str, Any] = {}

    @property
    def step_id(self) -> int:
        return self._step_id

    def add_tool_call(self, *, tool_call_id: str, function_name: str, arguments: dict | None) -> None:
        self._tool_calls.append({
            "tool_call_id": tool_call_id,
            "function_name": function_name,
            "arguments": arguments or {},
        })

    def add_observation(self, *, source_call_id: str, content: str) -> None:
        self._results.append({
            "source_call_id": source_call_id,
            "content": content,
        })

    def set_metrics(
        self,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float,
        cached_tokens: int | None = None,
    ) -> None:
        # TODO(atif): populate reasoning_content from the litellm response
        # — needs per-provider shim (Anthropic <thinking>, OpenAI
        # reasoning_content). For now reasoning is captured by callers
        # that already have it via ``set_reasoning_content``.
        metrics: dict[str, Any] = {
            "prompt_tokens": int(prompt_tokens),
            "completion_tokens": int(completion_tokens),
            "cost_usd": float(cost_usd),
        }
        if cached_tokens is not None:
            metrics["cached_tokens"] = int(cached_tokens)
        self._metrics = metrics

    def set_reasoning_content(self, content: str | None) -> None:
        self._reasoning_content = content

    def update_message(self, message: str) -> None:
        """Replace the step's message text (e.g. once the final assistant
        reply is known after the loop iteration completes)."""
        self._message = message

    def add_extra(self, key: str, value: Any) -> None:
        self._extra[key] = value

    def _commit(self) -> None:
        observation = {"results": self._results} if self._results else None
        _persist_step(
            run_id=self._run_id,
            step_id=self._step_id,
            source="agent",
            message=self._message,
            model_name=self._model_name,
            reasoning_content=self._reasoning_content,
            tool_calls=self._tool_calls or None,
            observation=observation,
            metrics=self._metrics,
            extra=self._extra,
        )


@contextlib.contextmanager
def begin_agent_step(
    message: str = "",
    *,
    model_name: str | None = None,
) -> Iterator[StepBuilder | None]:
    """Open an agent step. The returned builder accumulates tool calls,
    observations, and metrics until context exit, then flushes one row.

    On context exit the builder auto-derives ``metrics`` from the delta
    of the run handle's running token totals — every ``litellm.acompletion``
    inside this context bumps the handle via ``accumulate_run_totals``,
    so the delta is the per-step token spend. ``cost_usd`` comes from
    ``llm.runs._compute_cost_cents`` divided by 100.

    Yields ``None`` outside an active run so callers can use it
    unconditionally without crashing background paths.
    """
    run_id = current_run_id.get()
    if run_id is None:
        _warn_orphan("agent", "begin_agent_step")
        yield None
        return
    step_id = _next_step_id()
    if step_id is None:
        _warn_orphan("agent", "begin_agent_step")
        yield None
        return

    # Snapshot run-handle token totals so the step's metrics fall out of
    # the delta at exit. Avoids threading per-step token plumbing through
    # the agent loop.
    from agent.runs import _compute_cost_cents, _current_run_handle
    handle = _current_run_handle.get()
    before_in = handle.input_tokens if handle is not None else 0
    before_out = handle.output_tokens if handle is not None else 0
    before_cost = handle.total_cost_cents if handle is not None else None

    builder = StepBuilder(
        run_id=run_id, step_id=step_id, message=message, model_name=model_name,
    )
    token = _current_step_builder.set(builder)
    raised: BaseException | None = None
    try:
        yield builder
    except BaseException as exc:
        raised = exc
        builder.add_extra("step_errored", True)
        builder.add_extra("error_message", str(exc)[:500])
        raise
    finally:
        _current_step_builder.reset(token)
        if builder._metrics is None and handle is not None:
            d_in = max(0, handle.input_tokens - before_in)
            d_out = max(0, handle.output_tokens - before_out)
            if d_in or d_out:
                # Prefer the litellm-reported cost delta on the run handle;
                # fall back to the hardcoded model rates only when no
                # provider cost was recorded for this step.
                if handle.total_cost_cents is not None and before_cost is not None:
                    cost_cents = handle.total_cost_cents - before_cost
                elif handle.total_cost_cents is not None:
                    cost_cents = handle.total_cost_cents
                else:
                    cost_cents = _compute_cost_cents(model_name, d_in, d_out)
                cost_usd = float(cost_cents / Decimal("100"))
                builder.set_metrics(
                    prompt_tokens=d_in,
                    completion_tokens=d_out,
                    cost_usd=cost_usd,
                )
        try:
            builder._commit()
        except Exception:
            logger.exception("failed to commit agent step run_id=%s step_id=%s",
                             run_id, step_id)
        if raised is not None:
            pass


def current_step_builder() -> StepBuilder | None:
    """Active builder, or ``None`` if no agent step is open. Tool-dispatch
    callbacks use this to attach tool_calls + observations onto the same
    step row that owns the dispatch."""
    return _current_step_builder.get()


# ─── Serializer / read path ─────────────────────────────────────────────


def _step_to_atif(step: Any) -> dict[str, Any]:
    """Project an ``AgentStep`` row to ATIF Step shape — strips internal
    columns (``org_id``, ``creator_id``, ``run_id``) and emits only fields
    in the ATIF spec.

    # TODO(atif): emit prompt_token_ids/completion_token_ids when we
    # start exporting trajectories for RL training data.
    """
    out: dict[str, Any] = {
        "step_id": int(step.step_id),
        "timestamp": step.timestamp.isoformat() if step.timestamp else None,
        "source": step.source,
        "message": step.message or "",
    }
    if step.model_name is not None:
        out["model_name"] = step.model_name
    if step.reasoning_content is not None:
        out["reasoning_content"] = step.reasoning_content
    if step.tool_calls:
        out["tool_calls"] = step.tool_calls
    if step.observation:
        out["observation"] = step.observation
    if step.metrics:
        out["metrics"] = step.metrics
    if step.extra:
        out["extra"] = step.extra
    return out


def to_trajectory(db: Any, run_id: str) -> dict[str, Any] | None:
    """Build the canonical ATIF v1.4 trajectory for a run.

    Reads from ``agent_steps`` when populated. Falls back to the legacy
    adapter that synthesizes ATIF steps from pre-cutover ``agent_traces``
    rows so historical runs keep rendering in DevTools without a data
    migration.

    Returns ``None`` if the run doesn't exist.
    """
    from db.models import AgentRun, AgentStep

    run = db.query(AgentRun).filter_by(id=run_id).first()
    if run is None:
        return None

    steps = (
        db.query(AgentStep)
        .filter_by(run_id=run_id)
        .order_by(AgentStep.step_id)
        .all()
    )
    if not steps:
        atif_steps = _synthesize_steps_from_traces(db, run)
    else:
        atif_steps = [_step_to_atif(s) for s in steps]

    cost_usd = float(
        (run.total_cost_cents or Decimal("0")) / Decimal("100")
    )
    final_metrics = {
        "total_prompt_tokens": int(run.total_input_tokens or 0),
        "total_completion_tokens": int(run.total_output_tokens or 0),
        "total_cost_usd": cost_usd,
        "total_steps": len(atif_steps),
    }
    return {
        "schema_version": ATIF_SCHEMA_VERSION,
        "session_id": str(run.id),
        "agent": {
            "name": run.agent_version,
            "version": run.prompt_version,
            "model_name": run.model,
        },
        "steps": atif_steps,
        "final_metrics": final_metrics,
        "extra": {
            "rentmate_status": run.status,
            "rentmate_source": run.source,
            "rentmate_conversation_id": run.conversation_id,
            "rentmate_task_id": run.task_id,
            "rentmate_execution_path": run.execution_path,
        },
    }


# ─── Legacy adapter ────────────────────────────────────────────────────


def _parse_detail(detail: str | None) -> dict[str, Any]:
    if not detail:
        return {}
    try:
        import json
        parsed = json.loads(detail)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return {}


def _synthesize_steps_from_traces(db: Any, run: Any) -> list[dict[str, Any]]:
    """Render ATIF Steps from legacy ``agent_traces`` rows for a run that
    pre-dates the trajectory cutover.

    Lossy by design — we never captured ``reasoning_content`` and tool
    call IDs are synthesized as ``legacy-<trace_id>`` so observations
    cross-link via ``source_call_id``. ``llm_request`` / ``llm_reply``
    traces become standalone agent steps with metrics derived from the
    trace's recorded token counts.

    # TODO(atif): remove this adapter when agent_traces is dropped.
    """
    from db.models import AgentTrace

    traces = (
        db.query(AgentTrace)
        .filter_by(run_id=str(run.id))
        .order_by(AgentTrace.sequence_num)
        .all()
    )

    atif: list[dict[str, Any]] = []
    next_step = itertools.count(start=1)

    if run.trigger_input:
        atif.append({
            "step_id": next(next_step),
            "timestamp": (run.started_at.isoformat() if run.started_at else None),
            "source": "user",
            "message": run.trigger_input,
        })

    pending_step: dict[str, Any] | None = None

    def _flush_pending() -> None:
        nonlocal pending_step
        if pending_step is not None:
            atif.append(pending_step)
            pending_step = None

    for trace in traces:
        kind = (trace.trace_type or "").strip().lower()
        detail = _parse_detail(trace.detail)
        ts = trace.timestamp.isoformat() if trace.timestamp else None

        if kind == "tool_call":
            if pending_step is None:
                pending_step = {
                    "step_id": next(next_step),
                    "timestamp": ts,
                    "source": "agent",
                    "message": trace.summary or "",
                    "model_name": trace.model,
                    "tool_calls": [],
                    "observation": {"results": []},
                    "extra": {"legacy": True},
                }
            pending_step["tool_calls"].append({
                "tool_call_id": f"legacy-{trace.id}",
                "function_name": trace.tool_name or detail.get("tool_name") or "unknown",
                "arguments": detail.get("args") or {},
            })
        elif kind in ("tool_result", "tool_error", "error"):
            if pending_step is None:
                # Stand-alone error/result with no preceding tool_call —
                # emit as a system note so ordering is preserved.
                atif.append({
                    "step_id": next(next_step),
                    "timestamp": ts,
                    "source": "system",
                    "message": trace.summary or kind,
                    "extra": {"legacy": True, "trace_type": kind},
                })
                continue
            calls = pending_step.get("tool_calls") or []
            source_call_id = (
                calls[-1]["tool_call_id"] if calls else f"legacy-{trace.id}"
            )
            content = detail.get("result") or detail.get("error") or trace.summary or ""
            if isinstance(content, (dict, list)):
                import json
                content = json.dumps(content, default=str)
            if kind in ("tool_error", "error"):
                content = f"ERROR: {content}"
                pending_step.setdefault("extra", {})["error_kind"] = "tool_error"
            pending_step["observation"]["results"].append({
                "source_call_id": source_call_id,
                "content": str(content),
            })
        elif kind in ("llm_request", "llm_reply", "llm_exchange"):
            _flush_pending()
            metrics: dict[str, Any] | None = None
            if trace.input_tokens is not None or trace.output_tokens is not None:
                metrics = {
                    "prompt_tokens": int(trace.input_tokens or 0),
                    "completion_tokens": int(trace.output_tokens or 0),
                    "cost_usd": 0.0,
                }
            atif.append({
                "step_id": next(next_step),
                "timestamp": ts,
                "source": "agent",
                "message": trace.summary or "",
                "model_name": trace.model,
                **({"metrics": metrics} if metrics else {}),
                "extra": {"legacy": True, "trace_type": kind},
            })
        else:
            _flush_pending()
            atif.append({
                "step_id": next(next_step),
                "timestamp": ts,
                "source": "system",
                "message": trace.summary or kind,
                "extra": {"legacy": True, "trace_type": kind},
            })

    _flush_pending()
    return atif


# ─── Run-lifecycle integration helpers ─────────────────────────────────


def init_run_step_counter() -> Any:
    """Initialize the per-run ATIF step counter (1-indexed). Returned
    token must be passed to ``reset_run_step_counter`` at run exit. Wired
    into ``llm/runs.py:start_run``."""
    return current_step_id_counter.set(itertools.count(start=1))


def reset_run_step_counter(token: Any) -> None:
    current_step_id_counter.reset(token)
