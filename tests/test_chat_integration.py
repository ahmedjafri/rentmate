"""
Integration tests for the /chat/send SSE endpoint.

The real FastAPI app is exercised end-to-end against an in-memory SQLite
database.  Only two things are mocked:

  1. ``llm.client.call_agent`` — replaces the agent call so tests are
     fast and deterministic.  The mock can optionally call ``on_progress`` to
     simulate the reasoning-trace events that a real tool-using agent emits.

  2. ``handlers.chat.require_user`` — bypasses the JWT validation check so
     tests don't need external auth.

All other layers (FastAPI routing, SSE generation, DB writes, context
building) run for real against the test database.

Run:
    poetry run pytest tests/test_chat_integration.py -v
"""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.pool import StaticPool

from db.enums import TaskCategory, TaskMode, TaskStatus, Urgency
from db.models import Base, Conversation, Message, MessageType, Task
from llm.client import AgentResponse

# ─── DB helpers ──────────────────────────────────────────────────────────────

def _make_engine():
    """Fresh in-memory SQLite engine with the full schema applied."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


def _make_session_factory(engine):
    return scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))


# ─── Test-app context manager ─────────────────────────────────────────────────

async def _mock_require_user(request=None):
    """Mock require_user that also sets the request-scoped account context."""
    from backends.local_auth import set_request_context
    set_request_context(account_id=1)
    return {"id": "test-user", "email": "test@test.com", "account_id": 1}


@asynccontextmanager
async def _test_app(session_factory):
    """
    Yield an AsyncClient wired to the real FastAPI app with:
      - DB session factory replaced by ``session_factory``
      - auth token validation bypassed (any bearer token accepted)
    """
    import main as _main

    with (
        patch("main.SessionLocal", session_factory),
        patch("rentmate.app.SessionLocal", session_factory),
        patch("handlers.deps.SessionLocal", session_factory),
        patch("handlers.chat.SessionLocal", session_factory),
        patch("gql.services.settings_service.is_llm_configured", return_value=True),
        patch(
            "handlers.chat.require_user",
            AsyncMock(side_effect=_mock_require_user),
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=_main.app),
            base_url="http://test",
            headers={"Authorization": "Bearer fake-token"},
        ) as client:
            yield client


# ─── SSE parsing helper ───────────────────────────────────────────────────────

async def _collect_sse(stream_response) -> list[dict]:
    """Drain an SSE streaming response and return parsed event payloads."""
    events = []
    async for line in stream_response.aiter_lines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def engine():
    eng = _make_engine()
    yield eng
    eng.dispose()


@pytest.fixture()
def session_factory(engine):
    return _make_session_factory(engine)


@pytest.fixture()
def db(session_factory):
    session = session_factory()
    yield session
    session.close()


@pytest.fixture()
def task_id(db):
    """Insert a minimal Task with a linked Conversation and return the Task's ID."""
    task = Task(
        creator_id=1,
        title="HVAC Repair",
        task_status=TaskStatus.ACTIVE,
        task_mode=TaskMode.MANUAL,
        category=TaskCategory.MAINTENANCE,
        urgency=Urgency.MEDIUM,
        confidential=False,
        last_message_at=datetime.now(UTC),
    )
    db.add(task)
    db.flush()
    conv = Conversation(
        creator_id=1,
        subject="HVAC Repair",
    )
    db.add(conv)
    db.flush()
    task.ai_conversation_id = conv.id
    db.commit()
    return task.id


# ─── Generic /chat tests ──────────────────────────────────────────────────────

class TestGenericChat:
    def test_streams_thinking_and_reply(self, session_factory):
        """POST /chat returns an SSE stream with Thinking… then done."""

        async def _run():
            async with _test_app(session_factory) as client:
                with patch(
                    "llm.client.call_agent",
                    AsyncMock(return_value=AgentResponse(reply="Hello from RentMate!")),
                ):
                    async with client.stream(
                        "POST",
                        "/chat/send",
                        json={"message": "Hi there"},
                    ) as resp:
                        events = await _collect_sse(resp)

            assert events[0]["type"] == "stream_id"
            progress = [e for e in events if e["type"] == "progress"]
            assert progress[0] == {"type": "progress", "text": "Thinking\u2026"}
            done = next(e for e in events if e["type"] == "done")
            assert done["reply"] == "Hello from RentMate!"
            assert "conversation_id" in done

        asyncio.run(_run())

    def test_passes_conversation_id_through(self, session_factory):
        """A supplied conversation_id is echoed back in the done event."""

        conv_id = str(uuid.uuid4())

        async def _run():
            async with _test_app(session_factory) as client:
                with patch(
                    "llm.client.call_agent",
                    AsyncMock(return_value=AgentResponse(reply="Got it.")),
                ):
                    async with client.stream(
                        "POST",
                        "/chat/send",
                        json={"message": "Follow-up", "conversation_id": conv_id},
                    ) as resp:
                        events = await _collect_sse(resp)

            done = next(e for e in events if e["type"] == "done")
            assert done["conversation_id"] == conv_id

        asyncio.run(_run())


# ─── Task chat SSE tests ──────────────────────────────────────────────────────

