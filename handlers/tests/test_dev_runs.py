"""Tests for the /dev/runs endpoint and the run_id filter on /dev/traces."""
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import jwt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backends.local_auth import get_org_external_id, set_request_context
from db.models import AgentRun, AgentTrace
from handlers.deps import get_db
from main import app


def _token() -> str:
    return jwt.encode(
        {"sub": "1", "uid": "1", "org_uid": get_org_external_id(), "email": "admin@localhost"},
        os.getenv("JWT_SECRET", "rentmate-local-secret"),
        algorithm="HS256",
    )


AUTH = {"Authorization": f"Bearer {_token()}"}


async def _fake_require_user(request):
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.replace("Bearer ", "").strip():
        raise HTTPException(status_code=401, detail="Not authenticated")
    set_request_context(account_id=1, org_id=1)
    return {"account_id": 1, "org_id": 1, "uid": "1", "email": "admin@localhost"}


@pytest.mark.usefixtures("db")
class TestDevRunsEndpoint:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        self.db = db
        self.client = TestClient(app)
        app.dependency_overrides[get_db] = lambda: self.db
        self.patcher = patch("handlers.dev.require_user", side_effect=_fake_require_user)
        self.patcher.start()
        yield
        app.dependency_overrides = {}
        self.patcher.stop()

    def _seed_run(
        self,
        *,
        run_id: str,
        source: str,
        status: str,
        task_id: str | None = None,
        conversation_id: str | None = None,
        started_at: datetime | None = None,
        duration_seconds: float = 1.5,
        input_tokens: int = 100,
        output_tokens: int = 50,
        iteration_count: int = 3,
    ) -> AgentRun:
        started = started_at or datetime.now(UTC)
        run = AgentRun(
            id=run_id,
            org_id=1,
            creator_id=1,
            source=source,
            status=status,
            task_id=task_id,
            conversation_id=conversation_id,
            agent_version="rentmate-test",
            execution_path="local",
            model="claude-sonnet-4-6",
            started_at=started,
            ended_at=started + timedelta(seconds=duration_seconds),
            iteration_count=iteration_count,
            total_input_tokens=input_tokens,
            total_output_tokens=output_tokens,
            total_cost_cents=0,
            trigger_input=f"{source} prompt",
            final_response=f"{source} reply",
        )
        self.db.add(run)
        self.db.flush()
        return run

    def _seed_trace(self, run: AgentRun, *, sequence_num: int, trace_type: str, summary: str) -> None:
        self.db.add(AgentTrace(
            id=f"trace-{run.id}-{sequence_num}",
            org_id=1,
            creator_id=1,
            timestamp=run.started_at + timedelta(milliseconds=sequence_num * 10),
            trace_type=trace_type,
            source=run.source,
            run_id=run.id,
            sequence_num=sequence_num,
            summary=summary,
            detail=None,
        ))

    def test_lists_runs_newest_first_with_trace_count(self):
        older = self._seed_run(
            run_id="run-older",
            source="chat",
            status="completed",
            task_id="42",
            conversation_id="convo-1",
            started_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        self._seed_trace(older, sequence_num=0, trace_type="tool_call", summary="t0")
        self._seed_trace(older, sequence_num=1, trace_type="tool_result", summary="t1")
        self._seed_trace(older, sequence_num=2, trace_type="llm_reply", summary="t2")
        newer = self._seed_run(
            run_id="run-newer",
            source="task_review",
            status="errored",
            task_id="42",
        )
        self._seed_trace(newer, sequence_num=0, trace_type="error", summary="t0")
        self.db.commit()

        resp = self.client.get("/dev/runs", headers=AUTH)
        assert resp.status_code == 200
        rows = resp.json()
        assert [r["id"] for r in rows] == ["run-newer", "run-older"]
        assert rows[0]["status"] == "errored"
        assert rows[0]["trace_count"] == 1
        assert rows[1]["trace_count"] == 3
        assert rows[1]["task_id"] == "42"
        assert rows[1]["conversation_id"] == "convo-1"
        assert rows[1]["duration_ms"] == 1500

    def test_filters_runs_by_status_and_source(self):
        self._seed_run(run_id="r1", source="chat", status="completed")
        self._seed_run(run_id="r2", source="routine", status="errored")
        self._seed_run(run_id="r3", source="task_review", status="errored")
        self.db.commit()

        only_errored = self.client.get("/dev/runs?status=errored", headers=AUTH).json()
        assert sorted(r["id"] for r in only_errored) == ["r2", "r3"]

        only_routine = self.client.get("/dev/runs?source=routine", headers=AUTH).json()
        assert [r["id"] for r in only_routine] == ["r2"]

    def test_traces_endpoint_filters_by_run_id_in_sequence_order(self):
        run = self._seed_run(run_id="run-x", source="chat", status="completed")
        # Insert out of timestamp order to prove sequence_num drives the sort.
        self._seed_trace(run, sequence_num=2, trace_type="llm_reply", summary="reply")
        self._seed_trace(run, sequence_num=0, trace_type="tool_call", summary="call")
        self._seed_trace(run, sequence_num=1, trace_type="tool_result", summary="result")
        # A trace on a different run that should NOT appear.
        other = self._seed_run(run_id="run-other", source="chat", status="completed")
        self._seed_trace(other, sequence_num=0, trace_type="tool_call", summary="other")
        self.db.commit()

        resp = self.client.get("/dev/traces?run_id=run-x", headers=AUTH)
        assert resp.status_code == 200
        rows = resp.json()
        assert [r["sequence_num"] for r in rows] == [0, 1, 2]
        assert [r["summary"] for r in rows] == ["call", "result", "reply"]
        assert all(r["run_id"] == "run-x" for r in rows)
