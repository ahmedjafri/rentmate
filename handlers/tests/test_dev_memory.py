import os
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backends.local_auth import get_org_external_id, set_request_context
from db.enums import TaskMode, TaskStatus
from db.models import AgentMemory, AgentTrace, Conversation, Message, MessageType, ParticipantType, Property, Task
from handlers.deps import get_db
from llm.retrieval import ChromaMemoryIndex
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


@pytest.mark.usefixtures("db")
class TestDevMemoryEndpoints:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        self.db = db
        self.client = TestClient(app)
        app.dependency_overrides[get_db] = lambda: self.db
        self.require_user_patcher = patch("handlers.dev.require_user", side_effect=_fake_require_user)
        self.require_user_patcher.start()
        yield
        app.dependency_overrides = {}
        self.require_user_patcher.stop()

    def test_lists_memory_items_from_index(self, tmp_path, monkeypatch):
        monkeypatch.setattr("llm.retrieval.CHROMA_PATH", tmp_path / "chroma")
        ChromaMemoryIndex().reset()

        self.db.add(Property(
            id="prop-dev-1",
            org_id=1,
            creator_id=1,
            address_line1="123 Memory Lane",
            city="Toronto",
            postal_code="M5V 1A1",
            property_type="multi_family",
            source="manual",
            context="Boiler replacement planned next quarter.",
        ))
        self.db.add(AgentMemory(
            id="mem-dev-1",
            org_id=1,
            creator_id=1,
            memory_type="note:general",
            content="Tenant prefers SMS reminders before rent is due.",
        ))
        self.db.commit()

        response = self.client.get("/dev/memory-items?query=boiler", headers=AUTH)

        assert response.status_code == 200
        items = response.json()
        assert len(items) == 1
        assert items[0]["source_type"] == "property"
        assert "Boiler replacement" in items[0]["content"]

    def test_retrieve_context_returns_ranked_results_and_reindex_endpoint(self, tmp_path, monkeypatch):
        monkeypatch.setattr("llm.retrieval.CHROMA_PATH", tmp_path / "chroma")
        ChromaMemoryIndex().reset()

        self.db.add(Property(
            id="prop-dev-2",
            org_id=1,
            creator_id=1,
            address_line1="88 Renewal Ave",
            city="Toronto",
            postal_code="M5V 2B2",
            property_type="multi_family",
            source="manual",
            context="Lease renewals here should be offered 60 days before expiry.",
        ))
        self.db.commit()

        reindex_response = self.client.post("/dev/reindex-memory", headers=AUTH)
        assert reindex_response.status_code == 200
        assert reindex_response.json()["count"] >= 1

        response = self.client.post(
            "/dev/retrieve-context",
            headers=AUTH,
            json={
                "query": "Which leases need renewal follow up in 60 days?",
                "intent": "follow_up",
                "surface": "scheduler",
                "property_id": "prop-dev-2",
                "limit": 5,
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["items"]
        top = payload["items"][0]
        assert top["source_type"] in {"property", "lease"}
        assert isinstance(top["reasons"], list)

    def test_trace_filters_and_detail_endpoint(self):
        conv = Conversation(
            org_id=1,
            creator_id=1,
            subject="Trace Chat",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self.db.add(conv)
        self.db.flush()
        task = Task(
            org_id=1,
            creator_id=1,
            title="Trace Task",
            task_status=TaskStatus.ACTIVE,
            task_mode=TaskMode.AUTONOMOUS,
            ai_conversation_id=conv.id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self.db.add(task)
        self.db.flush()
        trace = AgentTrace(
            id="trace-dev-1",
            org_id=1,
            creator_id=1,
            timestamp=datetime.now(UTC),
            trace_type="llm_reply",
            source="chat",
            task_id=str(task.id),
            conversation_id=str(conv.id),
            summary="Trace summary",
            detail='{"version":1,"kind":"llm_exchange","messages_payload":[{"role":"system","content":"ctx"},{"role":"user","content":"hi"}],"context":{"text":"ctx","sections":[{"section_type":"task_core","title":"Task core","content":"ctx"}]},"retrieval":{"request":{"query":"hi"},"items":[{"memory_item_id":"m1","title":"item","source_type":"tenant","final_score":1.2,"vector_score":0.4,"heuristic_score":0.6,"content":"hello","reasons":["match"]}]}}',
        )
        self.db.add(trace)
        self.db.add(Message(
            org_id=1,
            conversation_id=conv.id,
            sender_name="RentMate",
            sender_type=ParticipantType.ACCOUNT_USER,
            body="hello",
            message_type=MessageType.MESSAGE,
            is_ai=True,
            sent_at=datetime.now(UTC),
        ))
        self.db.commit()

        traces_response = self.client.get(
            f"/dev/traces?task_id={task.id}&conversation_id={conv.id}&trace_type=llm_reply",
            headers=AUTH,
        )
        assert traces_response.status_code == 200
        traces_payload = traces_response.json()
        assert len(traces_payload) == 1
        assert traces_payload[0]["id"] == trace.id

        tasks_response = self.client.get("/dev/trace-filters/tasks", headers=AUTH)
        assert tasks_response.status_code == 200
        assert any(str(row["id"]) == str(task.id) for row in tasks_response.json())

        chats_response = self.client.get("/dev/trace-filters/chats", headers=AUTH)
        assert chats_response.status_code == 200
        assert any(str(row["id"]) == str(conv.id) for row in chats_response.json())

        detail_response = self.client.get(f"/dev/traces/{trace.id}", headers=AUTH)
        assert detail_response.status_code == 200
        detail_payload = detail_response.json()
        assert detail_payload["id"] == trace.id
        assert detail_payload["detail"]["kind"] == "llm_exchange"
        assert detail_payload["detail"]["retrieval"]["request"]["query"] == "hi"
