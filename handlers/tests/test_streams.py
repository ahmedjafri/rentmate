"""End-to-end test for the generic /api/agent-streams/{id} reconnect endpoint."""
from __future__ import annotations

import asyncio
import json
import os
import unittest

import jwt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backends.local_auth import get_org_external_id, set_request_context
from handlers.deps import get_db
from llm.invocation import invoke_agent
from llm.streams import StreamEvent, stream_registry
from main import app


def _auth_header() -> dict[str, str]:
    token = jwt.encode(
        {"sub": "1", "uid": "1", "org_uid": get_org_external_id(), "email": "admin@localhost"},
        os.getenv("JWT_SECRET", "rentmate-local-secret"),
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


async def _fake_require_user(request):
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.replace("Bearer ", "").strip():
        raise HTTPException(status_code=401, detail="Not authenticated")
    set_request_context(account_id=1, org_id=1)
    return {"account_id": 1, "org_id": 1, "uid": "1", "email": "admin@localhost"}


@pytest.mark.usefixtures("db")
class TestAgentStreamsReconnect(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        app.dependency_overrides[get_db] = lambda: self.db

    def tearDown(self):
        app.dependency_overrides.pop(get_db, None)

    def test_subscribe_replays_history_for_finished_run(self):
        """A client that connects to a run that already terminated gets
        the full history — the core guarantee of the reconnect feature."""
        run = stream_registry.start(source="unit", stream_id="test-replay-1")

        async def populate():
            await run.emit(StreamEvent(type="progress", text="step-a"))
            await run.emit(StreamEvent(type="progress", text="step-b"))
            await run.emit(StreamEvent(type="done", payload={"reply": "ok"}))
        asyncio.run(populate())

        with self.client.stream(
            "GET", f"/api/agent-streams/{run.stream_id}", headers=_auth_header(),
        ) as res:
            assert res.status_code == 200
            body = b"".join(res.iter_bytes()).decode("utf-8")

        frames: list[dict] = [
            json.loads(line[len("data: "):])
            for line in body.splitlines()
            if line.startswith("data: ")
        ]
        kinds = [f["type"] for f in frames]
        assert kinds == ["stream_id", "progress", "progress", "done"]
        assert frames[1]["text"] == "step-a"
        assert frames[2]["text"] == "step-b"
        assert frames[3]["reply"] == "ok"

    def test_unknown_stream_id_returns_404(self):
        res = self.client.get("/api/agent-streams/does-not-exist", headers=_auth_header())
        assert res.status_code == 404
        assert "not found" in res.json()["detail"].lower()
