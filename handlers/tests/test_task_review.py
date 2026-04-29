"""Tests for the periodic task-review loop (handlers/task_review.py).

Focus: the selection query — which tasks fire on a sweep — since that
encodes the three rules (never-reviewed, cooldown-expired, fresh
external activity). Loop-level failure isolation is also covered.
"""
from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from db.enums import TaskStatus, Urgency
from db.models import (
    Conversation,
    ConversationType,
    Message,
    MessageType,
    ParticipantType,
    Task,
)
from gql.services.number_allocator import NumberAllocator


def _make_task(db, *, title: str, status=TaskStatus.ACTIVE, last_reviewed_at=None, with_ai_conversation=False):
    ai_convo_id = None
    if with_ai_conversation:
        convo = Conversation(
            org_id=1,
            creator_id=1,
            subject=title,
            conversation_type=ConversationType.TASK_AI,
            is_group=False,
            is_archived=False,
        )
        db.add(convo)
        db.flush()
        ai_convo_id = convo.id
    task = Task(
        id=NumberAllocator.allocate_next(db, entity_type="task", org_id=1),
        org_id=1,
        creator_id=1,
        title=title,
        task_status=status,
        last_reviewed_at=last_reviewed_at,
        ai_conversation_id=ai_convo_id,
    )
    db.add(task)
    db.flush()
    return task


def _attach_external_conversation_with_message(
    db, *, task_id: int, sent_at: datetime, body: str = "reply",
):
    convo = Conversation(
        org_id=1,
        creator_id=1,
        subject="ext",
        conversation_type=ConversationType.TENANT,
        is_group=False,
        is_archived=False,
        parent_task_id=task_id,
    )
    db.add(convo)
    db.flush()
    db.add(Message(
        org_id=1,
        conversation_id=convo.id,
        sender_type=ParticipantType.TENANT,
        body=body,
        message_type=MessageType.MESSAGE,
        sender_name="Tenant",
        is_ai=False,
        sent_at=sent_at,
    ))
    db.flush()


@pytest.mark.usefixtures("db")
class TestSelectDueTasks:
    """The select query is the heart of the loop — each rule gets its
    own test so a regression pinpoints which branch broke.
    """

    def _run(self):
        from handlers.task_review import _select_due_tasks
        return _select_due_tasks(self.db)

    def test_never_reviewed_is_picked(self):
        task = _make_task(self.db, title="fresh")
        assert any(t.id == task.id for t in self._run())

    def test_recently_reviewed_is_skipped(self):
        now = datetime.now(UTC)
        task = _make_task(
            self.db, title="recent",
            last_reviewed_at=now - timedelta(minutes=30),
        )
        assert not any(t.id == task.id for t in self._run())

    def test_cooldown_expired_is_picked(self):
        now = datetime.now(UTC)
        task = _make_task(
            self.db, title="stale",
            last_reviewed_at=now - timedelta(hours=2),
        )
        assert any(t.id == task.id for t in self._run())

    def test_new_external_message_reopens_task_inside_cooldown(self):
        now = datetime.now(UTC)
        task = _make_task(
            self.db, title="replied",
            last_reviewed_at=now - timedelta(minutes=10),
        )
        _attach_external_conversation_with_message(
            self.db, task_id=task.id, sent_at=now - timedelta(minutes=1),
        )
        assert any(t.id == task.id for t in self._run()), (
            "fresh tenant message must override cooldown"
        )

    def test_external_message_before_last_review_is_ignored(self):
        now = datetime.now(UTC)
        task = _make_task(
            self.db, title="stale-reply",
            last_reviewed_at=now - timedelta(minutes=10),
        )
        _attach_external_conversation_with_message(
            self.db, task_id=task.id, sent_at=now - timedelta(minutes=30),
        )
        assert not any(t.id == task.id for t in self._run()), (
            "tenant message older than last review must NOT re-trigger"
        )

    def test_non_active_statuses_are_excluded(self):
        resolved = _make_task(self.db, title="done", status=TaskStatus.RESOLVED)
        dismissed = _make_task(self.db, title="gone", status=TaskStatus.DISMISSED)
        paused    = _make_task(self.db, title="wait", status=TaskStatus.PAUSED)
        ids = {t.id for t in self._run()}
        assert resolved.id not in ids
        assert dismissed.id not in ids
        assert paused.id not in ids


