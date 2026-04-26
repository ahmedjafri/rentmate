"""SSE helpers — convert a ``StreamRun`` into a FastAPI StreamingResponse.

The wire envelope matches what every existing client already expects:

    data: {"type": "stream_id", "stream_id": "…"}
    data: {"type": "progress", "text": "…"}
    data: {"type": "done", …caller-specific-payload}
    data: {"type": "error", "message": "…"}

Callers use one of two entry points:

  * ``start_and_stream(...)`` — create a new run, kick off the runner
    in the background, and return an SSE response for the initial
    subscriber.
  * ``subscribe_and_stream(run)`` — attach to an existing run (used by
    the reconnect endpoint).
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, TypeVar

from fastapi.responses import StreamingResponse

from llm.invocation import ProgressCb, invoke_agent
from llm.streams import StreamEvent, StreamRun, stream_registry

logger = logging.getLogger("rentmate.agent_stream")

T = TypeVar("T")

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


def _encode(frame: dict[str, Any]) -> str:
    return f"data: {json.dumps(frame)}\n\n"


async def _sse_iter(run: StreamRun, *, emit_stream_id: bool) -> Any:
    """SSE body generator — the shared drain for both entry points."""
    if emit_stream_id:
        yield _encode({"type": "stream_id", "stream_id": run.stream_id})
    async for event in run.subscribe():
        yield _encode(event.to_wire())


def _sse_response_for(run: StreamRun, *, emit_stream_id: bool) -> StreamingResponse:
    return StreamingResponse(
        _sse_iter(run, emit_stream_id=emit_stream_id),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


def start_and_stream(
    *,
    source: str,
    runner: Callable[[ProgressCb], Awaitable[T]],
    done_payload: Callable[[T], dict] | None = None,
    task_id: str | None = None,
    conversation_id: str | None = None,
    stream_id: str | None = None,
) -> StreamingResponse:
    """Start a new run, kick off the runner, and return the SSE response.

    The first frame in the SSE body is always a ``stream_id`` event so
    the client can stash it and reconnect later via the generic
    ``GET /api/agent-streams/{stream_id}`` endpoint.
    """
    run = stream_registry.start(
        source=source,
        task_id=task_id,
        conversation_id=conversation_id,
        stream_id=stream_id,
    )
    run.task = asyncio.create_task(invoke_agent(
        run=run,
        runner=runner,
        done_payload=done_payload,
    ))
    return _sse_response_for(run, emit_stream_id=True)


def subscribe_and_stream(run: StreamRun) -> StreamingResponse:
    """Attach to an existing (possibly already-terminal) run."""
    return _sse_response_for(run, emit_stream_id=True)


__all__ = ["start_and_stream", "subscribe_and_stream", "SSE_HEADERS"]
