"""Tests for ``invoke_agent`` — the progress-fan-out wrapper that every
LLM-backed flow routes through."""
from __future__ import annotations

import asyncio

import pytest

from llm.invocation import invoke_agent
from llm.streams import StreamRegistry


def test_invoke_agent_emits_progress_then_done_on_success():
    reg = StreamRegistry()
    run = reg.start(source="unit")

    async def runner(progress_cb):
        await progress_cb("step-1")
        await progress_cb("step-2")
        return {"reply": "all good"}

    async def scenario():
        return await invoke_agent(
            run=run, runner=runner, done_payload=lambda r: {"reply": r["reply"]},
        )

    result = asyncio.run(scenario())
    assert result == {"reply": "all good"}
    assert [(e.type, e.text) for e in run.progress_log] == [
        ("progress", "step-1"),
        ("progress", "step-2"),
        ("done", None),
    ]
    # done event carries the caller-specific payload.
    assert run.progress_log[-1].payload == {"reply": "all good"}


def test_invoke_agent_emits_error_on_exception_and_reraises():
    reg = StreamRegistry()
    run = reg.start(source="unit")

    async def runner(progress_cb):
        await progress_cb("step-1")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(invoke_agent(run=run, runner=runner))

    types = [e.type for e in run.progress_log]
    assert types == ["progress", "error"]
    err = run.progress_log[-1]
    assert err.text == "boom"
    assert err.payload is not None
    assert err.payload.get("source") == "unit"


def test_invoke_agent_skips_blank_progress_events():
    """Empty progress strings shouldn't clutter the replay buffer —
    callers that accidentally emit blanks (e.g. empty strings from
    the upstream tool-progress layer) get silently filtered."""
    reg = StreamRegistry()
    run = reg.start(source="unit")

    async def runner(progress_cb):
        await progress_cb("")
        await progress_cb("real-step")
        await progress_cb("")

    asyncio.run(invoke_agent(run=run, runner=runner))
    texts = [e.text for e in run.progress_log if e.type == "progress"]
    assert texts == ["real-step"]


def test_invoke_agent_done_payload_is_optional():
    reg = StreamRegistry()
    run = reg.start(source="unit")

    async def runner(_cb):
        return "result"

    asyncio.run(invoke_agent(run=run, runner=runner))
    done = run.progress_log[-1]
    assert done.type == "done"
    assert done.payload is None