@pytest.mark.usefixtures("db")
class TestTickFailureIsolation:
    """One task's review crashing must not block sibling tasks."""

    def test_one_task_failure_does_not_block_others(self):
        good = _make_task(self.db, title="good")
        bad = _make_task(self.db, title="bad")
        calls: list[int] = []

        async def fake_review(task):
            calls.append(task.id)
            if task.id == bad.id:
                raise RuntimeError("boom")

        from handlers import task_review as mod

        with patch.object(mod, "_review_one_task", new=AsyncMock(side_effect=fake_review)):
            reviewed = asyncio.run(mod._task_review_tick())

        assert set(calls) == {good.id, bad.id}, "every due task gets attempted"
        assert reviewed == 1, "only the non-failing task counts toward success"


class _FakeReply:
    def __init__(self, reply: str = "done"):
        self.reply = reply
        self.side_effects: list = []


def _stub_call_agent_that_records_review(status: str, summary: str, next_step: str | None = None):
    """Build a fake ``call_agent`` coroutine that:
      - invokes ``on_progress`` a few times (simulating trace events),
      - writes the review columns directly on the target Task (simulating
        what ``record_task_review`` would do during a real run),
      - returns a reply envelope.

    Isolates the review flow from the LLM + tool registry so the test is
    deterministic and fast.
    """
    async def _fake_call_agent(*args, **kwargs):
        on_progress = kwargs.get("on_progress")
        if on_progress is not None:
            await on_progress("Reading task context")
            await on_progress("Checking recent activity")
            await on_progress("Recording review decision")
        # Extract task_id from session_key ("task_review:<id>")
        session_key: str = kwargs.get("session_key") or ""
        _, _, tid = session_key.partition(":")
        from db.models import Task as TaskModel
        from main import SessionLocal
        inner = SessionLocal()
        try:
            row = inner.query(TaskModel).filter_by(id=tid).first()
            if row is not None:
                row.last_reviewed_at = datetime.now(UTC)
                row.last_review_status = status
                row.last_review_summary = summary
                row.last_review_next_step = next_step
                inner.commit()
        finally:
            inner.close()
        return _FakeReply("done")
    return _fake_call_agent


def _stub_task_context(text: str = "## Task context\nseeded") -> dict:
    return {
        "scope": "task",
        "query": "review prompt",
        "text": text,
        "sections": [
            {"section_type": "task_core", "title": "Task core", "content": text},
        ],
        "retrieval": {"results": [{"id": "tenant-1", "score": 0.9}]},
    }


