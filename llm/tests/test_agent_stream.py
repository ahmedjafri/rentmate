"""Tests for ``llm.agent_stream`` — the SSE projection of a StreamRun.

Verifies the wire envelope matches what every existing frontend
consumer expects so no JS/TS changes are required when flows migrate
to the shared helper.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from llm.agent_stream import _sse_iter  # type: ignore[attr-defined]
from llm.invocation import invoke_agent
from llm.streams import StreamRegistry


def _parse_sse_body(body: str) -> list[dict]:
    """Extract the JSON payload from each ``data: ...`` line in an SSE body."""
    frames: list[dict] = []
    for line in body.splitlines():
        if line.startswith("data: "):
            frames.append(json.loads(line[len("data: "):]))
    return frames


def test_sse_iter_emits_stream_id_then_progress_then_done():
    reg = StreamRegistry()
    run = reg.start(source="unit", stream_id="sid-1")

    async def runner(cb):
        await cb("step-1")
        return {"reply": "ok"}

    async def scenario() -> str:
        # Kick off the runner in the background so the SSE iterator
        # races it (matches real flow semantics).
        run.task = asyncio.create_task(invoke_agent(
            run=run, runner=runner, done_payload=lambda r: {"reply": r["reply"]},
        ))
        chunks: list[str] = []
        async for frame in _sse_iter(run, emit_stream_id=True):
            chunks.append(frame)
        await run.task
        return "".join(chunks)

    body = asyncio.run(scenario())
    frames = _parse_sse_body(body)
    assert frames[0] == {"type": "stream_id", "stream_id": "sid-1"}
    assert frames[1] == {"type": "progress", "text": "step-1"}
    # Terminal frame carries the caller's done payload.
    assert frames[-1] == {"type": "done", "reply": "ok"}


def test_sse_iter_error_frame_includes_message_field():
    reg = StreamRegistry()
    run = reg.start(source="unit")

    async def runner(_cb):
        raise ValueError("bad thing")

    async def scenario() -> str:
        run.task = asyncio.create_task(invoke_agent(run=run, runner=runner))
        # The runner raises — catch it here so the test itself doesn't fail.
        chunks: list[str] = []
        async for frame in _sse_iter(run, emit_stream_id=False):
            chunks.append(frame)
        try:
            await run.task
        except ValueError:
            pass
        return "".join(chunks)

    frames = _parse_sse_body(asyncio.run(scenario()))
    err = [f for f in frames if f["type"] == "error"][0]
    assert err["message"] == "bad thing"
    assert err["source"] == "unit"


def test_sse_iter_replay_for_reconnecting_subscriber():
    """A late subscriber sees the full history even if the run already
    finished — proves the reconnect path works end-to-end."""
    reg = StreamRegistry()
    run = reg.start(source="unit", stream_id="sid-late")

    async def runner(cb):
        await cb("early")
        await cb("mid")
        return None

    async def scenario() -> str:
        # Run to completion before any subscriber attaches.
        await invoke_agent(run=run, runner=runner)
        assert run.is_done()
        chunks: list[str] = []
        async for frame in _sse_iter(run, emit_stream_id=True):
            chunks.append(frame)
        return "".join(chunks)

    frames = _parse_sse_body(asyncio.run(scenario()))
    assert [f["type"] for f in frames] == ["stream_id", "progress", "progress", "done"]
    assert frames[1]["text"] == "early"
    assert frames[2]["text"] == "mid"
