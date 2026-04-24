"""Tests for the agent-stream registry — replay + live-tail semantics."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from llm.streams import StreamEvent, StreamRegistry, StreamRun


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if asyncio.get_event_loop().is_running() else asyncio.run(coro)


def test_run_to_wire_envelopes():
    assert StreamEvent(type="progress", text="hi").to_wire() == {"type": "progress", "text": "hi"}
    assert StreamEvent(type="done", payload={"reply": "ok"}).to_wire() == {"type": "done", "reply": "ok"}
    assert StreamEvent(type="error", text="boom", payload={"source": "x"}).to_wire() == {
        "type": "error", "message": "boom", "source": "x",
    }


def test_emit_latches_terminal_and_drops_subsequent():
    """Once a done/error fires, further emits are no-ops (no crash,
    no replay-buffer growth, no subscriber confusion)."""
    run = StreamRun(stream_id="t1", source="unit")

    async def scenario():
        await run.emit(StreamEvent(type="progress", text="a"))
        await run.emit(StreamEvent(type="done"))
        # Extra emits after terminal must be ignored.
        await run.emit(StreamEvent(type="progress", text="ignored"))
        await run.emit(StreamEvent(type="error", text="ignored"))

    asyncio.run(scenario())
    assert run.is_done()
    assert [(ev.type, ev.text) for ev in run.progress_log] == [
        ("progress", "a"),
        ("done", None),
    ]


def test_subscribe_replays_then_tails_live_events():
    run = StreamRun(stream_id="t2", source="unit")

    async def scenario():
        # Pre-buffer two events before any subscriber attaches.
        await run.emit(StreamEvent(type="progress", text="stage-1"))
        await run.emit(StreamEvent(type="progress", text="stage-2"))

        collected: list[tuple[str, str | None]] = []

        async def consume():
            async for ev in run.subscribe():
                collected.append((ev.type, ev.text))

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0)  # give the subscriber a chance to replay
        # Now emit a live event and the terminal.
        await run.emit(StreamEvent(type="progress", text="stage-3"))
        await run.emit(StreamEvent(type="done"))
        await consumer
        return collected

    events = asyncio.run(scenario())
    assert events == [
        ("progress", "stage-1"),
        ("progress", "stage-2"),
        ("progress", "stage-3"),
        ("done", None),
    ]


def test_subscribe_after_terminal_only_replays():
    """Clients that reconnect after a run finishes see the full history
    and then the generator ends — no hang."""
    run = StreamRun(stream_id="t3", source="unit")

    async def scenario():
        await run.emit(StreamEvent(type="progress", text="a"))
        await run.emit(StreamEvent(type="done", payload={"reply": "ok"}))

        collected: list[str] = []
        async for ev in run.subscribe():
            collected.append(ev.type)
        return collected

    assert asyncio.run(scenario()) == ["progress", "done"]


def test_registry_start_and_get():
    reg = StreamRegistry()
    run = reg.start(source="routine", task_id="42")
    assert run.source == "routine"
    assert run.task_id == "42"
    assert reg.get(run.stream_id) is run
    assert reg.get("does-not-exist") is None


def test_registry_sweeps_terminated_runs_past_ttl():
    reg = StreamRegistry(ttl=timedelta(milliseconds=10))
    run = reg.start(source="unit")

    async def finish():
        await run.emit(StreamEvent(type="done"))
    asyncio.run(finish())
    # Force the terminal_at into the past so the sweep actually evicts.
    run.terminal_at = datetime.now(UTC) - timedelta(seconds=1)

    reg.sweep()
    assert reg.get(run.stream_id) is None, "terminated run past TTL should be evicted"


def test_registry_keeps_active_runs_around():
    reg = StreamRegistry(ttl=timedelta(milliseconds=1))
    run = reg.start(source="unit")
    reg.sweep()
    assert reg.get(run.stream_id) is run, "active (non-terminal) runs must not be evicted by ttl sweep"


def test_registry_respects_explicit_stream_id():
    reg = StreamRegistry()
    run = reg.start(source="chat", stream_id="abc-123")
    assert run.stream_id == "abc-123"
    assert reg.get("abc-123") is run