@pytest.mark.usefixtures("db")
class TestReviewPersistsToAIConversation:
    def test_review_writes_summary_message_to_ai_conversation(self):
        task = _make_task(self.db, title="kitchen", with_ai_conversation=True)
        self.db.expunge(task)

        from handlers import task_review as mod

        with patch(
            "llm.client.call_agent",
            new=AsyncMock(
                side_effect=_stub_call_agent_that_records_review(
                    "needs_action",
                    "Quote pending; follow up tomorrow.",
                    "Ping vendor for quote status.",
                )
            ),
        ), patch("llm.registry.agent_registry.ensure_agent", return_value="agent-1"), \
             patch("llm.context.build_task_context_data", return_value=_stub_task_context()):
            asyncio.run(mod._review_one_task(task))

        self.db.expire_all()
        messages = (
            self.db.query(Message)
            .filter_by(conversation_id=task.ai_conversation_id)
            .order_by(Message.sent_at.asc())
            .all()
        )
        # Two rows: INTERNAL trace (rendered as ThinkingChain) + ACTION
        # review-card (rendered as a distinct status card, not a chat reply).
        assert len(messages) == 2, "review should post one trace + one review card"
        trace, summary = messages

        # 1. INTERNAL trace carries the reasoning bullets — chat panel
        #    renders these as a compact ThinkingChain.
        assert trace.is_ai is True
        assert trace.message_type == MessageType.INTERNAL
        assert "Reading task context" in trace.body
        assert "Checking recent activity" in trace.body

        # 2. ACTION row carries the structured review_card payload — no
        #    "Agent review" preamble or "Summary" label in the body.
        assert summary.is_ai is True
        assert summary.message_type == MessageType.ACTION
        assert summary.body == "Quote pending; follow up tomorrow."
        assert "Agent review" not in (summary.body or "")
        assert summary.meta == {
            "review_card": {
                "status": "needs_action",
                "summary": "Quote pending; follow up tomorrow.",
                "next_step": "Ping vendor for quote status.",
            }
        }

        refreshed = self.db.query(Task).filter_by(id=task.id).one()
        assert refreshed.last_message_at is None

    def test_review_logs_llm_request_trace_with_context_and_retrieval(self):
        """The trace UI surfaces system prompt + context sections + retrieval
        for routines; task_review must match so the same panel shows the
        same shape of data for review runs."""
        import json as _json

        from db.models import AgentTrace

        task = _make_task(self.db, title="trace-shape", with_ai_conversation=True)
        self.db.expunge(task)

        stub_context = {
            "scope": "account",
            "query": "review prompt",
            "text": "## Account overview\n(seeded)",
            "sections": [
                {"section_type": "retrieval", "title": "Account overview", "content": "seeded"},
            ],
            "retrieval": {"results": [{"id": "tenant-1", "score": 0.9}]},
        }

        from handlers import task_review as mod

        with patch(
            "llm.client.call_agent",
            new=AsyncMock(
                side_effect=_stub_call_agent_that_records_review("on_track", "fine", None)
            ),
        ), patch("llm.registry.agent_registry.ensure_agent", return_value="agent-1"), \
             patch("llm.context.build_task_context_data", return_value=stub_context):
            asyncio.run(mod._review_one_task(task))

        self.db.expire_all()
        from db.models import AgentRun
        traces = (
            self.db.query(AgentTrace)
            .join(
                AgentRun,
                (AgentTrace.org_id == AgentRun.org_id) & (AgentTrace.run_id == AgentRun.id),
            )
            .filter(AgentRun.task_id == str(task.id), AgentTrace.source == "task_review")
            .order_by(AgentTrace.timestamp.asc())
            .all()
        )
        types = [t.trace_type for t in traces]
        assert "llm_request" in types
        assert "llm_reply" in types

        def detail_of(trace_type: str) -> dict:
            trace = next(t for t in traces if t.trace_type == trace_type)
            return _json.loads(trace.detail) if trace.detail else {}

        req = detail_of("llm_request")
        rep = detail_of("llm_reply")

        # Both carry the full review prompt + context text we seeded.
        for detail in (req, rep):
            assert detail["flow"] == "task_review"
            assert detail["messages_payload"][0]["role"] == "system"
            assert "## Account overview" in detail["messages_payload"][0]["content"]
            assert detail["messages_payload"][1]["role"] == "user"
            assert f"task #{task.id}" in detail["messages_payload"][1]["content"].lower()
            assert detail["context"]["text"].startswith("## Account overview")
            assert any(s.get("section_type") == "retrieval" for s in detail["context"]["sections"])
            assert detail["retrieval"] == stub_context["retrieval"]
        # The reply trace specifically captures the agent's reply.
        assert rep["reply"] == "done"

    def test_review_prompt_surfaces_tenant_external_id(self):
        """_build_review_prompt must include the tenant's external_id so
        the agent doesn't guess at UUIDs (regression from a session where
        the agent mistook the lease_id for a tenant_id).
        """
        from datetime import date

        from db.models import Lease, Property, Tenant, Unit, User

        owner = User(
            org_id=1,
            creator_id=1,
            user_type="account",
            email="owner@example.com",
            active=True,
        )
        self.db.add(owner)
        self.db.flush()
        tenant_user = User(
            org_id=1,
            creator_id=owner.id,
            user_type="tenant",
            first_name="Ryan",
            last_name="Chen",
            phone="+14255550105",
            email="ryan.chen@example.com",
            active=True,
        )
        self.db.add(tenant_user)
        self.db.flush()
        tenant = Tenant(org_id=1, creator_id=owner.id, user_id=tenant_user.id)
        self.db.add(tenant)
        self.db.flush()
        prop = Property(
            id="prop-1",
            org_id=1,
            creator_id=owner.id,
            address_line1="500 Pinecrest",
            property_type="multi_family",
            source="manual",
        )
        self.db.add(prop)
        self.db.flush()
        unit = Unit(
            id="unit-1",
            org_id=1,
            creator_id=owner.id,
            property_id=prop.id,
            label="Unit 102",
            tenant_id=tenant.id,
        )
        self.db.add(unit)
        lease = Lease(
            id="lease-1",
            org_id=1,
            creator_id=owner.id,
            property_id=prop.id,
            unit_id=unit.id,
            tenant_id=tenant.id,
            start_date=date(2024, 6, 1),
            end_date=date(2025, 5, 31),
            rent_amount=1650,
        )
        self.db.add(lease)
        self.db.flush()

        task = Task(
            id=NumberAllocator.allocate_next(self.db, entity_type="task", org_id=1),
            org_id=1,
            creator_id=owner.id,
            title="Rent payment question",
            task_status=TaskStatus.ACTIVE,
            unit_id=unit.id,
            lease_id=lease.id,
        )
        self.db.add(task)
        self.db.flush()

        from handlers.task_review import _build_review_prompt

        prompt = _build_review_prompt(task, self.db)
        assert "Ryan Chen" in prompt, "tenant full name must be inlined"
        assert f"tenant_id={tenant.external_id}" in prompt, (
            "agent needs the tenant's external UUID verbatim"
        )
        # And the lease UUID must NOT be presented as a tenant identifier.
        assert "tenant_id=lease-1" not in prompt

    def test_review_uses_task_context_with_linked_conversation_transcripts(self):
        from db.models import ConversationParticipant, User

        task = _make_task(self.db, title="spring cleanup review", with_ai_conversation=True)
        ai_convo = self.db.query(Conversation).filter_by(id=task.ai_conversation_id).one()
        self.db.add(Message(
            org_id=1,
            conversation_id=ai_convo.id,
            sender_type=ParticipantType.ACCOUNT_USER,
            body="Holding the Thursday slot while I confirm with the owner.",
            message_type=MessageType.MESSAGE,
            sender_name="RentMate",
            is_ai=True,
            sent_at=datetime.now(UTC) - timedelta(minutes=5),
        ))

        vendor = User(
            org_id=1,
            creator_id=1,
            user_type="vendor",
            first_name="Alex",
            last_name="Vendor",
            phone="+12065550199",
            active=True,
        )
        self.db.add(vendor)
        self.db.flush()

        vendor_convo = Conversation(
            org_id=1,
            creator_id=1,
            subject="Spring cleanup",
            conversation_type=ConversationType.VENDOR,
            is_group=False,
            is_archived=False,
            parent_task_id=task.id,
        )
        self.db.add(vendor_convo)
        self.db.flush()
        self.db.add(ConversationParticipant(
            org_id=1,
            conversation_id=vendor_convo.id,
            user_id=vendor.id,
            participant_type=ParticipantType.EXTERNAL_CONTACT,
            creator_id=1,
            is_active=True,
        ))
        self.db.add(Message(
            org_id=1,
            conversation_id=vendor_convo.id,
            sender_type=ParticipantType.EXTERNAL_CONTACT,
            body="$545 for the first cleanup, $60/visit biweekly. I have next Thursday open.",
            message_type=MessageType.MESSAGE,
            sender_name="Alex",
            is_ai=False,
            sent_at=datetime.now(UTC) - timedelta(minutes=2),
        ))
        self.db.flush()
        self.db.expunge(task)

        captured_messages = {}

        async def _capture_call(*args, **kwargs):
            captured_messages["messages"] = kwargs.get("messages")
            return _FakeReply("done")

        from handlers import task_review as mod

        with patch("llm.client.call_agent", new=AsyncMock(side_effect=_capture_call)), \
             patch("llm.registry.agent_registry.ensure_agent", return_value="agent-1"):
            asyncio.run(mod._review_one_task(task))

        system_prompt = captured_messages["messages"][0]["content"]
        assert "Linked conversation transcripts:" in system_prompt
        assert "Vendor conversation:" in system_prompt
        assert "AI conversation:" in system_prompt
        assert "$545 for the first cleanup" in system_prompt
        assert "Holding the Thursday slot while I confirm with the owner." in system_prompt

    def test_review_prompt_requires_step_updates_before_recording_review(self):
        task = Task(
            id=NumberAllocator.allocate_next(self.db, entity_type="task", org_id=1),
            org_id=1,
            creator_id=1,
            title="Landscape spring cleanup — getting quotes",
            task_status=TaskStatus.ACTIVE,
            goal="Get two quotes and book the better landscaper.",
            steps=[
                {"key": "collect_quotes", "label": "Collect landscaper quotes", "status": "done"},
                {"key": "compare_bids", "label": "Compare bids and timing", "status": "active"},
                {"key": "book_vendor", "label": "Book the selected landscaper", "status": "pending"},
            ],
        )
        self.db.add(task)
        self.db.flush()

        from handlers.task_review import _build_review_prompt

        prompt = _build_review_prompt(task, self.db)
        assert "call `update_task_progress`" in prompt
        assert "mark that exact step done before you call `record_task_review`" in prompt
        assert "`record_task_review` only records review metadata" in prompt

    def test_review_prompt_includes_follow_up_wait_window_by_urgency(self):
        task = Task(
            id=NumberAllocator.allocate_next(self.db, entity_type="task", org_id=1),
            org_id=1,
            creator_id=1,
            title="Low urgency follow-up",
            task_status=TaskStatus.ACTIVE,
            urgency=Urgency.LOW,
        )
        self.db.add(task)
        self.db.flush()

        from handlers.task_review import _build_review_prompt

        prompt = _build_review_prompt(task, self.db)
        assert "wait about 24 hours before another follow-up" in prompt
        assert "Do not mark a confirmation/check-it-worked step done just because you sent a reminder" in prompt

    def test_review_prompt_requires_asking_manager_for_missing_concrete_values(self):
        task = Task(
            id=NumberAllocator.allocate_next(self.db, entity_type="task", org_id=1),
            org_id=1,
            creator_id=1,
            title="Payment portal follow-up",
            task_status=TaskStatus.ACTIVE,
        )
        self.db.add(task)
        self.db.flush()

        from handlers.task_review import _build_review_prompt

        prompt = _build_review_prompt(task, self.db)
        assert "Never send or stage a tenant/vendor message with bracketed placeholders" in prompt
        assert "[payment portal link]" in prompt
        assert "call `ask_manager` and get the exact value before messaging anyone" in prompt

    def test_review_prompt_routes_in_task_pm_approvals_to_ask_manager(self):
        task = Task(
            id=NumberAllocator.allocate_next(self.db, entity_type="task", org_id=1),
            org_id=1,
            creator_id=1,
            title="Landscape spring cleanup — getting quotes",
            task_status=TaskStatus.ACTIVE,
        )
        self.db.add(task)
        self.db.flush()

        from handlers.task_review import _build_review_prompt

        prompt = _build_review_prompt(task, self.db)
        assert "If the blocker is PM approval or another manager decision on this same task" in prompt
        assert "Do not create a suggestion for an in-task approval step" in prompt
        assert "use `ask_manager` in the task AI conversation" in prompt

    def test_review_skips_chat_write_when_task_has_no_ai_conversation(self):
        # Task without ai_conversation_id — review still runs, no crash, no
        # stray message rows anywhere.
        task = _make_task(self.db, title="orphan", with_ai_conversation=False)
        self.db.expunge(task)
        baseline_messages = self.db.query(Message).count()

        from handlers import task_review as mod

        with patch(
            "llm.client.call_agent",
            new=AsyncMock(
                side_effect=_stub_call_agent_that_records_review("on_track", "All good.", None)
            ),
        ), patch("llm.registry.agent_registry.ensure_agent", return_value="agent-1"), \
             patch("llm.context.build_task_context_data", return_value=_stub_task_context()):
            asyncio.run(mod._review_one_task(task))

        assert self.db.query(Message).count() == baseline_messages, (
            "orphan task review must not create any messages"
        )