class TestTaskChatSSE:
    def test_first_event_is_stream_id(self, session_factory, task_id):
        """The very first SSE event is always {type: stream_id, stream_id: ...}."""

        async def _run():
            async with _test_app(session_factory) as client:
                with patch(
                    "llm.client.call_agent",
                    AsyncMock(return_value=AgentResponse(reply="All good.")),
                ):
                    async with client.stream(
                        "POST",
                        "/chat/send",
                        json={"task_id": str(task_id), "message": "Status?"},
                    ) as resp:
                        events = await _collect_sse(resp)

            assert events[0]["type"] == "stream_id"
            assert "stream_id" in events[0]
            # Second event is the thinking progress
            progress_events = [e for e in events if e["type"] == "progress"]
            assert progress_events[0] == {"type": "progress", "text": "Thinking\u2026"}

        asyncio.run(_run())

    def test_stream_ends_with_done_event(self, session_factory, task_id):
        """Stream ends with a done event containing the reply, message_id, and actions."""

        reply_text = "The HVAC filter is scheduled for replacement."

        async def _run():
            async with _test_app(session_factory) as client:
                with patch(
                    "llm.client.call_agent",
                    AsyncMock(return_value=AgentResponse(reply=reply_text)),
                ):
                    async with client.stream(
                        "POST",
                        "/chat/send",
                        json={"task_id": str(task_id), "message": "What is the HVAC status?"},
                    ) as resp:
                        events = await _collect_sse(resp)

            assert events[-1]["type"] == "done"
            done = events[-1]
            assert done["reply"] == reply_text
            assert "message_id" in done
            assert "conversation_id" in done

        asyncio.run(_run())

    def test_reasoning_trace_appears_after_thinking(self, session_factory, task_id):
        """on_progress calls produce additional progress lines after 'Thinking…'."""

        progress_texts = [
            "Checking maintenance records\u2026",
            "Drafting response\u2026",
        ]

        async def fake_agent(agent_id, session_key, messages, on_progress=None, **kwargs):
            if on_progress:
                for text in progress_texts:
                    await on_progress(text)
            return AgentResponse(reply="Replacement is booked for Thursday.")

        async def _run():
            async with _test_app(session_factory) as client:
                with patch("llm.client.call_agent", side_effect=fake_agent):
                    async with client.stream(
                        "POST",
                        "/chat/send",
                        json={"task_id": str(task_id), "message": "Any updates?"},
                    ) as resp:
                        events = await _collect_sse(resp)

            progress_events = [e for e in events if e["type"] == "progress"]
            # First is always Thinking…
            assert progress_events[0]["text"] == "Thinking\u2026"
            # Remaining match the on_progress calls in order
            trace_texts = [e["text"] for e in progress_events[1:]]
            assert trace_texts == progress_texts
            # Reply is correct
            done = next(e for e in events if e["type"] == "done")
            assert done["reply"] == "Replacement is booked for Thursday."

        asyncio.run(_run())

    def test_error_event_when_agent_raises(self, session_factory, task_id):
        """When call_agent raises, the stream emits {type: error}."""

        async def _run():
            async with _test_app(session_factory) as client:
                with patch(
                    "llm.client.call_agent",
                    AsyncMock(side_effect=RuntimeError("LLM unavailable")),
                ):
                    async with client.stream(
                        "POST",
                        "/chat/send",
                        json={"task_id": str(task_id), "message": "Hello?"},
                    ) as resp:
                        events = await _collect_sse(resp)

            assert any(e["type"] == "error" for e in events)

        asyncio.run(_run())

    def test_ai_reply_persisted_to_db(self, session_factory, task_id, db):
        """After the stream completes the AI reply is saved as a Message row."""

        reply_text = "Rent is due on the 1st."

        async def _run():
            async with _test_app(session_factory) as client:
                with patch(
                    "llm.client.call_agent",
                    AsyncMock(return_value=AgentResponse(reply=reply_text)),
                ):
                    async with client.stream(
                        "POST",
                        "/chat/send",
                        json={"task_id": str(task_id), "message": "When is rent due?"},
                    ) as resp:
                        await _collect_sse(resp)

            # Give the background task a moment to finish the DB write
            await asyncio.sleep(0.2)

        asyncio.run(_run())

        ai_msgs = []
        for _ in range(10):
            db.expire_all()
            task = db.query(Task).filter_by(id=task_id).first()
            conv_id = task.ai_conversation.id
            ai_msgs = (
                db.query(Message)
                .filter_by(
                    conversation_id=conv_id,
                    is_ai=True,
                    message_type=MessageType.MESSAGE,
                )
                .all()
            )
            if ai_msgs:
                break
            asyncio.run(asyncio.sleep(0.1))

        assert len(ai_msgs) == 1
        assert ai_msgs[0].body == reply_text
        assert ai_msgs[0].sender_name == "RentMate"

    def test_404_for_unknown_task(self, session_factory):
        """POST /chat/send with a nonexistent task_id returns 404."""

        async def _run():
            async with _test_app(session_factory) as client:
                with patch(
                    "llm.client.call_agent",
                    AsyncMock(return_value=AgentResponse(reply="irrelevant")),
                ):
                    resp = await client.post(
                        "/chat/send",
                        json={"task_id": str(uuid.uuid4()), "message": "Hello?"},
                    )
            assert resp.status_code == 404

        asyncio.run(_run())
