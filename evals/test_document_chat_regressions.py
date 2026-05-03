"""Eval: document chat regressions for missing-tenant lease onboarding.

Exercises the real /chat/send account-chat flow with an uploaded lease PDF and
verifies that:
- the property/unit are created once
- no placeholder tenant is created when the tenant name is missing
- the assistant asks for the tenant name before creating the tenant
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.orm import scoped_session, sessionmaker

from db.models import Base, Conversation, Document, Lease, Message, Property, Tenant, Unit
from evals.conftest import judge_message

pytestmark = pytest.mark.eval

_MISSING_TENANT_PDF = Path(__file__).resolve().parent / "sample_lease_missing_tenant.pdf"


def _init_engine(engine):
    Base.metadata.create_all(engine)


def _make_session_factory(engine):
    return scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))


async def _mock_require_user(request=None):
    from integrations.local_auth import set_request_context

    set_request_context(account_id=1, org_id=1)
    return {"id": "eval-user", "email": "eval@example.com", "account_id": 1}


@asynccontextmanager
async def _test_app(session_factory):
    import main as _main

    with (
        patch("main.SessionLocal", session_factory),
        patch("rentmate.app.SessionLocal", session_factory),
        patch("handlers.deps.SessionLocal", session_factory),
        patch("handlers.chat.SessionLocal", session_factory),
        patch("services.settings_service.is_llm_configured", return_value=True),
        patch("handlers.chat.require_user", AsyncMock(side_effect=_mock_require_user)),
        patch("handlers.documents.require_user", AsyncMock(side_effect=_mock_require_user)),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=_main.app),
            base_url="http://test",
            headers={"Authorization": "Bearer fake-token"},
            timeout=300.0,
        ) as client:
            yield client


async def _collect_sse(stream_response) -> list[dict]:
    import json

    events = []
    async for line in stream_response.aiter_lines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


async def _send_chat_turn(client: AsyncClient, *, message: str, conversation_id: str | None = None) -> list[dict]:
    payload = {"message": message}
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
    async with client.stream("POST", "/chat/send", json=payload) as resp:
        return await _collect_sse(resp)


def _done_event(events: list[dict]) -> dict:
    return next(event for event in events if event["type"] == "done")


@pytest.fixture()
def engine(isolated_engine):
    eng = isolated_engine
    _init_engine(eng)
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
def missing_tenant_document(db):
    doc = Document(
        id="eval-doc-missing-tenant",
        org_id=1,
        creator_id=1,
        filename="sample_lease_missing_tenant.pdf",
        content_type="application/pdf",
        storage_path="documents/eval-doc-missing-tenant/sample_lease_missing_tenant.pdf",
        document_type="lease",
        status="pending",
        sha256_checksum="eval-missing-tenant",
        created_at=datetime.now(UTC),
    )
    db.add(doc)
    db.commit()
    return doc


@pytest.mark.xfail(
    reason=(
        "Requires a model with stronger tool-following than qwen-flash; passes "
        "reliably on haiku-4-5 but qwen misses the create_property call on "
        "user confirmation. Flip to strict once the default LLM_MODEL catches up."
    ),
    strict=False,
)
def test_missing_tenant_lease_creates_property_once_and_requests_name(session_factory, db, missing_tenant_document):
    if not _MISSING_TENANT_PDF.exists():
        pytest.skip(f"Missing tenant PDF not found: {_MISSING_TENANT_PDF}")

    import os

    if not os.getenv("LLM_API_KEY"):
        pytest.skip("LLM_API_KEY not set")
    if "qwen" in (os.getenv("LLM_MODEL") or "").lower():
        pytest.skip("qwen-flash does not reliably follow this document-chat workflow")

    pdf_bytes = _MISSING_TENANT_PDF.read_bytes()

    async def fake_download(_storage_path: str) -> bytes:
        return pdf_bytes

    async def _run():
        with patch("integrations.wire.storage_backend.download", side_effect=fake_download):
            async with _test_app(session_factory) as client:
                upload_message = (
                    "Uploaded sample_lease_missing_tenant.pdf\n\n"
                    f"[Attached documents: {missing_tenant_document.id} (sample_lease_missing_tenant.pdf)]"
                )
                upload_events = await _send_chat_turn(client, message=upload_message)
                upload_done = _done_event(upload_events)
                conversation_id = upload_done["conversation_id"]

                confirmation_events = await _send_chat_turn(
                    client,
                    message="yes go ahead",
                    conversation_id=conversation_id,
                )

                return upload_events, confirmation_events, conversation_id

    upload_events, second_events, conversation_external_id = asyncio.run(_run())
    upload_done = _done_event(upload_events)
    upload_reply = upload_done.get("reply") or ""

    upload_judge = judge_message(
        upload_reply,
        "User uploaded a lease PDF that contains a property address and unit but is missing the tenant's name. "
        "The agent should acknowledge the upload, surface what it extracted, and ask the user for the missing tenant "
        "name (and/or confirm before creating any records). It must not silently create a placeholder tenant or "
        "claim the upload failed.",
        [
            "Acknowledges the uploaded lease document",
            "Asks the user for the missing tenant name OR asks for confirmation before creating records",
            "Does not claim the document tool failed or that nothing was created",
        ],
    )
    assert upload_judge["pass"], f"LLM judge failed on upload reply: {upload_judge['reason']}\nReply: {upload_reply}"

    second_done = _done_event(second_events)
    reply = (second_done.get("reply") or "").lower()

    conversation = db.query(Conversation).filter_by(external_id=conversation_external_id).one()

    created_properties = db.query(Property).filter(Property.address_line1.ilike("%1234 Acme Lane%")).all()
    assert len(created_properties) == 1

    created_property = created_properties[0]
    created_units = db.query(Unit).filter(Unit.property_id == created_property.id).all()
    assert len(created_units) == 1

    assert db.query(Tenant).count() == 0
    assert db.query(Lease).count() == 0

    action_messages = (
        db.query(Message)
        .filter(Message.conversation_id == conversation.id)
        .all()
    )
    property_actions = [
        msg for msg in action_messages
        if ((msg.meta or {}).get("action_card") or {}).get("kind") == "property"
    ]
    tenant_actions = [
        msg for msg in action_messages
        if ((msg.meta or {}).get("action_card") or {}).get("kind") == "tenant"
    ]
    assert len(property_actions) == 1
    assert len(tenant_actions) == 0

    assert "i did not create the document" not in reply
    assert "document tool was not executed successfully" not in reply
    assert ("tenant name" in reply) or ("full name" in reply) or ("name of the tenant" in reply)
    if "email" in reply:
        assert "phone" in reply
        assert reply.index("phone") < reply.index("email")
