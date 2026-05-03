"""Invoke agent-shaped coroutines inside a ``StreamRun``.

Any LLM-backed flow that wants reconnect-capable progress goes through
``invoke_agent``: it wires a progress callback into the caller's runner,
fans those events out to the run's subscribers, and emits a terminal
``done`` / ``error`` event when the runner returns or raises.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, TypeVar

from agent.streams import StreamEvent, StreamRun

logger = logging.getLogger("rentmate.invocation")

T = TypeVar("T")
ProgressCb = Callable[[str], Awaitable[None]]


async def invoke_agent(
    *,
    run: StreamRun,
    runner: Callable[[ProgressCb], Awaitable[T]],
    done_payload: Callable[[T], dict] | None = None,
) -> T:
    """Run ``runner(progress_cb)`` inside ``run``.

    ``runner`` is any async callable that accepts a progress-emitting
    callback. It can be a direct ``call_agent`` wrapper, a multi-stage
    document pipeline, or anything in between.

    On success: emits a ``done`` event whose payload comes from
    ``done_payload(result)`` (or empty if omitted), then returns the
    runner's result.

    On failure: emits an ``error`` event with the exception message and
    re-raises so the caller's supervisor still sees the failure.
    """
    async def progress_cb(text: str) -> None:
        if not text:
            return
        await run.emit(StreamEvent(type="progress", text=text))

    try:
        result = await runner(progress_cb)
    except Exception as exc:
        logger.exception("stream %s failed in runner", run.stream_id)
        await run.emit(StreamEvent(
            type="error",
            text=str(exc)[:500],
            payload={"source": run.source, "detail": repr(exc)[:1000]},
        ))
        raise
    payload = done_payload(result) if done_payload is not None else None
    await run.emit(StreamEvent(type="done", payload=payload))
    return result


__all__ = ["invoke_agent", "ProgressCb"]