@pytest.mark.usefixtures("db")
class TestTriggerEndpoint(unittest.TestCase):
    def setUp(self):
        import os

        import jwt
        from fastapi import HTTPException
        from fastapi.testclient import TestClient

        from backends.local_auth import get_org_external_id, set_request_context
        from handlers.deps import get_db
        from main import app

        async def _fake_require_user(request):
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.replace("Bearer ", "").strip():
                raise HTTPException(status_code=401, detail="Not authenticated")
            set_request_context(account_id=1, org_id=1)
            return {"account_id": 1, "org_id": 1, "uid": "1", "email": "admin@localhost"}

        self.client = TestClient(app)
        token = jwt.encode(
            {"sub": "1", "uid": "1", "org_uid": get_org_external_id(), "email": "admin@localhost"},
            os.getenv("JWT_SECRET", "rentmate-local-secret"),
            algorithm="HS256",
        )
        self.auth = {"Authorization": f"Bearer {token}"}
        app.dependency_overrides[get_db] = lambda: self.db

    def tearDown(self):
        from handlers.deps import get_db as _get_db
        from main import app as _app
        _app.dependency_overrides.pop(_get_db, None)

    def test_trigger_endpoint_streams_sse_and_writes_review(self):
        task = _make_task(self.db, title="manual", with_ai_conversation=True)
        self.db.commit()

        async def _progress_emitting_stub(*args, **kwargs):
            # Emit a progress event so the SSE stream has at least one
            # intermediate frame the test can verify.
            on_progress = kwargs.get("on_progress")
            if on_progress:
                await on_progress("Reading task context")
            stub = _stub_call_agent_that_records_review("on_track", "Looks healthy.", None)
            return await stub(*args, **kwargs)

        with patch(
            "llm.client.call_agent", new=AsyncMock(side_effect=_progress_emitting_stub),
        ), patch("llm.registry.agent_registry.ensure_agent", return_value="agent-1"), \
             patch("llm.context.build_task_context_data", return_value=_stub_task_context()):
            with self.client.stream(
                "POST", f"/api/tasks/{task.id}/review", headers=self.auth,
            ) as res:
                assert res.status_code == 200, res.text
                body = b"".join(res.iter_bytes()).decode("utf-8")

        # SSE body must contain the standard progress + done envelopes.
        assert '"type": "progress"' in body
        assert '"type": "done"' in body
        self.db.expire_all()
        refreshed = self.db.query(Task).filter_by(id=task.id).one()
        assert refreshed.last_review_status == "on_track"
        assert refreshed.last_reviewed_at is not None

    def test_trigger_endpoint_returns_404_for_missing_task(self):
        res = self.client.post("/api/tasks/99999/review", headers=self.auth)
        assert res.status_code == 404
        assert "not found" in res.json()["detail"].lower()
