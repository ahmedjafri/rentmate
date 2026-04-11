import json
import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from backends.local_auth import get_org_external_id, set_request_context
from db.models import ScheduledTask, Suggestion
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


class TestScheduledTaskSimulate(unittest.TestCase):
    def setUp(self):
        from db.session import SessionLocal

        self.client = TestClient(app)
        self.db = SessionLocal.session_factory()
        self.db_close_patcher = patch.object(self.db, "close", lambda: None)
        self.db_close_patcher.start()

        task = ScheduledTask(
            org_id=1,
            creator_id=1,
            name="Sim test",
            prompt="Review expiring leases.",
            schedule="daily",
            enabled=True,
            state="scheduled",
        )
        self.db.add(task)
        self.db.commit()
        self.task_id = task.id

        self.require_user_patcher = patch("handlers.scheduler.require_user", side_effect=_fake_require_user)
        self.require_user_patcher.start()
        self.session_local_patcher = patch("db.session.SessionLocal", return_value=self.db)
        self.session_local_patcher.start()

    def tearDown(self):
        self.session_local_patcher.stop()
        self.require_user_patcher.stop()
        self.db_close_patcher.stop()
        self.db.rollback()
        self.db.close()

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

            response = self.client.post(f"/api/scheduled-task/{self.task_id}/simulate", headers=AUTH)

        assert response.status_code == 200
        events = _parse_sse(response.text)
        assert {"type": "progress", "text": "Thinking..."} in events
        assert {"type": "done", "reply": "Would create two suggestions.", "suggestions": []} in events

        self.db.expire_all()
        row = self.db.query(ScheduledTask).filter_by(id=self.task_id).first()
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

            response = self.client.post(f"/api/scheduled-task/{self.task_id}/simulate", headers=AUTH)

        assert response.status_code == 200
        done_event = [event for event in _parse_sse(response.text) if event.get("type") == "done"][0]
        reply = done_event["reply"]
        assert done_event["suggestions"] == [{
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
        }]
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

            response = self.client.post(f"/api/scheduled-task/{self.task_id}/simulate", headers=AUTH)

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

            response = self.client.post(f"/api/scheduled-task/{self.task_id}/simulate", headers=AUTH)

        assert response.status_code == 200
        assert self.db.query(Suggestion).count() == before_count
