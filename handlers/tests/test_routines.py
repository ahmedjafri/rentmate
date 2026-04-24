import asyncio
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backends.local_auth import get_org_external_id, set_request_context
from db.enums import RoutineState
from db.models import AgentTrace, Routine, Suggestion
from gql.services.number_allocator import NumberAllocator
from handlers.deps import get_db
from main import app


def make_token():
    import jwt

    return jwt.encode(
        {"sub": "1", "uid": "1", "org_uid": get_org_external_id(), "email": "admin@localhost"},
        os.getenv("JWT_SECRET", "rentmate-local-secret"),
        algorithm="HS256",
    )


AUTH = {"Authorization": f"Bearer {make_token()}"}


async def _fake_require_user(request):
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.replace("Bearer ", "").strip():
        raise HTTPException(status_code=401, detail="Not authenticated")
    set_request_context(account_id=1, org_id=1)
    return {"account_id": 1, "org_id": 1, "uid": "1", "email": "admin@localhost"}


def _parse_sse(response_text: str) -> list[dict]:
    events = []
    for line in response_text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


@pytest.mark.usefixtures("db")
class TestRoutineSimulation(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        app.dependency_overrides[get_db] = lambda: self.db

        task = Routine(
            id=NumberAllocator.allocate_next(self.db, entity_type="routine", org_id=1),
            org_id=1,
            creator_id=1,
            name="Sim test",
            prompt="Review expiring leases.",
            schedule="daily",
            enabled=True,
            state=RoutineState.SCHEDULED,
        )
        self.db.add(task)
        self.db.commit()
        self.task_id = task.id

        self.require_user_patcher = patch("handlers.routines.require_user", side_effect=_fake_require_user)
        self.require_user_patcher.start()

    def tearDown(self):
        app.dependency_overrides = {}
        self.require_user_patcher.stop()

    def test_simulate_streams_done_event_without_detached_instance_error(self):
        with (
            patch("llm.registry.agent_registry.ensure_agent", return_value="agent-1"),
            patch("llm.context.load_account_context", return_value="ctx"),
            patch("llm.client.call_agent", new_callable=AsyncMock) as mock_call_agent,
        ):
            async def _fake_call_agent(*args, **kwargs):
                on_progress = kwargs.get("on_progress")
                if on_progress:
                    await on_progress("Thinking...")
                return type("Resp", (), {"reply": "Would create two suggestions."})()

            mock_call_agent.side_effect = _fake_call_agent

            response = self.client.post(f"/api/routines/{self.task_id}/simulate", headers=AUTH)

        assert response.status_code == 200
        events = _parse_sse(response.text)
        assert {"type": "progress", "text": "Thinking..."} in events
        done_event = [event for event in events if event.get("type") == "done"][0]
        assert done_event["reply"] == "Would create two suggestions."
        assert done_event["actions"] == []
        assert done_event["suggestions"] == []

        self.db.expire_all()
        row = self.db.query(Routine).filter_by(id=self.task_id).first()
        assert row is not None
        assert row.simulated_at is not None

    def test_simulate_returns_suggestions_it_would_create(self):
        with (
            patch("llm.registry.agent_registry.ensure_agent", return_value="agent-1"),
            patch("llm.context.load_account_context", return_value="ctx"),
            patch("llm.client.call_agent", new_callable=AsyncMock) as mock_call_agent,
        ):
            async def _fake_call_agent(*args, **kwargs):
                from llm.tools import simulation_suggestions

                pending = simulation_suggestions.get()
                assert pending is not None
                pending.append({
                    "id": "sim-1",
                    "title": "Renewal follow-up for Alice Smith",
                    "body": "Lease expires on 2026-05-20 for Unit 2A at 123 Test St. Current rent is $2,150.",
                    "category": "leasing",
                    "urgency": "medium",
                    "property_id": "prop-123",
                    "risk_score": 3,
                    "action_payload": {
                        "tenant_name": "Alice Smith",
                        "unit_label": "2A",
                        "expiry_date": "2026-05-20",
                    },
                })
                return type("Resp", (), {"reply": "Generic plan", "side_effects": []})()

            mock_call_agent.side_effect = _fake_call_agent

            response = self.client.post(f"/api/routines/{self.task_id}/simulate", headers=AUTH)

        assert response.status_code == 200
        done_event = [event for event in _parse_sse(response.text) if event.get("type") == "done"][0]
        reply = done_event["reply"]
        # Legacy flat-shape suggestion entries are coerced to the new
        # ``{id, tool, title, body, …}`` card shape the UI renders.
        assert done_event["actions"] == [{
            "id": "sim-1",
            "tool": "create_suggestion",
            "title": "Renewal follow-up for Alice Smith",
            "body": "Lease expires on 2026-05-20 for Unit 2A at 123 Test St. Current rent is $2,150.",
            "category": "leasing",
            "urgency": "medium",
            "property_id": "prop-123",
            "task_id": None,
            "risk_score": 3,
            "action_payload": {
                "tenant_name": "Alice Smith",
                "unit_label": "2A",
                "expiry_date": "2026-05-20",
            },
        }]
        # Legacy key mirrors ``actions`` for older UI bundles.
        assert done_event["suggestions"] == done_event["actions"]
        assert "I would create 1 suggestion" in reply
        assert "Renewal follow-up for Alice Smith" in reply
        assert "Lease expires on 2026-05-20" in reply
        assert '"tenant_name": "Alice Smith"' in reply

    def test_simulate_parses_reply_bullets_into_suggestions(self):
        with (
            patch("llm.registry.agent_registry.ensure_agent", return_value="agent-1"),
            patch("llm.context.load_account_context", return_value="ctx"),
            patch("llm.client.call_agent", new_callable=AsyncMock) as mock_call_agent,
        ):
            async def _fake_call_agent(*args, **kwargs):
                return type("Resp", (), {
                    "reply": (
                        "I've created three suggestions for you to review:\n\n"
                        "1. **Review property information completeness** - Check all properties for complete addresses\n"
                        "2. **Check tenant contact information** - Review all tenant profiles for missing phone numbers\n"
                        "3. **Review expired and expiring leases** - Identify leases that have expired or will expire within 30 days\n"
                    ),
                    "side_effects": [],
                })()

            mock_call_agent.side_effect = _fake_call_agent

            response = self.client.post(f"/api/routines/{self.task_id}/simulate", headers=AUTH)

        assert response.status_code == 200
        done_event = [event for event in _parse_sse(response.text) if event.get("type") == "done"][0]
        assert [item["title"] for item in done_event["suggestions"]] == [
            "Review property information completeness",
            "Check tenant contact information",
            "Review expired and expiring leases",
        ]

    def test_simulate_does_not_persist_suggestions(self):
        before_count = self.db.query(Suggestion).count()

        with (
            patch("llm.registry.agent_registry.ensure_agent", return_value="agent-1"),
            patch("llm.context.load_account_context", return_value="ctx"),
            patch("llm.client.call_agent", new_callable=AsyncMock) as mock_call_agent,
        ):
            async def _fake_call_agent(*args, **kwargs):
                from llm.tools import simulation_suggestions

                pending = simulation_suggestions.get()
                assert pending is not None
                pending.append({
                    "id": "sim-1",
                    "title": "Would create this suggestion",
                    "body": "Dry run only.",
                })
                return type("Resp", (), {"reply": "ok", "side_effects": []})()

            mock_call_agent.side_effect = _fake_call_agent

            response = self.client.post(f"/api/routines/{self.task_id}/simulate", headers=AUTH)

        assert response.status_code == 200
        assert self.db.query(Suggestion).count() == before_count

    def test_execute_routine_works_with_detached_instance(self):
        detached = self.db.query(Routine).filter_by(id=self.task_id).one()
        self.db.expunge(detached)

        with (
            patch("llm.registry.agent_registry.ensure_agent", return_value="agent-1"),
            patch("llm.context.load_account_context", return_value="ctx"),
            patch("llm.client.call_agent", new_callable=AsyncMock) as mock_call_agent,
        ):
            async def _fake_call_agent(*args, **kwargs):
                return type("Resp", (), {"reply": "ran", "side_effects": []})()

            mock_call_agent.side_effect = _fake_call_agent

            from handlers.routines import execute_routine

            reply = asyncio.run(execute_routine(detached))

        assert reply == "ran"

    def test_execute_routine_logs_traces_for_simulation_runs(self):
        detached = self.db.query(Routine).filter_by(id=self.task_id).one()
        self.db.expunge(detached)

        with (
            patch("llm.registry.agent_registry.ensure_agent", return_value="agent-1"),
            patch("llm.context.load_account_context", return_value="ctx"),
            patch("llm.client.call_agent", new_callable=AsyncMock) as mock_call_agent,
        ):
            async def _fake_call_agent(*args, **kwargs):
                return type("Resp", (), {"reply": "simulated run", "side_effects": []})()

            mock_call_agent.side_effect = _fake_call_agent

            from handlers.routines import execute_routine

            reply = asyncio.run(execute_routine(detached, session_prefix="simulate"))

        assert reply == "simulated run"
        traces = (
            self.db.query(AgentTrace)
            .filter(AgentTrace.task_id == str(self.task_id))
            .order_by(AgentTrace.timestamp.asc())
            .all()
        )
        assert [trace.trace_type for trace in traces] == ["llm_request", "llm_reply"]
        assert all(trace.source == "simulate" for trace in traces)

    def test_run_streams_progress_and_updates_task(self):
        with (
            patch("llm.registry.agent_registry.ensure_agent", return_value="agent-1"),
            patch("llm.context.load_account_context", return_value="ctx"),
            patch("llm.client.call_agent", new_callable=AsyncMock) as mock_call_agent,
        ):
            async def _fake_call_agent(*args, **kwargs):
                on_progress = kwargs.get("on_progress")
                if on_progress:
                    await on_progress("Checking leases")
                return type("Resp", (), {"reply": "Created 2 renewal suggestions.", "side_effects": []})()

            mock_call_agent.side_effect = _fake_call_agent

            response = self.client.post(f"/api/routines/{self.task_id}/run", headers=AUTH)

        assert response.status_code == 200
        events = _parse_sse(response.text)
        assert {"type": "progress", "text": "Checking leases"} in events
        done_event = [event for event in events if event.get("type") == "done"][0]
        assert done_event["reply"] == "Created 2 renewal suggestions."
        assert done_event["task"]["lastStatus"] == "ok"

        self.db.expire_all()
        row = self.db.query(Routine).filter_by(id=self.task_id).first()
        assert row is not None
        assert row.last_status == "ok"
        assert row.last_output == "Created 2 renewal suggestions."
        assert row.completed_count == 1


@pytest.mark.usefixtures("db")
class TestSeedDefaultRoutines(unittest.TestCase):
    """seed_default_routines must be idempotent AND sync drift from code.

    It should:
      - insert missing default routines on first call;
      - no-op when the code and DB already agree;
      - update prompt/schedule/schedule_display when the code drifts;
      - preserve user-mutable state (enabled, state, completed_count) across
        updates.
    """

    def setUp(self):
        from backends.local_auth import DEFAULT_ORG_ID
        from db.models import User

        # SessionLocal is monkey-patched by the test harness to return self.db,
        # so the seed function will operate on this session.
        self.account = self.db.query(User).first()
        if self.account is None:
            self.account = User(
                id=1,
                org_id=DEFAULT_ORG_ID,
                external_id="seed-test-user",
                email="seed@example.com",
                user_type="account",
                active=True,
            )
            self.db.add(self.account)
            self.db.flush()

    def _run_seed(self):
        from handlers.routines import seed_default_routines
        seed_default_routines()
        # seed_default_routines commits, which expires instances — re-query.
        self.db.expire_all()

    def test_first_run_inserts_defaults(self):
        assert self.db.query(Routine).count() == 0
        self._run_seed()
        rows = self.db.query(Routine).filter_by(is_default=True).all()
        assert len(rows) > 0
        # Every default starts paused/disabled so nothing fires before the
        # manager opts in.
        for r in rows:
            assert r.enabled is False
            assert r.state == RoutineState.PAUSED

    def test_second_run_is_noop_when_in_sync(self):
        self._run_seed()
        before = {r.id: (r.prompt, r.schedule, r.updated_at) for r in self.db.query(Routine).all()}
        self._run_seed()
        after = {r.id: (r.prompt, r.schedule, r.updated_at) for r in self.db.query(Routine).all()}
        assert before == after

    def test_drifted_prompt_is_synced_from_code(self):
        self._run_seed()
        target_name = "Tenant satisfaction check-in"
        routine = self.db.query(Routine).filter_by(name=target_name, is_default=True).first()
        assert routine is not None
        canonical_prompt = routine.prompt

        # Simulate drift: a prior version of the code had a different prompt
        # now baked into the DB. On next startup the seed should re-sync it.
        routine.prompt = "OLD prompt that no longer matches the code."
        routine.enabled = True           # user-state we want to preserve
        routine.completed_count = 7       # user-state we want to preserve
        self.db.flush()

        self._run_seed()

        routine = self.db.query(Routine).filter_by(name=target_name, is_default=True).first()
        assert routine.prompt == canonical_prompt, "seed did not re-sync drifted prompt"
        assert routine.enabled is True, "seed clobbered user-toggled enabled flag"
        assert routine.completed_count == 7, "seed clobbered user-visible run counter"

    def test_schedule_drift_recomputes_next_run(self):
        from datetime import UTC, datetime

        self._run_seed()
        target_name = "Tenant satisfaction check-in"
        routine = self.db.query(Routine).filter_by(name=target_name, is_default=True).first()
        assert routine is not None

        # Replace both the schedule AND next_run_at with bogus values so we
        # can assert that the seed put next_run_at back on the canonical
        # cadence (rather than leaving the drifted timestamp in place).
        bogus_next_run = datetime(2099, 1, 1, tzinfo=UTC)
        routine.schedule = "0 0 1 1 *"
        routine.next_run_at = bogus_next_run
        self.db.flush()

        self._run_seed()

        routine = self.db.query(Routine).filter_by(name=target_name, is_default=True).first()
        assert routine.schedule != "0 0 1 1 *", "schedule drift was not reverted"
        # Use naive compare — Routine.next_run_at is stored without tz in some
        # configs; the only property we care about is that it is not the
        # bogus value we planted before reseed.
        assert routine.next_run_at is not None
        assert routine.next_run_at.year != 2099, "next_run_at should have been recomputed from the restored schedule"


class TestSimulationFormatter(unittest.TestCase):
    """_format_simulation_reply partitions actions by tool and renders per-tool
    sections in a stable order. It is the formatter used on the Simulate
    endpoint — changing its output is a product-visible change, so the test
    pins the exact section boundaries and order.
    """

    def test_empty_actions_falls_back_to_agent_reply(self):
        from handlers.routines import _format_simulation_reply

        assert _format_simulation_reply("hello", []) == "hello"
        assert _format_simulation_reply("", []) == "(no output)"

    def test_mixed_actions_render_in_stable_order(self):
        from handlers.routines import _format_simulation_reply

        actions = [
            # Intentionally shuffled so the test exercises the canonical order
            # rather than insertion order.
            {
                "id": "sim-create_suggestion-1",
                "tool": "create_suggestion",
                "args": {"title": "Renew lease at 100 Main", "category": "leases"},
            },
            {
                "id": "sim-create_property-1",
                "tool": "create_property",
                "args": {"name": "100 Main"},
            },
            {
                "id": "sim-message_person-1",
                "tool": "message_person",
                "args": {
                    "entity_name": "Tyler Brooks",
                    "entity_type": "tenant",
                    "draft_message": "Hi Tyler, just checking in.",
                },
            },
            {
                "id": "sim-message_person-2",
                "tool": "message_person",
                "args": {
                    "entity_name": "Nadia Kim",
                    "entity_type": "tenant",
                    "draft_message": "Hi Nadia — how's everything?",
                },
            },
            {
                "id": "sim-brand_new_tool-1",
                "tool": "brand_new_tool",
                "args": {"title": "hypothetical"},
            },
        ]

        rendered = _format_simulation_reply("ignored agent reply", actions)

        # message_person comes first (highest in the canonical order).
        assert rendered.startswith("I would message 2 people directly:")
        assert 'Tyler Brooks — "Hi Tyler, just checking in."' in rendered
        assert 'Nadia Kim — "Hi Nadia — how\'s everything?"' in rendered

        # create_property is defaulted (no tailored renderer) and precedes
        # create_suggestion in the canonical order.
        msg_idx = rendered.index("I would message")
        prop_idx = rendered.index("I would call `create_property`")
        sugg_idx = rendered.index("I would create 1 suggestion")
        assert msg_idx < prop_idx < sugg_idx

        # Unknown tools render via the default renderer after the canonical
        # sections.
        unknown_idx = rendered.index("I would call `brand_new_tool`")
        assert unknown_idx > sugg_idx

    def test_legacy_suggestion_shape_is_normalised(self):
        from handlers.routines import _format_simulation_reply

        # Older callers (e.g. the reply-parse fallback) yield flat suggestion
        # dicts without a "tool" key. The formatter must coerce them into a
        # create_suggestion action so the output isn't blank.
        actions = [
            {"id": "reply-1", "title": "Renew lease", "body": "Discuss with tenant."},
        ]
        rendered = _format_simulation_reply("ignored", actions)
        assert "I would create 1 suggestion:" in rendered
        assert "Renew lease" in rendered
        assert "Discuss with tenant." in rendered
