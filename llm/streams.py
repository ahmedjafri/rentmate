"""In-memory agent-stream registry — the reconnectable-progress pattern.

Every LLM-backed flow (chat reply, routine run, task review, document
analysis) can register itself here so clients can:

  * watch the live progress feed of a new run, **and**
  * reconnect to an in-flight or recently-finished run by ``stream_id``
    and pick up the full event sequence (buffered replay + live tail).

The design mirrors the ``_active_chats`` pattern that ``handlers/chat.py``
used pre-consolidation, generalised so any caller can use it.

Process-local. Multi-worker scaling is a later concern; for today a
single gunicorn worker handles every agent run end-to-end.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, AsyncIterator, Literal, Optional

logger = logging.getLogger("rentmate.streams")


StreamEventType = Literal["progress", "done", "error"]


@dataclass
class StreamEvent:
    """One user-visible progress event produced during a run.

    ``text`` is the human-readable label (used for progress + error).
    ``payload`` carries structured caller-specific data — e.g. the
    routine's task snapshot on a done event, or an exception detail
    dict on an error event.
    """
    type: StreamEventType
    text: str | None = None
    payload: dict[str, Any] | None = None

    def to_wire(self) -> dict[str, Any]:
        """Project to the JSON envelope used on the SSE wire.

        Callers keep the legacy key names (``message`` for errors,
        plus whatever done-payload keys the original endpoints used)
        so the frontend consumer doesn't have to change shape.
        """
        frame: dict[str, Any] = {"type": self.type}
        if self.type == "progress" and self.text is not None:
            frame["text"] = self.text
        elif self.type == "error":
            frame["message"] = self.text or ""
            if self.payload:
                frame.update(self.payload)
        elif self.type == "done":
            if self.payload:
                frame.update(self.payload)
        return frame


@dataclass
class StreamRun:
    """A single logical agent invocation with live + replay semantics."""
    stream_id: str
    source: str
    task_id: str | None = None
    conversation_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    task: asyncio.Task | None = None
    progress_log: list[StreamEvent] = field(default_factory=list)
    subscribers: list[asyncio.Queue[StreamEvent]] = field(default_factory=list)
    terminal: StreamEvent | None = None
    terminal_at: datetime | None = None

    def is_done(self) -> bool:
        return self.terminal is not None

    async def emit(self, event: StreamEvent) -> None:
        """Append to the replay buffer and fan out to live subscribers.

        Idempotent for terminal events — a second ``done`` or ``error``
        is logged and dropped rather than confusing existing subscribers.
        """
        if self.terminal is not None:
            logger.debug(
                "stream %s already terminal (%s); dropping %s",
                self.stream_id, self.terminal.type, event.type,
            )
            return
        self.progress_log.append(event)
        if event.type in ("done", "error"):
            self.terminal = event
            self.terminal_at = datetime.now(UTC)
        # Fan-out — best-effort. Slow subscribers don't stall other
        # subscribers or the run.
        for q in list(self.subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("stream %s subscriber queue full; dropping event", self.stream_id)

    async def subscribe(self) -> AsyncIterator[StreamEvent]:
        """Yield buffered events, then yield live events until terminal.

        A subscriber that attaches *after* a terminal event only sees
        the replay — the generator returns once the terminal is yielded.
        """
        q: asyncio.Queue[StreamEvent] = asyncio.Queue()
        self.subscribers.append(q)
        try:
            # Replay everything already buffered. If a terminal was
            # already emitted, the replay includes it and we return.
            for ev in list(self.progress_log):
                yield ev
                if ev.type in ("done", "error"):
                    return
            while True:
                ev = await q.get()
                yield ev
                if ev.type in ("done", "error"):
                    return
        finally:
            try:
                self.subscribers.remove(q)
            except ValueError:
                pass


class StreamRegistry:
    """Global process-local registry for active + recently-finished runs."""

    def __init__(self, *, ttl: timedelta = timedelta(minutes=10)):
        self._runs: dict[str, StreamRun] = {}
        self._ttl = ttl

    def start(
        self,
        *,
        source: str,
        task_id: str | None = None,
        conversation_id: str | None = None,
        stream_id: str | None = None,
    ) -> StreamRun:
        """Create and register a new run. Returns the ``StreamRun`` so the
        caller can attach the runner task and emit events."""
        sid = stream_id or f"{source}-{uuid.uuid4().hex}"
        run = StreamRun(
            stream_id=sid,
            source=source,
            task_id=task_id,
            conversation_id=conversation_id,
        )
        self._runs[sid] = run
        self.sweep()
        return run

    def get(self, stream_id: str) -> StreamRun | None:
        self.sweep()
        return self._runs.get(stream_id)

    def sweep(self) -> None:
        """Evict terminated runs older than ``ttl``.

        Called lazily on every ``start``/``get`` so we don't need a
        background task. Idempotent.
        """
        cutoff = datetime.now(UTC) - self._ttl
        stale = [
            sid for sid, r in self._runs.items()
            if r.terminal is not None and (r.terminal_at or r.created_at) < cutoff
        ]
        for sid in stale:
            self._runs.pop(sid, None)

    def active_run_ids(self) -> list[str]:
        """Debug-friendly snapshot — runs that haven't terminated yet."""
        return [sid for sid, r in self._runs.items() if r.terminal is None]


# Process singleton.
stream_registry = StreamRegistry()


__all__ = [
    "StreamEvent",
    "StreamEventType",
    "StreamRun",
    "StreamRegistry",
    "stream_registry",
]
