import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from db.enums import TaskMode
from db.models import (
    Conversation,
    ConversationType,
    Document,
    DocumentTag,
    EntityNote,
    Message,
    MessageType,
    Notification,
    ParticipantType,
    Property,
    Suggestion,
    Task,
    Tenant,
    Unit,
    User,
)
from gql.services.number_allocator import NumberAllocator
from llm.generated_documents import RenderedDocument
from llm.tools import (
    CreateDocumentTool,
    CreatePropertyTool,
    CreateSuggestionTool,
    CreateTenantTool,
    CreateVendorTool,
    LookupPropertiesTool,
    LookupTenantsTool,
    LookupVendorsTool,
    MessageExternalPersonTool,
    ProposeTaskTool,
    RecallMemoryTool,
    SaveMemoryTool,
    UpdateTaskProgressTool,
    active_conversation_id,
    current_user_message,
    pending_suggestion_messages,
)


def _skip_if_weasyprint_native_deps_missing(error: Exception) -> None:
    if "libpangoft2" in str(error) or "WeasyPrint could not import some external libraries" in str(error):
        pytest.skip(f"WeasyPrint native dependencies unavailable: {error}")


def _run_tool(tool, **kwargs):
    return asyncio.run(tool.execute(**kwargs))


def _wa_notice_legal_requirements(**overrides):
    payload = {
        "jurisdiction": "Washington, USA",
        "citation": "RCW 59.18.057",
        "reason": (
            "Washington's statutory 14-day pay-or-vacate form requires the owner/landlord name "
            "and the address where the amount due must be paid."
        ),
        "required_fields": [
            "owner/landlord name",
            "address where the amount due must be paid",
        ],
        "missing_fields": [],
        "low_confidence_fields": [],
    }
    payload.update(overrides)
    return payload


def test_lookup_vendors_returns_external_ids(db):
    vendor = User(
        org_id=1,
        creator_id=1,
        user_type="vendor",
        first_name="Vera",
        last_name="Vendor",
        role_label="Plumber",
        phone="+15550001111",
        email="vera@example.com",
    )
    db.add(vendor)
    db.flush()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        payload = json.loads(_run_tool(LookupVendorsTool()))

    assert payload["vendors"][0]["id"] == vendor.external_id
    assert payload["vendors"][0]["name"] == vendor.name


def test_create_vendor_returns_external_id(db):
    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        payload = json.loads(_run_tool(
            CreateVendorTool(),
            name="Pat Plumber",
            phone="+15550002222",
            vendor_type="Plumber",
            email="pat@example.com",
        ))

    assert payload["status"] == "ok"
    vendor = db.query(User).filter_by(external_id=payload["vendor_id"], user_type="vendor").one()
    assert vendor.name == "Pat Plumber"


def test_create_tenant_returns_external_id(db):
    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        payload = json.loads(_run_tool(
            CreateTenantTool(),
            first_name="Tina",
            last_name="Tenant",
            email="tina@example.com",
            phone="+15550003333",
        ))

    assert payload["status"] == "ok"
    tenant = db.query(Tenant).filter_by(external_id=payload["tenant_id"]).one()
    assert tenant.user.email == "tina@example.com"


def test_create_tenant_rejects_placeholder_name(db):
    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        payload = json.loads(_run_tool(
            CreateTenantTool(),
            first_name="Tenant",
            last_name="Unknown",
        ))

    assert payload["status"] == "error"
    assert "Tenant name is required" in payload["message"]
    assert db.query(Tenant).count() == 0


def test_create_property_queues_action_card_message(db):
    conv_token = active_conversation_id.set("conv-123")
    pending_token = pending_suggestion_messages.set([])
    try:
        with patch("db.session.SessionLocal.session_factory", return_value=db), \
             patch.object(db, "close", lambda: None):
            payload = json.loads(_run_tool(
                CreatePropertyTool(),
                address="123 Test St",
                property_type="multi_family",
                unit_labels=["1A", "1B"],
            ))
    finally:
        queued = pending_suggestion_messages.get() or []
        pending_suggestion_messages.reset(pending_token)
        active_conversation_id.reset(conv_token)

    assert payload["status"] == "ok"
    assert queued[0]["message_type"].name == "ACTION"
    assert queued[0]["meta"]["action_card"]["kind"] == "property"
    assert queued[0]["meta"]["action_card"]["units"] == [
        {"uid": payload["units"][0]["id"], "label": "1A", "property_id": payload["property_id"]},
        {"uid": payload["units"][1]["id"], "label": "1B", "property_id": payload["property_id"]},
    ]


def test_create_property_dedups_same_address(db):
    """Second call with the same address returns already_exists, not a duplicate row."""
    pending_token = pending_suggestion_messages.set([])
    try:
        with patch("db.session.SessionLocal.session_factory", return_value=db), \
             patch.object(db, "close", lambda: None):
            first = json.loads(_run_tool(
                CreatePropertyTool(),
                address="  500 Dedup Ln  ",
                property_type="multi_family",
                unit_labels=["A"],
            ))
            second = json.loads(_run_tool(
                CreatePropertyTool(),
                address="500 dedup ln",
                property_type="multi_family",
                unit_labels=["A"],
            ))
    finally:
        pending_suggestion_messages.reset(pending_token)

    assert first["status"] == "ok"
    assert second["status"] == "already_exists"
    assert second["property_id"] == first["property_id"]
    assert [u["id"] for u in second["units"]] == [u["id"] for u in first["units"]]
    assert db.query(Property).filter(
        Property.address_line1.ilike("%dedup%")
    ).count() == 1


def test_create_tenant_queues_action_card_message(db):
    conv_token = active_conversation_id.set("conv-456")
    pending_token = pending_suggestion_messages.set([])
    try:
        with patch("db.session.SessionLocal.session_factory", return_value=db), \
             patch.object(db, "close", lambda: None):
            payload = json.loads(_run_tool(
                CreateTenantTool(),
                first_name="Tina",
                last_name="Tenant",
                email="tina@example.com",
            ))
    finally:
        queued = pending_suggestion_messages.get() or []
        pending_suggestion_messages.reset(pending_token)
        active_conversation_id.reset(conv_token)

    assert payload["status"] == "ok"
    assert queued[0]["message_type"].name == "ACTION"
    assert queued[0]["meta"]["action_card"]["kind"] == "tenant"
    assert queued[0]["meta"]["action_card"]["links"][0] == {
        "label": "Open tenant",
        "entity_type": "tenant",
        "entity_id": payload["tenant_id"],
    }


def test_update_task_progress_marks_steps_done_and_allows_close(db):
    task = Task(
        id=NumberAllocator.allocate_next(db, entity_type="task", org_id=1),
        org_id=1,
        creator_id=1,
        title="Send portal link",
        goal="Send the updated payment portal link and confirm the tenant is set.",
        task_mode=TaskMode.MANUAL,
        steps=[
            {"key": "send_link", "label": "Send the updated payment portal link", "status": "active"},
            {"key": "confirm_payment", "label": "Confirm next month's payment plan", "status": "pending"},
        ],
    )
    db.add(task)
    db.commit()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        first = json.loads(_run_tool(
            UpdateTaskProgressTool(),
            task_id=str(task.id),
            step_key="send_link",
            status="done",
        ))
        second = json.loads(_run_tool(
            UpdateTaskProgressTool(),
            task_id=str(task.id),
            step_key="confirm_payment",
            status="done",
        ))
        closed = json.loads(_run_tool(
            __import__("llm.tools", fromlist=["CloseTaskTool"]).CloseTaskTool(),
            task_id=str(task.id),
        ))

    assert first["status"] == "ok"
    assert second["status"] == "ok"
    db.refresh(task)
    assert [step["status"] for step in task.steps] == ["done", "done"]
    assert closed["status"] == "ok"


def test_update_task_progress_rejects_confirmation_step_without_external_confirmation(db):
    task = Task(
        id=NumberAllocator.allocate_next(db, entity_type="task", org_id=1),
        org_id=1,
        creator_id=1,
        title="Garbage disposal repair",
        goal="Confirm the garbage disposal works after repair.",
        task_mode=TaskMode.MANUAL,
        steps=[
            {"key": "confirm_disposal", "label": "Confirm the disposal works after repair", "status": "active"},
        ],
    )
    db.add(task)
    db.commit()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        result = json.loads(_run_tool(
            UpdateTaskProgressTool(),
            task_id=str(task.id),
            step_key="confirm_disposal",
            status="done",
        ))

    assert result["status"] == "error"
    assert "actually been received" in result["message"]


def test_update_task_progress_allows_confirmation_step_after_affirmative_reply(db):
    from datetime import UTC, datetime

    from db.models import Conversation, ConversationParticipant

    tenant_user = User(
        org_id=1,
        creator_id=1,
        user_type="tenant",
        first_name="Devon",
        last_name="Tenant",
        active=True,
    )
    db.add(tenant_user)
    db.flush()
    tenant = Tenant(org_id=1, creator_id=1, user_id=tenant_user.id)
    db.add(tenant)
    db.flush()

    task = Task(
        id=NumberAllocator.allocate_next(db, entity_type="task", org_id=1),
        org_id=1,
        creator_id=1,
        title="Garbage disposal repair",
        goal="Confirm the garbage disposal works after repair.",
        task_mode=TaskMode.MANUAL,
        steps=[
            {"key": "confirm_disposal", "label": "Confirm the disposal works after repair", "status": "active"},
        ],
    )
    db.add(task)
    db.flush()

    convo = Conversation(
        org_id=1,
        creator_id=1,
        subject="Garbage disposal follow-up",
        conversation_type=ConversationType.TENANT,
        is_group=False,
        is_archived=False,
        parent_task_id=task.id,
    )
    db.add(convo)
    db.flush()
    task.parent_conversation_id = convo.id
    db.add(ConversationParticipant(
        org_id=1,
        conversation_id=convo.id,
        user_id=tenant.user_id,
        participant_type=ParticipantType.TENANT,
        creator_id=1,
        is_active=True,
    ))
    db.add(Message(
        org_id=1,
        conversation_id=convo.id,
        sender_type=ParticipantType.TENANT,
        body="Yes, it's working now. Thanks!",
        message_type=MessageType.MESSAGE,
        sender_name="Devon",
        is_ai=False,
        sent_at=datetime.now(UTC),
    ))
    db.commit()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        result = json.loads(_run_tool(
            UpdateTaskProgressTool(),
            task_id=str(task.id),
            step_key="confirm_disposal",
            status="done",
        ))

    assert result["status"] == "ok", result


def test_create_document_tool_creates_document_and_queues_action_card(db):
    property_row = Property(
        id="prop-doc-1",
        org_id=1,
        creator_id=1,
        address_line1="123 Test St",
        property_type="single_family",
        source="manual",
    )
    unit_row = Unit(
        id="unit-doc-1",
        org_id=1,
        creator_id=1,
        property_id=property_row.id,
        label="Main",
    )
    tenant_user = User(
        org_id=1,
        creator_id=1,
        user_type="tenant",
        first_name="Bob",
        last_name="Ferguson",
        active=True,
    )
    db.add_all([property_row, unit_row, tenant_user])
    db.flush()
    tenant = Tenant(org_id=1, creator_id=1, user_id=tenant_user.id)
    db.add(tenant)
    db.flush()

    conv_token = active_conversation_id.set("conv-document")
    pending_token = pending_suggestion_messages.set([])
    try:
        with patch("db.session.SessionLocal.session_factory", return_value=db), \
             patch.object(db, "close", lambda: None), \
             patch("backends.wire.storage_backend.upload", new_callable=AsyncMock) as upload_mock, \
             patch("llm.generated_documents.render_document", return_value=RenderedDocument(
                 html="<html><body><p>Tenant: Bob Ferguson</p></body></html>",
                 pdf_bytes=b"%PDF-1.4 fake pdf",
                 renderer="weasyprint",
             )):
            payload = json.loads(_run_tool(
                CreateDocumentTool(),
                title="14-Day Pay or Vacate Notice",
                content="Tenant: Bob Ferguson\nAmount due: $5,590",
                document_type="notice",
                legal_requirements=_wa_notice_legal_requirements(),
                property_id=property_row.id,
                unit_id=unit_row.id,
                tenant_id=tenant.external_id,
            ))
    finally:
        queued = pending_suggestion_messages.get() or []
        pending_suggestion_messages.reset(pending_token)
        active_conversation_id.reset(conv_token)

    assert payload["status"] == "ok"
    doc = db.query(Document).filter_by(id=payload["document_id"]).one()
    assert doc.filename.endswith(".pdf")
    assert doc.document_type == "notice"
    assert doc.status == "done"
    assert doc.extraction_meta["source"] == "agent_generated"
    assert doc.extraction_meta["generated_pdf_renderer"] == "weasyprint"
    assert doc.extraction_meta["generated_html_storage_path"].endswith("/source.html")
    assert upload_mock.await_count == 2
    first_upload = upload_mock.await_args_list[0].kwargs
    second_upload = upload_mock.await_args_list[1].kwargs
    assert first_upload["data"].startswith(b"%PDF-")
    assert second_upload["data"] == b"<html><body><p>Tenant: Bob Ferguson</p></body></html>"

    tags = db.query(DocumentTag).filter_by(document_id=doc.id).all()
    assert {tag.tag_type for tag in tags} == {"property", "unit", "tenant"}

    assert queued[0]["message_type"].name == "ACTION"
    assert queued[0]["meta"]["action_card"]["kind"] == "document"
    assert queued[0]["meta"]["action_card"]["links"][0]["label"] == "Download PDF"
    assert queued[0]["meta"]["action_card"]["links"][0]["entity_type"] == "document"


def test_create_document_tool_makes_filename_unique(db):
    db.add(Document(
        id="existing-generated-doc",
        org_id=1,
        creator_id=1,
        filename="Notice.pdf",
        content_type="application/pdf",
        storage_path="generated-documents/existing-generated-doc/Notice.pdf",
        document_type="notice",
        status="done",
        progress="generated",
    ))
    db.commit()

    conv_token = active_conversation_id.set("conv-document-unique")
    pending_token = pending_suggestion_messages.set([])
    try:
        with patch("db.session.SessionLocal.session_factory", return_value=db), \
             patch.object(db, "close", lambda: None), \
             patch("backends.wire.storage_backend.upload", new_callable=AsyncMock), \
             patch("llm.generated_documents.render_document", return_value=RenderedDocument(
                 html="<html><body><p>Tenant: Bob Ferguson</p></body></html>",
                 pdf_bytes=b"%PDF-1.4 fake pdf",
                 renderer="weasyprint",
             )):
            payload = json.loads(_run_tool(
                CreateDocumentTool(),
                title="Notice",
                content="Tenant: Bob Ferguson",
                filename="Notice.pdf",
                document_type="notice",
                legal_requirements=_wa_notice_legal_requirements(),
            ))
    finally:
        pending_suggestion_messages.reset(pending_token)
        active_conversation_id.reset(conv_token)

    assert payload["status"] == "ok"
    assert payload["filename"] == "Notice-1.pdf"
    doc = db.query(Document).filter_by(id=payload["document_id"]).one()
    assert doc.filename == "Notice-1.pdf"


def test_create_document_tool_real_render_end_to_end(db):
    property_row = Property(
        id="prop-doc-e2e",
        org_id=1,
        creator_id=1,
        address_line1="123 Test St",
        property_type="single_family",
        source="manual",
    )
    unit_row = Unit(
        id="unit-doc-e2e",
        org_id=1,
        creator_id=1,
        property_id=property_row.id,
        label="Main",
    )
    tenant_user = User(
        org_id=1,
        creator_id=1,
        user_type="tenant",
        first_name="Bob",
        last_name="Ferguson",
        active=True,
    )
    db.add_all([property_row, unit_row, tenant_user])
    db.flush()
    tenant = Tenant(org_id=1, creator_id=1, user_id=tenant_user.id)
    db.add(tenant)
    db.flush()

    conv_token = active_conversation_id.set("conv-document-e2e")
    pending_token = pending_suggestion_messages.set([])
    try:
        with patch("db.session.SessionLocal.session_factory", return_value=db), \
             patch.object(db, "close", lambda: None), \
             patch("backends.wire.storage_backend.upload", new_callable=AsyncMock) as upload_mock:
            try:
                payload = json.loads(_run_tool(
                    CreateDocumentTool(),
                    title="14-Day Pay or Vacate Notice",
                    content=(
                        "Tenant: Bob Ferguson\n\n"
                        "Property: 123 Test St\n\n"
                        "Amount due: $5,590\n\n"
                        "This is a real tool-path render smoke test."
                    ),
                    document_type="notice",
                    legal_requirements=_wa_notice_legal_requirements(),
                    property_id=property_row.id,
                    unit_id=unit_row.id,
                    tenant_id=tenant.external_id,
                ))
            except RuntimeError as error:
                _skip_if_weasyprint_native_deps_missing(error)
                raise
    finally:
        queued = pending_suggestion_messages.get() or []
        pending_suggestion_messages.reset(pending_token)
        active_conversation_id.reset(conv_token)

    assert payload["status"] == "ok"
    doc = db.query(Document).filter_by(id=payload["document_id"]).one()
    assert doc.filename.endswith(".pdf")
    assert doc.document_type == "notice"
    assert doc.status == "done"
    assert doc.extraction_meta["source"] == "agent_generated"
    assert doc.extraction_meta["generated_pdf_renderer"] == "weasyprint"
    assert upload_mock.await_count == 2
    first_upload = upload_mock.await_args_list[0].kwargs
    second_upload = upload_mock.await_args_list[1].kwargs
    assert first_upload["data"].startswith(b"%PDF-")
    assert len(first_upload["data"]) > 1000
    assert b"<!DOCTYPE html>" in second_upload["data"]
    assert b"Bob Ferguson" in second_upload["data"]

    tags = db.query(DocumentTag).filter_by(document_id=doc.id).all()
    assert {tag.tag_type for tag in tags} == {"property", "unit", "tenant"}

    assert queued[0]["message_type"].name == "ACTION"
    assert queued[0]["meta"]["action_card"]["kind"] == "document"
    assert queued[0]["meta"]["action_card"]["links"][0]["label"] == "Download PDF"


def test_create_document_tool_blocks_notice_when_legal_fields_are_missing(db):
    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None), \
         patch("backends.wire.storage_backend.upload", new_callable=AsyncMock) as upload_mock, \
         patch("llm.generated_documents.render_document", return_value=RenderedDocument(
             html="<html><body><p>Should not render</p></body></html>",
             pdf_bytes=b"%PDF-1.4 fake pdf",
             renderer="weasyprint",
         )):
        payload = json.loads(_run_tool(
            CreateDocumentTool(),
            title="14-Day Pay or Vacate Notice",
            content="Tenant: Bob Ferguson\nAmount due: $5,590",
            document_type="notice",
            legal_requirements=_wa_notice_legal_requirements(
                missing_fields=["owner/landlord name", "address where the amount due must be paid"],
            ),
        ))

    assert payload["status"] == "error"
    assert "RCW 59.18.057" in payload["message"]
    assert "owner/landlord name" in payload["message"]
    assert "address where the amount due must be paid" in payload["message"]
    assert upload_mock.await_count == 0


def test_create_document_tool_blocks_notice_without_legal_preflight(db):
    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None), \
         patch("backends.wire.storage_backend.upload", new_callable=AsyncMock) as upload_mock, \
         patch("llm.generated_documents.render_document", return_value=RenderedDocument(
             html="<html><body><p>Should not render</p></body></html>",
             pdf_bytes=b"%PDF-1.4 fake pdf",
             renderer="weasyprint",
         )):
        payload = json.loads(_run_tool(
            CreateDocumentTool(),
            title="14-Day Pay or Vacate Notice",
            content="Tenant: Bob Ferguson\nAmount due: $5,590",
            document_type="notice",
        ))

    assert payload["status"] == "error"
    assert "governing law" in payload["message"]
    assert "required" in payload["message"]
    assert upload_mock.await_count == 0


def test_create_document_tool_blocks_compliance_document_without_legal_preflight(db):
    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None), \
         patch("backends.wire.storage_backend.upload", new_callable=AsyncMock) as upload_mock, \
         patch("llm.generated_documents.render_document", return_value=RenderedDocument(
             html="<html><body><p>Should not render</p></body></html>",
             pdf_bytes=b"%PDF-1.4 fake pdf",
             renderer="weasyprint",
         )):
        payload = json.loads(_run_tool(
            CreateDocumentTool(),
            title="Washington security deposit compliance letter",
            content="This letter explains the statutory basis for a deposit deduction.",
            document_type="other",
            document_category="compliance",
            risk_score=8,
        ))

    assert payload["status"] == "error"
    assert "legal or compliance document" in payload["message"]
    assert "governing law" in payload["message"]
    assert upload_mock.await_count == 0


def test_create_document_tool_allows_compliance_document_with_legal_preflight(db):
    conv_token = active_conversation_id.set("conv-compliance-document")
    pending_token = pending_suggestion_messages.set([])
    try:
        with patch("db.session.SessionLocal.session_factory", return_value=db), \
             patch.object(db, "close", lambda: None), \
             patch("backends.wire.storage_backend.upload", new_callable=AsyncMock) as upload_mock, \
             patch("llm.generated_documents.render_document", return_value=RenderedDocument(
                 html="<html><body><p>Compliance letter</p></body></html>",
                 pdf_bytes=b"%PDF-1.4 fake pdf",
                 renderer="weasyprint",
             )):
            payload = json.loads(_run_tool(
                CreateDocumentTool(),
                title="Washington security deposit compliance letter",
                content="Statutory deposit deduction explanation.",
                document_type="other",
                document_category="compliance",
                risk_score=8,
                legal_requirements={
                    "jurisdiction": "Washington, USA",
                    "citation": "RCW 59.18.280",
                    "reason": "Washington law governs the contents and timing of deposit statements.",
                    "required_fields": ["tenant name", "property address", "deposit statement details"],
                    "missing_fields": [],
                },
            ))
    finally:
        pending_suggestion_messages.reset(pending_token)
        active_conversation_id.reset(conv_token)

    assert payload["status"] == "ok"
    assert upload_mock.await_count == 2


def test_create_document_tool_blocks_unresolved_notice_placeholders(db):
    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None), \
         patch("backends.wire.storage_backend.upload", new_callable=AsyncMock) as upload_mock, \
         patch("llm.generated_documents.render_document", return_value=RenderedDocument(
             html="<html><body><p>Should not render</p></body></html>",
             pdf_bytes=b"%PDF-1.4 fake pdf",
             renderer="weasyprint",
         )):
        payload = json.loads(_run_tool(
            CreateDocumentTool(),
            title="14-Day Pay or Vacate Notice",
            content=(
                "LANDLORD/MANAGER CONTACT INFORMATION:\n"
                "Property Manager\n"
                "1234 Acme Lane, USA\n"
                "Phone: [Manager Phone]\n"
                "Email: [Manager Email]"
            ),
            document_type="notice",
            legal_requirements=_wa_notice_legal_requirements(),
        ))

    assert payload["status"] == "error"
    assert "RCW 59.18.057" in payload["message"]
    assert "Manager Phone" in payload["message"]
    assert "Manager Email" in payload["message"]
    assert upload_mock.await_count == 0


def test_create_document_tool_blocks_low_confidence_compliance_fields(db):
    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None), \
         patch("backends.wire.storage_backend.upload", new_callable=AsyncMock) as upload_mock, \
         patch("llm.generated_documents.render_document", return_value=RenderedDocument(
             html="<html><body><p>Should not render</p></body></html>",
             pdf_bytes=b"%PDF-1.4 fake pdf",
             renderer="weasyprint",
         )):
        payload = json.loads(_run_tool(
            CreateDocumentTool(),
            title="14-Day Pay or Vacate Notice",
            content="Tenant: Bob Ferguson\nAmount due: $5,590",
            document_type="notice",
            legal_requirements=_wa_notice_legal_requirements(
                low_confidence_fields=["owner/landlord name"],
            ),
        ))

    assert payload["status"] == "error"
    assert "stale or low-confidence" in payload["message"]
    assert "owner/landlord name" in payload["message"]
    assert "RCW 59.18.057" in payload["message"]
    assert upload_mock.await_count == 0


def test_message_person_tool_uses_external_tenant_id_in_payload(db):
    tenant_user = User(
        org_id=1,
        creator_id=1,
        user_type="tenant",
        first_name="Tina",
        last_name="Tenant",
        phone="+15550004444",
        active=True,
    )
    db.add(tenant_user)
    db.flush()
    tenant = Tenant(org_id=1, creator_id=1, user_id=tenant_user.id)
    task = Task(id=NumberAllocator.allocate_next(db, entity_type="task", org_id=1), org_id=1, creator_id=1, title="Fix sink")
    db.add_all([tenant, task])
    db.flush()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None), \
         patch("gql.services.settings_service.get_action_policy_settings", return_value={
             "entity_changes": "balanced",
             "outbound_messages": "strict",
             "suggestion_fallback": "balanced",
         }):
        payload = json.loads(_run_tool(
            MessageExternalPersonTool(),
            task_id=str(task.id),
            entity_id=tenant.external_id,
            entity_type="tenant",
            draft_message="Checking in about the sink.",
            risk_level="high",
        ))

    suggestion = db.query(Suggestion).filter_by(id=payload["suggestion_id"]).one()
    assert suggestion.action_payload["entity_id"] == tenant.external_id
    assert suggestion.action_payload["risk_level"] == "high"
    assert suggestion.status == "pending", "high risk under strict policy must stay pending"
    assert "manager review" in payload["message"]
    assert "strict" in payload["policy_reason"]
    assert db.query(Tenant).count() == 1


def test_propose_task_tool_uses_external_vendor_id_in_payload(db):
    vendor = User(
        org_id=1,
        creator_id=1,
        user_type="vendor",
        first_name="Vera",
        last_name="Vendor",
        role_label="Plumber",
        phone="+15550005555",
        active=True,
    )
    db.add(vendor)
    db.flush()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        payload = json.loads(_run_tool(
            ProposeTaskTool(),
            title="Leak in unit",
            category="maintenance",
            vendor_id=vendor.external_id,
            goal="Stop the leak in unit and document the repair.",
            steps=[
                {"key": "diagnose", "label": "Diagnose the source of the leak", "status": "active"},
                {"key": "repair", "label": "Repair the leak", "status": "pending"},
                {"key": "confirm", "label": "Confirm with tenant the leak is resolved", "status": "pending"},
            ],
            draft_message="Can you take a look at this leak?",
        ))

    # propose_task returns a proposal_id (not a task_id) and explicitly
    # signals the dependent-task does not exist yet.
    assert payload["status"] == "pending_approval"
    assert payload["task_id"] is None
    suggestion = db.query(Suggestion).filter_by(id=payload["proposal_id"]).one()
    assert suggestion.action_payload["vendor_id"] == vendor.external_id
    assert suggestion.action_payload["action"] == "send_and_create_task"


def test_propose_task_tool_rejects_unknown_vendor_id(db):
    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        payload = json.loads(_run_tool(
            ProposeTaskTool(),
            title="Leak in unit",
            category="maintenance",
            vendor_id="vendor_id_needed",
            goal="Stop the leak in unit and document the repair.",
            steps=[
                {"key": "diagnose", "label": "Diagnose the source of the leak", "status": "active"},
                {"key": "repair", "label": "Repair the leak", "status": "pending"},
                {"key": "confirm", "label": "Confirm with tenant the leak is resolved", "status": "pending"},
            ],
            draft_message="Can you take a look at this leak?",
        ))

    assert payload["status"] == "error"
    assert "Vendor vendor_id_needed not found" in payload["message"]
    assert db.query(Suggestion).count() == 0


def test_propose_task_tool_rejects_tenant_addressed_vendor_draft(db):
    vendor = User(
        org_id=1,
        creator_id=1,
        user_type="vendor",
        first_name="Sarah",
        last_name="Chen",
        company="Green Thumb Landscaping",
        role_label="Landscaper",
        phone="+15550005556",
        active=True,
    )
    db.add(vendor)
    db.flush()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        payload = json.loads(_run_tool(
            ProposeTaskTool(),
            title="Schedule gutter cleaning",
            category="maintenance",
            vendor_id=vendor.external_id,
            goal="Schedule gutter cleaning and confirm access with Priya.",
            steps=[
                {"key": "confirm_vendor", "label": "Confirm vendor availability", "status": "active"},
                {"key": "coordinate_access", "label": "Coordinate tenant access", "status": "pending"},
                {"key": "complete", "label": "Complete gutter cleaning", "status": "pending"},
            ],
            draft_message="Hi Priya, what days work for gutter cleaning?",
        ))

    assert payload["status"] == "error"
    assert "appears addressed to 'priya'" in payload["message"]
    assert "propose_task sends draft_message to the assigned vendor" in payload["message"]
    assert db.query(Suggestion).count() == 0


def test_propose_task_tool_blocks_explicit_direct_draft_request(db):
    vendor = User(
        org_id=1,
        creator_id=1,
        user_type="vendor",
        first_name="Vera",
        last_name="Vendor",
        role_label="Process Server",
        phone="+15550005555",
        active=True,
    )
    db.add(vendor)
    db.flush()

    user_token = current_user_message.set("dont create a suggestion, create the draft")
    try:
        with patch("db.session.SessionLocal.session_factory", return_value=db), \
             patch.object(db, "close", lambda: None):
            payload = json.loads(_run_tool(
                ProposeTaskTool(),
                title="Draft 14-Day Pay or Vacate Notice for Bob Ferguson",
                category="compliance",
                vendor_id=vendor.external_id,
            ))
    finally:
        current_user_message.reset(user_token)

    assert payload["status"] == "error"
    assert "Draft the requested notice or document directly in the chat response." in payload["message"]
    assert db.query(Suggestion).count() == 0


def test_propose_task_requires_steps(db):
    """Tasks without steps render with an empty progress tracker —
    propose_task must reject the call instead of producing a stepless
    Suggestion the agent then has to retry to fix."""
    vendor = User(
        org_id=1,
        creator_id=1,
        user_type="vendor",
        first_name="Vera",
        last_name="Vendor",
        role_label="Plumber",
        phone="+15550006666",
        active=True,
    )
    db.add(vendor)
    db.flush()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        payload = json.loads(_run_tool(
            ProposeTaskTool(),
            title="Loud HVAC unit",
            category="maintenance",
            vendor_id=vendor.external_id,
            goal="Diagnose and fix the noisy HVAC unit before tenant complains again.",
            draft_message="Can you look at this HVAC noise?",
        ))

    assert payload["status"] == "error"
    assert "steps is required" in payload["message"]
    assert db.query(Suggestion).count() == 0


def test_propose_task_rejects_steps_without_key_or_label(db):
    vendor = User(
        org_id=1,
        creator_id=1,
        user_type="vendor",
        first_name="Vera",
        last_name="Vendor",
        role_label="Plumber",
        phone="+15550006677",
        active=True,
    )
    db.add(vendor)
    db.flush()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        payload = json.loads(_run_tool(
            ProposeTaskTool(),
            title="Loud HVAC unit",
            category="maintenance",
            vendor_id=vendor.external_id,
            goal="Diagnose and fix the noisy HVAC unit.",
            steps=[{"label": "Diagnose"}],   # missing key
        ))

    assert payload["status"] == "error"
    assert "key" in payload["message"]
    assert db.query(Suggestion).count() == 0


def test_create_suggestion_tool_queues_inline_action_card_message(db):
    conv_token = active_conversation_id.set("conv-suggestion")
    pending_token = pending_suggestion_messages.set([])
    try:
        with patch("handlers.deps.SessionLocal", return_value=db), \
             patch.object(db, "close", lambda: None):
            payload = json.loads(_run_tool(
                CreateSuggestionTool(),
                title="Upload notice",
                body="Need a signed notice uploaded to continue.",
                suggestion_type="compliance",
                risk_score=8,
            ))
    finally:
        queued = pending_suggestion_messages.get() or []
        pending_suggestion_messages.reset(pending_token)
        active_conversation_id.reset(conv_token)

    assert payload["status"] == "ok"
    assert queued[0]["message_type"].name == "ACTION"
    assert queued[0]["meta"]["action_card"]["kind"] == "suggestion"
    assert queued[0]["meta"]["action_card"]["title"] == "Upload notice"


def test_create_suggestion_tool_normalizes_blank_optional_ids_and_leaves_session_usable(db):
    conv_token = active_conversation_id.set("conv-suggestion")
    pending_token = pending_suggestion_messages.set([])
    try:
        with patch("db.session.SessionLocal.session_factory", return_value=db), \
             patch.object(db, "close", lambda: None):
            payload = json.loads(_run_tool(
                CreateSuggestionTool(),
                title="Create tenant record from lease",
                body="Tenant name missing.",
                suggestion_type="compliance",
                risk_score=8,
                property_id="   ",
                unit_id="",
            ))
    finally:
        pending_suggestion_messages.reset(pending_token)
        active_conversation_id.reset(conv_token)

    assert payload["status"] == "ok"
    suggestion = db.query(Suggestion).filter_by(id=payload["suggestion_id"]).one()
    convo = db.query(Conversation).filter_by(id=suggestion.ai_conversation_id).one()
    assert suggestion.property_id is None
    assert suggestion.unit_id is None
    assert convo.property_id is None
    assert convo.unit_id is None
    assert db.query(Suggestion).count() >= 1


def test_create_suggestion_tool_redacts_vendor_identity_from_tenant_draft(db):
    vendor = User(
        org_id=1,
        creator_id=1,
        user_type="vendor",
        first_name="Handyman",
        last_name="Rob",
        phone="206-555-0200",
        active=True,
    )
    tenant_user = User(
        org_id=1,
        creator_id=1,
        user_type="tenant",
        first_name="Alice",
        last_name="Renter",
        phone="206-555-0100",
        active=True,
    )
    db.add_all([vendor, tenant_user])
    db.flush()
    tenant = Tenant(org_id=1, creator_id=1, user_id=tenant_user.id)
    convo = Conversation(
        org_id=1,
        creator_id=1,
        subject="Garage door is broken",
        conversation_type=ConversationType.TASK_AI,
        is_group=False,
        is_archived=False,
    )
    db.add_all([tenant, convo])
    db.flush()
    task = Task(id=NumberAllocator.allocate_next(db, entity_type="task", org_id=1), org_id=1, creator_id=1, title="Garage door is broken", ai_conversation_id=convo.id)
    db.add(task)
    db.flush()

    vendor_conv = Conversation(
        org_id=1,
        creator_id=1,
        subject="Garage door repair",
        conversation_type=ConversationType.VENDOR,
        is_group=False,
        is_archived=False,
    )
    db.add(vendor_conv)
    db.flush()
    vendor_conv.parent_task_id = task.id
    db.add(Message(
        org_id=1,
        conversation_id=vendor_conv.id,
        sender_type=ParticipantType.EXTERNAL_CONTACT,
        body="I can come at 2pm tomorrow",
        message_type=MessageType.MESSAGE,
        sender_name="Handyman Rob",
        is_ai=False,
    ))
    db.flush()

    conv_token = active_conversation_id.set(str(convo.id))
    pending_token = pending_suggestion_messages.set([])
    try:
        with patch("db.session.SessionLocal.session_factory", return_value=db), \
             patch.object(db, "close", lambda: None):
            payload = json.loads(_run_tool(
                CreateSuggestionTool(),
                title="Check tenant availability",
                body="Ask the tenant if Handyman Rob can come at 2pm tomorrow to assess the garage door.",
                suggestion_type="maintenance",
                risk_score=5,
                task_id=str(task.id),
                action_payload={
                    "action": "message_person",
                    "entity_type": "tenant",
                    "entity_id": tenant.external_id,
                    "entity_name": "Alice Renter",
                    "draft_message": "Hi Alice, Handyman Rob can come at 2pm tomorrow to assess the garage door. Will you be available? You can reach him at 206-555-0200.",
                },
            ))
    finally:
        pending_suggestion_messages.reset(pending_token)
        active_conversation_id.reset(conv_token)

    assert payload["status"] == "ok"
    suggestion = db.query(Suggestion).filter_by(id=payload["suggestion_id"]).one()
    draft = suggestion.action_payload["draft_message"]
    assert "Handyman Rob" not in draft
    assert "206-555-0200" not in draft
    assert "contractor" in draft.lower()


def test_message_person_tool_redacts_vendor_identity_from_tenant_draft(db):
    vendor = User(
        org_id=1,
        creator_id=1,
        user_type="vendor",
        first_name="Handyman",
        last_name="Rob",
        phone="206-555-0200",
        active=True,
    )
    tenant_user = User(
        org_id=1,
        creator_id=1,
        user_type="tenant",
        first_name="Alice",
        last_name="Renter",
        phone="206-555-0100",
        active=True,
    )
    db.add_all([vendor, tenant_user])
    db.flush()
    tenant = Tenant(org_id=1, creator_id=1, user_id=tenant_user.id)
    convo = Conversation(
        org_id=1,
        creator_id=1,
        subject="Garage door is broken",
        conversation_type=ConversationType.TASK_AI,
        is_group=False,
        is_archived=False,
    )
    db.add_all([tenant, convo])
    db.flush()
    task = Task(id=NumberAllocator.allocate_next(db, entity_type="task", org_id=1), org_id=1, creator_id=1, title="Garage door is broken", ai_conversation_id=convo.id)
    db.add(task)
    db.flush()

    vendor_conv = Conversation(
        org_id=1,
        creator_id=1,
        subject="Garage door repair",
        conversation_type=ConversationType.VENDOR,
        is_group=False,
        is_archived=False,
    )
    db.add(vendor_conv)
    db.flush()
    vendor_conv.parent_task_id = task.id
    db.add(Message(
        org_id=1,
        conversation_id=vendor_conv.id,
        sender_type=ParticipantType.EXTERNAL_CONTACT,
        body="I can come at 2pm tomorrow",
        message_type=MessageType.MESSAGE,
        sender_name="Handyman Rob",
        is_ai=False,
    ))
    db.flush()

    conv_token = active_conversation_id.set(str(convo.id))
    pending_token = pending_suggestion_messages.set([])
    try:
        with patch("db.session.SessionLocal.session_factory", return_value=db), \
             patch.object(db, "close", lambda: None):
            payload = json.loads(_run_tool(
                MessageExternalPersonTool(),
                task_id=str(task.id),
                entity_id=tenant.external_id,
                entity_type="tenant",
                draft_message="Hi Alice, Handyman Rob can come at 2pm tomorrow to assess the garage door. Will you be available? You can reach him at 206-555-0200.",
                risk_level="medium",
            ))
    finally:
        pending_suggestion_messages.reset(pending_token)
        active_conversation_id.reset(conv_token)

    assert payload["status"] == "ok"
    suggestion = db.query(Suggestion).filter_by(id=payload["suggestion_id"]).one()
    draft = suggestion.action_payload["draft_message"]
    assert "Handyman Rob" not in draft
    assert "206-555-0200" not in draft
    assert "contractor" in draft.lower()


def test_create_suggestion_tool_blocks_explicit_direct_draft_request(db):
    user_token = current_user_message.set("dont create a suggestion, create the draft")
    try:
        with patch("db.session.SessionLocal.session_factory", return_value=db), \
             patch.object(db, "close", lambda: None):
            payload = json.loads(_run_tool(
                CreateSuggestionTool(),
                title="Draft 14-Day Pay or Vacate Notice for Bob Ferguson",
                body="Draft the notice for the unpaid February and March 2026 rent.",
                suggestion_type="compliance",
                risk_score=9,
            ))
    finally:
        current_user_message.reset(user_token)

    assert payload["status"] == "error"
    assert "Draft the requested notice or document directly in the chat response." in payload["message"]
    assert db.query(Suggestion).count() == 0


def test_create_suggestion_tool_requires_confirmation_for_upload_request(db):
    convo = Conversation(
        org_id=1,
        creator_id=1,
        subject="Eviction",
        conversation_type=ConversationType.TASK_AI,
        is_group=False,
        is_archived=False,
    )
    db.add(convo)
    db.flush()
    task = Task(id=NumberAllocator.allocate_next(db, entity_type="task", org_id=1), org_id=1, creator_id=1, title="Eviction proceedings", ai_conversation_id=convo.id)
    db.add(task)
    db.flush()
    db.add(Message(
        org_id=1,
        conversation_id=convo.id,
        sender_type=ParticipantType.ACCOUNT_USER,
        body="What do I need to do next?",
        message_type=MessageType.MESSAGE,
        sender_name="Manager",
        is_ai=False,
    ))
    db.flush()

    conv_token = active_conversation_id.set(str(convo.id))
    try:
        with patch("db.session.SessionLocal.session_factory", return_value=db), \
             patch.object(db, "close", lambda: None):
            payload = json.loads(_run_tool(
                CreateSuggestionTool(),
                title="Upload 14-Day Pay or Vacate Notice",
                body="The current task is blocked until a 14-day notice is uploaded.",
                suggestion_type="compliance",
                risk_score=9,
                action_payload={
                    "action": "request_file_upload",
                    "requested_file_kind": "notice",
                    "requested_file_label": "14-Day Pay or Vacate Notice",
                    "instructions": "Upload the completed 14-day notice for Bob Ferguson.",
                    "target_task_id": str(task.id),
                },
            ))
    finally:
        active_conversation_id.reset(conv_token)

    assert payload["status"] == "error"
    assert "Ask the user first" in payload["message"]
    assert db.query(Suggestion).count() == 0


def test_create_suggestion_tool_creates_confirmed_upload_request_and_marks_task_blocked(db):
    convo = Conversation(
        org_id=1,
        creator_id=1,
        subject="Eviction",
        conversation_type=ConversationType.TASK_AI,
        is_group=False,
        is_archived=False,
    )
    db.add(convo)
    db.flush()
    task = Task(id=NumberAllocator.allocate_next(db, entity_type="task", org_id=1), org_id=1, creator_id=1, title="Eviction proceedings", ai_conversation_id=convo.id)
    db.add(task)
    db.flush()
    db.add(Message(
        org_id=1,
        conversation_id=convo.id,
        sender_type=ParticipantType.ACCOUNT_USER,
        body="Yes, create a suggestion and I will upload the 14-day notice.",
        message_type=MessageType.MESSAGE,
        sender_name="Manager",
        is_ai=False,
    ))
    db.flush()

    conv_token = active_conversation_id.set(str(convo.id))
    pending_token = pending_suggestion_messages.set([])
    try:
        with patch("db.session.SessionLocal.session_factory", return_value=db), \
             patch.object(db, "close", lambda: None):
            payload = json.loads(_run_tool(
                CreateSuggestionTool(),
                title="Upload 14-Day Pay or Vacate Notice for Bob Ferguson",
                body="The current eviction task is blocked until the 14-day notice is uploaded.",
                suggestion_type="compliance",
                risk_score=9,
                action_payload={
                    "action": "request_file_upload",
                    "requested_file_kind": "notice",
                    "requested_file_label": "14-Day Pay or Vacate Notice",
                    "instructions": "Upload the completed 14-day notice for Bob Ferguson so the task can continue.",
                    "target_task_id": str(task.id),
                },
            ))
    finally:
        queued = pending_suggestion_messages.get() or []
        pending_suggestion_messages.reset(pending_token)
        active_conversation_id.reset(conv_token)

    suggestion = db.query(Suggestion).filter_by(id=payload["suggestion_id"]).one()
    db.refresh(task)
    assert suggestion.task_id == task.id
    assert suggestion.action_payload["action"] == "request_file_upload"
    assert task.task_mode == TaskMode.WAITING_APPROVAL
    assert task.steps[0]["label"] == "Upload 14-Day Pay or Vacate Notice"
    assert "Blocked until 14-Day Pay or Vacate Notice is uploaded." in task.steps[0]["note"]
    assert queued[0]["meta"]["action_card"]["fields"][-1] == {
        "label": "Requested File",
        "value": "14-Day Pay or Vacate Notice",
    }


def test_create_suggestion_tool_blocks_in_task_manager_approval_requests(db):
    convo = Conversation(
        org_id=1,
        creator_id=1,
        subject="Landscaping",
        conversation_type=ConversationType.TASK_AI,
        is_group=False,
        is_archived=False,
    )
    db.add(convo)
    db.flush()
    task = Task(
        id=NumberAllocator.allocate_next(db, entity_type="task", org_id=1),
        org_id=1,
        creator_id=1,
        title="Landscape spring cleanup — getting quotes",
        ai_conversation_id=convo.id,
    )
    db.add(task)
    db.flush()

    conv_token = active_conversation_id.set(str(convo.id))
    try:
        with patch("db.session.SessionLocal.session_factory", return_value=db), \
             patch.object(db, "close", lambda: None):
            payload = json.loads(_run_tool(
                CreateSuggestionTool(),
                title="Approve landscaper Alex at $545 for spring cleanup at The Meadows",
                body=(
                    "Two quotes received. Alex is lower cost and tenant timing aligns. "
                    "Request approval to book Alex at $545 for the first cleanup."
                ),
                suggestion_type="maintenance",
                risk_score=4,
                task_id=str(task.id),
                action_payload={
                    "selected_vendor": "Alex",
                    "quote_amount": 545,
                    "decision_needed": "approve_and_book",
                },
            ))
    finally:
        active_conversation_id.reset(conv_token)

    assert payload["status"] == "error"
    assert "ask_manager" in payload["message"]
    assert db.query(Suggestion).count() == 0


def test_create_suggestion_tool_normalizes_notice_draft_into_upload_request(db):
    convo = Conversation(
        org_id=1,
        creator_id=1,
        subject="Eviction",
        conversation_type=ConversationType.TASK_AI,
        is_group=False,
        is_archived=False,
    )
    db.add(convo)
    db.flush()
    task = Task(id=NumberAllocator.allocate_next(db, entity_type="task", org_id=1), org_id=1, creator_id=1, title="Eviction proceedings", ai_conversation_id=convo.id)
    db.add(task)
    db.flush()
    db.add(Message(
        org_id=1,
        conversation_id=convo.id,
        sender_type=ParticipantType.ACCOUNT_USER,
        body="Yes, create a suggestion for the 14-day notice.",
        message_type=MessageType.MESSAGE,
        sender_name="Manager",
        is_ai=False,
    ))
    db.flush()

    conv_token = active_conversation_id.set(str(convo.id))
    try:
        with patch("db.session.SessionLocal.session_factory", return_value=db), \
             patch.object(db, "close", lambda: None):
            payload = json.loads(_run_tool(
                CreateSuggestionTool(),
                title="Draft 14-Day Pay or Vacate Notice for Bob Ferguson",
                body="Draft a formal 14-Day Pay or Vacate Notice as required before filing.",
                suggestion_type="compliance",
                risk_score=9,
            ))
    finally:
        active_conversation_id.reset(conv_token)

    suggestion = db.query(Suggestion).filter_by(id=payload["suggestion_id"]).one()
    assert suggestion.action_payload["action"] == "request_file_upload"
    assert suggestion.action_payload["requested_file_kind"] == "notice"


def test_create_suggestion_tool_blocks_followup_after_notice_served(db):
    convo = Conversation(
        org_id=1,
        creator_id=1,
        subject="Eviction",
        conversation_type=ConversationType.TASK_AI,
        is_group=False,
        is_archived=False,
    )
    db.add(convo)
    db.flush()
    task = Task(id=NumberAllocator.allocate_next(db, entity_type="task", org_id=1), org_id=1, creator_id=1, title="Eviction proceedings", ai_conversation_id=convo.id)
    db.add(task)
    db.flush()
    db.add(Suggestion(
        id=NumberAllocator.allocate_next(db, entity_type="suggestion", org_id=1),
        org_id=1,
        creator_id=1,
        title="Upload 14-Day Pay or Vacate Notice",
        status="pending",
        task_id=task.id,
        action_payload={
            "action": "request_file_upload",
            "requested_file_kind": "notice",
            "requested_file_label": "14-Day Pay or Vacate Notice",
            "instructions": "Upload the completed notice.",
            "target_task_id": str(task.id),
        },
    ))
    db.flush()

    conv_token = active_conversation_id.set(str(convo.id))
    user_token = current_user_message.set(
        "I uploaded the signed 14-day pay or vacate notice and served it by certified mail and posting on the property today."
    )
    try:
        with patch("db.session.SessionLocal.session_factory", return_value=db), \
             patch.object(db, "close", lambda: None):
            payload = json.loads(_run_tool(
                CreateSuggestionTool(),
                title="Next steps after 14-day notice service",
                body="Create a follow-up suggestion for next steps.",
                suggestion_type="compliance",
                risk_score=8,
            ))
    finally:
        current_user_message.reset(user_token)
        active_conversation_id.reset(conv_token)

    assert payload["status"] == "error"
    assert "Do not create a new suggestion or task" in payload["message"]


def test_save_memory_private_entity_note_uses_current_account(db):
    property_owner = User(
        org_id=1,
        creator_id=1,
        user_type="account",
        email="owner@example.com",
        active=True,
    )
    db.add(property_owner)
    db.flush()

    from db.models import Property

    property_row = Property(
        org_id=1,
        creator_id=property_owner.id,
        address_line1="123 Test St",
        property_type="single_family",
    )
    db.add(property_row)
    db.flush()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None), \
         patch("llm.tools.memory.resolve_account_id", return_value=property_owner.id):
        payload = json.loads(_run_tool(
            SaveMemoryTool(),
            content="Owner prefers weekend vendor visits only.",
            scope="entity",
            entity_type="property",
            entity_id=property_row.id,
            entity_label="123 Test St",
            visibility="private",
        ))

    assert payload["status"] == "ok"
    note = db.query(EntityNote).filter_by(
        creator_id=property_owner.id,
        entity_type="property",
        entity_id=property_row.id,
    ).one()
    assert "weekend vendor visits" in note.content


def test_recall_memory_private_entity_note_uses_current_account(db):
    property_owner = User(
        org_id=1,
        creator_id=1,
        user_type="account",
        email="owner@example.com",
        active=True,
    )
    db.add(property_owner)
    db.flush()

    from db.models import Property

    property_row = Property(
        org_id=1,
        creator_id=property_owner.id,
        address_line1="123 Test St",
        property_type="single_family",
        context="Shared note for all staff.",
    )
    db.add(property_row)
    db.flush()

    db.add(EntityNote(
        creator_id=property_owner.id,
        entity_type="property",
        entity_id=property_row.id,
        content="Private preference: use the side gate.",
    ))
    db.commit()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None), \
         patch("llm.tools.memory.resolve_account_id", return_value=property_owner.id):
        payload = json.loads(_run_tool(
            RecallMemoryTool(),
            entity_type="property",
            entity_id=property_row.id,
        ))

    assert payload["count"] == 1
    note = payload["notes"][0]
    assert note["entity_id"] == property_row.id
    assert note["shared_context"] == "Shared note for all staff."
    assert note["private_notes"] == "Private preference: use the side gate."


# ── Simulation dispatcher tests ──────────────────────────────────────────────


def _dispatcher_call(tool, args: dict) -> str:
    """Run the same simulation gate that _register_rentmate_tools installs.

    Tests use this rather than calling ``tool.execute`` directly because the
    read-write blackhole lives in the registry's handler wrapper, not in the
    tool body.
    """
    import json as _json

    from llm.tools._common import (
        ToolMode,
        is_simulating,
        record_simulated_action,
    )

    async def _invoke():
        if tool.mode == ToolMode.READ_WRITE and is_simulating():
            sim_id = record_simulated_action(tool.name, args or {})
            return _json.dumps({
                "status": "ok",
                "simulation_id": sim_id,
                "message": f"(simulation) would call {tool.name}",
            })
        return await tool.execute(**args)

    return asyncio.run(_invoke())


_READ_WRITE_TOOL_CASES = [
    pytest.param(
        lambda: __import__("llm.tools", fromlist=["CreatePropertyTool"]).CreatePropertyTool(),
        {"address": "123 Sim St", "property_type": "single_family"},
        id="create_property",
    ),
    pytest.param(
        lambda: __import__("llm.tools", fromlist=["CreateTenantTool"]).CreateTenantTool(),
        {"first_name": "Sim", "last_name": "Tenant"},
        id="create_tenant",
    ),
    pytest.param(
        lambda: __import__("llm.tools", fromlist=["CreateVendorTool"]).CreateVendorTool(),
        {"name": "Sim Plumber", "phone": "+15550009999", "vendor_type": "Plumber"},
        id="create_vendor",
    ),
    pytest.param(
        lambda: __import__("llm.tools", fromlist=["CreateSuggestionTool"]).CreateSuggestionTool(),
        {"title": "Sim suggestion", "body": "body", "suggestion_type": "compliance", "risk_score": 3},
        id="create_suggestion",
    ),
    pytest.param(
        lambda: __import__("llm.tools", fromlist=["MessageExternalPersonTool"]).MessageExternalPersonTool(),
        {"entity_id": "sim-entity", "entity_type": "tenant", "draft_message": "Sim check-in"},
        id="message_person",
    ),
    pytest.param(
        lambda: __import__("llm.tools", fromlist=["SaveMemoryTool"]).SaveMemoryTool(),
        {"content": "Sim note", "scope": "task", "task_id": "sim-task"},
        id="save_memory",
    ),
    pytest.param(
        lambda: __import__("llm.tools", fromlist=["EditMemoryTool"]).EditMemoryTool(),
        {"entity_type": "property", "entity_id": "sim-prop", "new_context": "Sim context"},
        id="edit_memory",
    ),
    pytest.param(
        lambda: __import__("llm.tools", fromlist=["ProposeTaskTool"]).ProposeTaskTool(),
        {"title": "Sim task", "category": "maintenance"},
        id="propose_task",
    ),
    pytest.param(
        lambda: __import__("llm.tools", fromlist=["CloseTaskTool"]).CloseTaskTool(),
        {"task_id": "sim-task", "reason": "Sim close"},
        id="close_task",
    ),
    pytest.param(
        lambda: __import__("llm.tools", fromlist=["UpdateTaskProgressTool"]).UpdateTaskProgressTool(),
        {"task_id": "sim-task", "step_key": "step-1", "status": "done"},
        id="update_task_progress",
    ),
    pytest.param(
        lambda: __import__("llm.tools", fromlist=["RecordTaskReviewTool"]).RecordTaskReviewTool(),
        {"task_id": "1", "status": "on_track", "summary": "Sim review."},
        id="record_task_review",
    ),
    pytest.param(
        lambda: __import__("llm.tools", fromlist=["AskManagerTool"]).AskManagerTool(),
        {"task_id": "1", "question": "Should I proceed?"},
        id="ask_manager",
    ),
]


@pytest.mark.parametrize("tool_factory, args", _READ_WRITE_TOOL_CASES)
def test_read_write_tools_are_blackholed_in_simulation(db, tool_factory, args):
    """Every read-write tool short-circuits inside a simulation:
    the dispatcher records the inputs and does NOT run ``execute``, so no
    database rows or external side effects are produced.
    """
    from llm.tools._common import ToolMode, simulation_actions

    tool = tool_factory()
    assert tool.mode == ToolMode.READ_WRITE, (
        f"{tool.name} is classified as read-only; move it to the read-only test"
    )

    baseline_counts = {
        "Property": db.query(Property).count(),
        "Tenant": db.query(Tenant).count(),
        "Suggestion": db.query(Suggestion).count(),
        "User": db.query(User).count(),
    }

    token = simulation_actions.set([])
    try:
        # Patch SessionLocal so tools that slip through the gate (a bug) would
        # hit the test DB; the gate should prevent any such call.
        with patch("db.session.SessionLocal.session_factory", return_value=db), \
             patch.object(db, "close", lambda: None):
            payload = json.loads(_dispatcher_call(tool, args))
        recorded = simulation_actions.get() or []
    finally:
        simulation_actions.reset(token)

    assert payload["status"] == "ok"
    assert payload["simulation_id"], "simulation_id must be recorded so the reply formatter can reference it"
    assert payload["message"].startswith("(simulation)")
    assert len(recorded) == 1
    entry = recorded[0]
    assert entry["tool"] == tool.name
    assert entry["args"] == args

    after_counts = {
        "Property": db.query(Property).count(),
        "Tenant": db.query(Tenant).count(),
        "Suggestion": db.query(Suggestion).count(),
        "User": db.query(User).count(),
    }
    assert after_counts == baseline_counts, (
        f"{tool.name} wrote rows during simulation — dispatcher gate missed it"
    )


def test_read_only_tool_still_runs_in_simulation(db):
    """Read-only tools must NOT be blackholed — the agent should still see
    real data during simulation (otherwise it can't decide what to do).
    """
    from llm.tools._common import ToolMode, simulation_actions

    vendor = User(
        org_id=1,
        creator_id=1,
        user_type="vendor",
        first_name="Read",
        last_name="Only",
        role_label="Plumber",
        phone="+15550008888",
        email="ro@example.com",
    )
    db.add(vendor)
    db.flush()

    tool = LookupVendorsTool()
    assert tool.mode == ToolMode.READ_ONLY

    token = simulation_actions.set([])
    try:
        with patch("db.session.SessionLocal.session_factory", return_value=db), \
             patch.object(db, "close", lambda: None):
            payload = json.loads(_dispatcher_call(tool, {}))
        recorded = simulation_actions.get() or []
    finally:
        simulation_actions.reset(token)

    assert payload.get("vendors"), "read-only tool should have returned vendor data, not a simulation stub"
    assert payload["vendors"][0]["id"] == vendor.external_id
    assert recorded == [], "read-only tools must not record simulation entries"


# ── message_person risk × policy routing ─────────────────────────────────────


def _seed_message_person_task(db) -> tuple[str, str]:
    """Create the minimum Task + Tenant a message_person call needs.

    Returns ``(task_id, tenant_external_id)`` for use in parametrised tests.
    """
    tenant_user = User(
        org_id=1,
        creator_id=1,
        user_type="tenant",
        first_name="Routing",
        last_name="Tenant",
        phone="+15550007777",
        active=True,
    )
    db.add(tenant_user)
    db.flush()
    tenant = Tenant(org_id=1, creator_id=1, user_id=tenant_user.id)
    task = Task(
        id=NumberAllocator.allocate_next(db, entity_type="task", org_id=1),
        org_id=1,
        creator_id=1,
        title="Routing check",
    )
    db.add_all([tenant, task])
    db.flush()
    return str(task.id), tenant.external_id


@pytest.mark.parametrize("policy,risk,expects_review", [
    # strict — every outbound message routes to manager review
    ("strict",     "low",      True),
    ("strict",     "medium",   True),
    ("strict",     "high",     True),
    ("strict",     "critical", True),
    # balanced — low and medium auto-send
    ("balanced",   "low",      False),
    ("balanced",   "medium",   False),
    ("balanced",   "high",     True),
    ("balanced",   "critical", True),
    # aggressive — only critical routes to review
    ("aggressive", "low",      False),
    ("aggressive", "medium",   False),
    ("aggressive", "high",     False),
    ("aggressive", "critical", True),
])
def test_message_person_routes_by_risk_and_policy(db, policy, risk, expects_review):
    """Every (risk × outbound_messages policy) cell routes correctly:
    risky combinations stay pending for manager review; safe ones auto-send
    (Suggestion row moves to status='accepted').

    Matches the inline table at
    ``llm/tools/messaging.py::_SUGGESTION_REVIEW_RISKS``. Changing the
    table without updating this matrix should fail the corresponding cell.
    """
    task_id, tenant_id = _seed_message_person_task(db)

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None), \
         patch("gql.services.settings_service.get_action_policy_settings", return_value={
             "entity_changes": "balanced",
             "outbound_messages": policy,
             "suggestion_fallback": "balanced",
         }):
        payload = json.loads(_run_tool(
            MessageExternalPersonTool(),
            task_id=task_id,
            entity_id=tenant_id,
            entity_type="tenant",
            draft_message=f"Routing check for {policy}/{risk}.",
            risk_level=risk,
        ))

    assert payload["status"] == "ok", payload
    suggestion = db.query(Suggestion).filter_by(id=payload["suggestion_id"]).one()
    assert suggestion.action_payload["risk_level"] == risk

    if expects_review:
        assert suggestion.status == "pending", (
            f"{policy}+{risk} should have stayed pending; got {suggestion.status}"
        )
        assert "manager review" in payload["message"]
        assert risk in payload["message"]
        assert policy in payload["message"]
        assert payload["policy_reason"].startswith(f"risk {risk}")
    else:
        assert suggestion.status == "accepted", (
            f"{policy}+{risk} should have auto-sent (status=accepted); got {suggestion.status}"
        )
        assert "auto-approved" in payload["message"]
        assert risk in payload["message"]
        assert policy in payload["message"]


def test_message_person_requires_risk_level(db):
    """Schema marks ``risk_level`` required; if the agent still omits it,
    execute() refuses rather than defaulting — so the routing gate can't
    be bypassed by a missing field.
    """
    task_id, tenant_id = _seed_message_person_task(db)

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        payload = json.loads(_run_tool(
            MessageExternalPersonTool(),
            task_id=task_id,
            entity_id=tenant_id,
            entity_type="tenant",
            draft_message="No risk specified.",
        ))

    assert payload["status"] == "error"
    assert "risk_level" in payload["message"]
    # And nothing was staged.
    assert db.query(Suggestion).count() == 0


def test_message_person_rejects_unknown_risk_level(db):
    task_id, tenant_id = _seed_message_person_task(db)

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        payload = json.loads(_run_tool(
            MessageExternalPersonTool(),
            task_id=task_id,
            entity_id=tenant_id,
            entity_type="tenant",
            draft_message="Bad risk.",
            risk_level="urgent",
        ))

    assert payload["status"] == "error"
    assert "risk_level" in payload["message"]
    assert db.query(Suggestion).count() == 0


def test_message_person_allows_repeat_send_when_we_already_messaged_recently(db):
    """Repeated task-scoped follow-ups are allowed now that the resend
    guard has been removed."""
    from datetime import UTC, datetime

    from db.models import Conversation, ConversationParticipant

    task_id, tenant_id = _seed_message_person_task(db)
    tenant = db.query(Tenant).filter_by(external_id=tenant_id).one()

    convo = Conversation(
        org_id=1,
        creator_id=1,
        subject="Rent payment question",
        conversation_type=ConversationType.TENANT,
        is_group=False,
        is_archived=False,
        parent_task_id=int(task_id),
    )
    db.add(convo)
    db.flush()
    db.add(ConversationParticipant(
        org_id=1,
        conversation_id=convo.id,
        user_id=tenant.user_id,
        participant_type=ParticipantType.TENANT,
        creator_id=1,
        is_active=True,
    ))
    db.add(Message(
        org_id=1,
        conversation_id=convo.id,
        sender_type=ParticipantType.ACCOUNT_USER,
        body="Hi Ryan, here's your payment portal link...",
        message_type=MessageType.MESSAGE,
        sender_name="RentMate",
        is_ai=True,
        sent_at=datetime.now(UTC),
    ))
    db.flush()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None), \
         patch("gql.services.settings_service.get_action_policy_settings", return_value={
             "entity_changes": "balanced",
             "outbound_messages": "balanced",
             "suggestion_fallback": "balanced",
         }):
        payload = json.loads(_run_tool(
            MessageExternalPersonTool(),
            task_id=task_id,
            entity_id=tenant_id,
            entity_type="tenant",
            draft_message="Hi Ryan, just following up on the payment portal link...",
            risk_level="low",
        ))

    assert payload["status"] == "ok", payload
    assert payload.get("code") != "recent_duplicate"


def test_message_person_allows_follow_up_after_tenant_reply(db):
    """Follow-ups remain allowed when the tenant has replied."""
    from datetime import UTC, datetime, timedelta

    from db.models import Conversation, ConversationParticipant

    task_id, tenant_id = _seed_message_person_task(db)
    tenant = db.query(Tenant).filter_by(external_id=tenant_id).one()

    convo = Conversation(
        org_id=1,
        creator_id=1,
        subject="Rent payment question",
        conversation_type=ConversationType.TENANT,
        is_group=False,
        is_archived=False,
        parent_task_id=int(task_id),
    )
    db.add(convo)
    db.flush()
    db.add(ConversationParticipant(
        org_id=1,
        conversation_id=convo.id,
        user_id=tenant.user_id,
        participant_type=ParticipantType.TENANT,
        creator_id=1,
        is_active=True,
    ))
    now = datetime.now(UTC)
    db.add(Message(
        org_id=1,
        conversation_id=convo.id,
        sender_type=ParticipantType.ACCOUNT_USER,
        body="Hi Ryan, here's the link",
        message_type=MessageType.MESSAGE,
        sender_name="RentMate",
        is_ai=True,
        sent_at=now - timedelta(minutes=30),
    ))
    db.add(Message(
        org_id=1,
        conversation_id=convo.id,
        sender_type=ParticipantType.TENANT,
        body="Thanks, but it's showing an error",
        message_type=MessageType.MESSAGE,
        sender_name="Ryan",
        is_ai=False,
        sent_at=now - timedelta(minutes=5),
    ))
    db.flush()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None), \
         patch("gql.services.settings_service.get_action_policy_settings", return_value={
             "entity_changes": "balanced",
             "outbound_messages": "balanced",
             "suggestion_fallback": "balanced",
         }):
        payload = json.loads(_run_tool(
            MessageExternalPersonTool(),
            task_id=task_id,
            entity_id=tenant_id,
            entity_type="tenant",
            draft_message="Sorry about that — can you share the exact error?",
            risk_level="low",
        ))

    assert payload["status"] == "ok", payload
    assert payload.get("code") != "recent_duplicate"


def test_message_person_rejects_unresolved_placeholders(db):
    task_id, tenant_id = _seed_message_person_task(db)

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None), \
         patch("gql.services.settings_service.get_action_policy_settings", return_value={
             "entity_changes": "balanced",
             "outbound_messages": "balanced",
             "suggestion_fallback": "balanced",
         }):
        payload = json.loads(_run_tool(
            MessageExternalPersonTool(),
            task_id=task_id,
            entity_id=tenant_id,
            entity_type="tenant",
            draft_message="Hi Ryan, here is your updated payment portal link: [payment portal link]",
            risk_level="low",
        ))

    assert payload["status"] == "error"
    assert "placeholders" in payload["message"]
    assert "ask_manager" in payload["message"]


def test_message_person_allows_scheduling_follow_up_without_reply(db):
    """Scheduling updates are allowed even without an intervening reply."""
    from datetime import UTC, datetime

    from db.models import Conversation, ConversationParticipant

    task_id, tenant_id = _seed_message_person_task(db)
    tenant = db.query(Tenant).filter_by(external_id=tenant_id).one()

    convo = Conversation(
        org_id=1,
        creator_id=1,
        subject="Spring cleanup access",
        conversation_type=ConversationType.TENANT,
        is_group=False,
        is_archived=False,
        parent_task_id=int(task_id),
    )
    db.add(convo)
    db.flush()
    db.add(ConversationParticipant(
        org_id=1,
        conversation_id=convo.id,
        user_id=tenant.user_id,
        participant_type=ParticipantType.TENANT,
        creator_id=1,
        is_active=True,
    ))
    db.add(Message(
        org_id=1,
        conversation_id=convo.id,
        sender_type=ParticipantType.ACCOUNT_USER,
        body=(
            "Hi Priya, we're scheduling the spring cleanup next Thursday. "
            "Please let me know if that day works for you."
        ),
        message_type=MessageType.MESSAGE,
        sender_name="RentMate",
        is_ai=True,
        sent_at=datetime.now(UTC),
    ))
    db.flush()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None), \
         patch("gql.services.settings_service.get_action_policy_settings", return_value={
             "entity_changes": "balanced",
             "outbound_messages": "balanced",
             "suggestion_fallback": "balanced",
         }):
        payload = json.loads(_run_tool(
            MessageExternalPersonTool(),
            task_id=task_id,
            entity_id=tenant_id,
            entity_type="tenant",
            draft_message=(
                "Hi Priya, Alex confirmed 10am next Thursday for the spring cleanup. "
                "Does that specific time work for access?"
            ),
            risk_level="low",
        ))

    assert payload["status"] == "ok", payload
    assert payload.get("code") != "recent_duplicate"


def test_message_person_allows_vendor_repeat_send_on_parent_conversation_thread(db):
    from datetime import UTC, datetime

    from db.models import Conversation, ConversationParticipant

    vendor = User(
        org_id=1,
        creator_id=1,
        user_type="vendor",
        first_name="Alex",
        last_name="Cleanup",
        phone="+12065550188",
        active=True,
    )
    task = Task(
        id=NumberAllocator.allocate_next(db, entity_type="task", org_id=1),
        org_id=1,
        creator_id=1,
        title="Spring cleanup coordination",
    )
    db.add_all([vendor, task])
    db.flush()

    convo = Conversation(
        org_id=1,
        creator_id=1,
        subject="Spring cleanup",
        conversation_type=ConversationType.VENDOR,
        is_group=False,
        is_archived=False,
    )
    db.add(convo)
    db.flush()
    task.parent_conversation_id = convo.id
    db.add(ConversationParticipant(
        org_id=1,
        conversation_id=convo.id,
        user_id=vendor.id,
        participant_type=ParticipantType.EXTERNAL_CONTACT,
        creator_id=1,
        is_active=True,
    ))
    db.add(Message(
        org_id=1,
        conversation_id=convo.id,
        sender_type=ParticipantType.ACCOUNT_USER,
        body="Could you provide a quote and availability for spring cleanup?",
        message_type=MessageType.MESSAGE,
        sender_name="RentMate",
        is_ai=True,
        sent_at=datetime.now(UTC),
    ))
    db.flush()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None), \
         patch("gql.services.settings_service.get_action_policy_settings", return_value={
             "entity_changes": "balanced",
             "outbound_messages": "balanced",
             "suggestion_fallback": "balanced",
         }):
        payload = json.loads(_run_tool(
            MessageExternalPersonTool(),
            task_id=str(task.id),
            entity_id=vendor.external_id,
            entity_type="vendor",
            draft_message="Hello, I'm reaching out from RentMate to request a quote for spring cleanup.",
            risk_level="low",
        ))

    assert payload["status"] == "ok", payload
    assert payload.get("code") != "recent_duplicate"


def test_message_person_without_task_creates_standalone_conversation(db):
    """Omitting task_id routes through the standalone path: a fresh
    conversation is created for the recipient and the low-risk draft lands
    in it directly (no Suggestion row needed — the message is the audit).
    """
    from db.models import Conversation, Message
    _task_id_unused, tenant_id = _seed_message_person_task(db)
    before_messages = db.query(Message).count()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None), \
         patch("gql.services.settings_service.get_action_policy_settings", return_value={
             "entity_changes": "balanced",
             "outbound_messages": "balanced",
             "suggestion_fallback": "balanced",
         }):
        payload = json.loads(_run_tool(
            MessageExternalPersonTool(),
            entity_id=tenant_id,
            entity_type="tenant",
            draft_message="Hi, this is a routine check-in from RentMate.",
            risk_level="low",
        ))

    assert payload["status"] == "ok", payload
    assert "conversation_id" in payload
    assert "standalone" in payload["message"]
    convo = db.query(Conversation).filter_by(id=int(payload["conversation_id"])).one()
    assert convo.parent_task_id is None, "standalone conversation must not be tied to a task"
    assert db.query(Message).count() == before_messages + 1
    # No Suggestion gets written on the direct-send path.
    assert db.query(Suggestion).count() == 0


def test_message_person_without_task_review_path_stages_suggestion_only(db):
    """A risky standalone message stages a Suggestion for manager review
    but does NOT create the conversation yet. Dismissing the suggestion
    should leave zero orphaned Conversation rows behind.
    """
    from db.models import Conversation, ConversationType
    _task_id_unused, tenant_id = _seed_message_person_task(db)

    # Count conversations of the recipient types before the tool runs so we
    # can assert "no new standalone conversation" exactly.
    before_standalone = (
        db.query(Conversation)
        .filter(Conversation.conversation_type.in_([ConversationType.TENANT, ConversationType.VENDOR]))
        .filter(Conversation.parent_task_id.is_(None))
        .count()
    )

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None), \
         patch("gql.services.settings_service.get_action_policy_settings", return_value={
             "entity_changes": "balanced",
             "outbound_messages": "balanced",
             "suggestion_fallback": "balanced",
         }):
        payload = json.loads(_run_tool(
            MessageExternalPersonTool(),
            entity_id=tenant_id,
            entity_type="tenant",
            draft_message="Formal notice — you have 14 days to cure.",
            risk_level="critical",
        ))

    assert payload["status"] == "ok", payload
    assert "suggestion_id" in payload
    # No conversation_id in the review-only response — conversation is
    # deferred until the manager approves the draft.
    assert "conversation_id" not in payload
    suggestion = db.query(Suggestion).filter_by(id=payload["suggestion_id"]).one()
    assert suggestion.task_id is None
    assert suggestion.status == "pending"
    # Full draft is preserved on the suggestion for the approval-time
    # executor to pick up.
    assert suggestion.action_payload["draft_message"] == "Formal notice — you have 14 days to cure."
    # And — crucially — zero new standalone conversations were created.
    after_standalone = (
        db.query(Conversation)
        .filter(Conversation.conversation_type.in_([ConversationType.TENANT, ConversationType.VENDOR]))
        .filter(Conversation.parent_task_id.is_(None))
        .count()
    )
    assert after_standalone == before_standalone, (
        "review path must not create a conversation until the suggestion is accepted"
    )


def test_message_person_strips_entity_prefix_from_id(db):
    """Agents sometimes copy the full context-line notation
    ("tenant <uuid>") into entity_id. The tool must strip that prefix
    rather than blow up with a misleading "Tenant not found" error.
    """
    task_id, tenant_external_id = _seed_message_person_task(db)
    prefixed_id = f"tenant {tenant_external_id}"

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None), \
         patch("gql.services.settings_service.get_action_policy_settings", return_value={
             "entity_changes": "balanced",
             "outbound_messages": "balanced",
             "suggestion_fallback": "balanced",
         }):
        payload = json.loads(_run_tool(
            MessageExternalPersonTool(),
            task_id=task_id,
            entity_id=prefixed_id,
            entity_type="tenant",
            draft_message="Hello.",
            risk_level="low",
        ))

    assert payload["status"] == "ok", payload
    # Entity was resolved and the suggestion carries the bare UUID on payload.
    suggestion = db.query(Suggestion).filter_by(id=payload["suggestion_id"]).one()
    assert suggestion.action_payload["entity_id"] == tenant_external_id


def test_message_person_inherits_task_id_from_active_conversation(db):
    """The active task's AI conversation is ground truth — when the agent
    omits task_id, message_person uses the task whose AI conversation we're
    chatting in. Prevents the "conversation orphaned outside the task" bug
    where new tenant threads got created with parent_task_id=NULL."""
    from db.models import Conversation, ConversationType
    from llm.tools._common import active_conversation_id

    task_id, tenant_id = _seed_message_person_task(db)
    task = db.query(Task).filter_by(id=int(task_id)).one()
    ai_convo = Conversation(
        org_id=1,
        creator_id=1,
        subject="Task chat",
        conversation_type=ConversationType.TASK_AI,
        is_group=False,
        is_archived=False,
    )
    db.add(ai_convo)
    db.flush()
    task.ai_conversation_id = ai_convo.id
    db.flush()

    token = active_conversation_id.set(str(ai_convo.id))
    try:
        with patch("db.session.SessionLocal.session_factory", return_value=db), \
             patch.object(db, "close", lambda: None), \
             patch("gql.services.settings_service.get_action_policy_settings", return_value={
                 "entity_changes": "balanced",
                 "outbound_messages": "balanced",
                 "suggestion_fallback": "balanced",
             }):
            payload = json.loads(_run_tool(
                MessageExternalPersonTool(),
                # Note: NO task_id passed.
                entity_id=tenant_id,
                entity_type="tenant",
                draft_message="Hi, quick check-in.",
                risk_level="low",
            ))
    finally:
        active_conversation_id.reset(token)

    assert payload["status"] == "ok", payload
    suggestion = db.query(Suggestion).filter_by(id=payload["suggestion_id"]).one()
    assert str(suggestion.task_id) == task_id, (
        "task_id should have been inferred from the active task AI conversation"
    )


def test_message_person_overrides_wrong_task_id_with_active_conversation(db):
    """Stricter rule: the active conversation overrides an agent-supplied
    task_id when they disagree. Blocks the hallucinated-task-id failure
    mode that landed conversations on the wrong task in production."""
    from db.models import Conversation, ConversationType
    from llm.tools._common import active_conversation_id

    # Two tasks with two AI conversations + one tenant.
    task_id, tenant_id = _seed_message_person_task(db)
    correct_task = db.query(Task).filter_by(id=int(task_id)).one()
    correct_convo = Conversation(
        org_id=1, creator_id=1, subject="Correct task",
        conversation_type=ConversationType.TASK_AI, is_group=False, is_archived=False,
    )
    db.add(correct_convo)
    db.flush()
    correct_task.ai_conversation_id = correct_convo.id

    other_task = Task(
        id=NumberAllocator.allocate_next(db, entity_type="task", org_id=1),
        org_id=1, creator_id=1, title="Other task",
    )
    db.add(other_task)
    db.flush()

    token = active_conversation_id.set(str(correct_convo.id))
    try:
        with patch("db.session.SessionLocal.session_factory", return_value=db), \
             patch.object(db, "close", lambda: None), \
             patch("gql.services.settings_service.get_action_policy_settings", return_value={
                 "entity_changes": "balanced",
                 "outbound_messages": "balanced",
                 "suggestion_fallback": "balanced",
             }):
            payload = json.loads(_run_tool(
                MessageExternalPersonTool(),
                # Agent passes the WRONG task id (hallucinated).
                task_id=str(other_task.id),
                entity_id=tenant_id,
                entity_type="tenant",
                draft_message="Hi.",
                risk_level="low",
            ))
    finally:
        active_conversation_id.reset(token)

    assert payload["status"] == "ok", payload
    suggestion = db.query(Suggestion).filter_by(id=payload["suggestion_id"]).one()
    assert str(suggestion.task_id) == task_id, (
        "active conversation's task should have overridden the agent's wrong task_id"
    )
    assert str(suggestion.task_id) != str(other_task.id)


def test_accepting_standalone_message_suggestion_creates_conversation_and_sends(db):
    """The deferred conversation is materialised at approval time by
    MessagePersonSuggestionExecutor. After the manager approves, the
    conversation exists, contains the drafted message, and the suggestion
    carries the resolved conversation_id on its action_payload.
    """
    from db.models import Conversation, ConversationType, Message
    from gql.services.task_suggestions import SuggestionExecutor
    _task_id_unused, tenant_id = _seed_message_person_task(db)

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None), \
         patch("gql.services.settings_service.get_action_policy_settings", return_value={
             "entity_changes": "balanced",
             "outbound_messages": "balanced",
             "suggestion_fallback": "balanced",
         }):
        payload = json.loads(_run_tool(
            MessageExternalPersonTool(),
            entity_id=tenant_id,
            entity_type="tenant",
            draft_message="Heads up — rent is due.",
            risk_level="critical",
        ))
    suggestion_id = payload["suggestion_id"]

    # Nothing exists yet on the standalone side.
    assert (
        db.query(Conversation)
        .filter(Conversation.parent_task_id.is_(None))
        .filter(Conversation.conversation_type == ConversationType.TENANT)
        .count()
    ) == 0

    executor = SuggestionExecutor.for_suggestion(db, suggestion_id)
    executor.execute(suggestion_id, "message_person_send")

    db.expire_all()
    suggestion = db.query(Suggestion).filter_by(id=suggestion_id).one()
    assert suggestion.status == "accepted"
    new_convo_id = suggestion.action_payload["conversation_id"]
    convo = db.query(Conversation).filter_by(id=int(new_convo_id)).one()
    assert convo.parent_task_id is None
    assert convo.conversation_type == ConversationType.TENANT
    messages = db.query(Message).filter_by(conversation_id=convo.id).all()
    assert len(messages) == 1
    assert messages[0].body == "Heads up — rent is due."


def test_task_scoped_tenant_message_auto_send_creates_conversation_and_persists_message(db):
    """A low-risk task-scoped tenant message auto-executes immediately.

    The executor must both create/reuse the tenant conversation and persist
    the outbound message into that task-scoped thread.
    """
    from db.models import Conversation, ConversationType, Message

    task_id, tenant_id = _seed_message_person_task(db)

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None), \
         patch("gql.services.settings_service.get_action_policy_settings", return_value={
             "entity_changes": "balanced",
             "outbound_messages": "balanced",
             "suggestion_fallback": "balanced",
         }):
        payload = json.loads(_run_tool(
            MessageExternalPersonTool(),
            task_id=task_id,
            entity_id=tenant_id,
            entity_type="tenant",
            draft_message="Can you provide access next Thursday for the cleanup?",
            risk_level="low",
        ))

    assert payload["status"] == "ok", payload

    suggestion = db.query(Suggestion).filter_by(id=payload["suggestion_id"]).one()
    assert suggestion.status == "accepted"

    db.expire_all()
    convo = (
        db.query(Conversation)
        .filter_by(parent_task_id=int(task_id), conversation_type=ConversationType.TENANT)
        .one()
    )
    messages = db.query(Message).filter_by(conversation_id=convo.id).order_by(Message.sent_at.asc()).all()
    assert len(messages) == 1
    assert messages[0].body == "Can you provide access next Thursday for the cleanup?"


# ── record_task_review tool ──────────────────────────────────────────────────


def test_record_task_review_writes_columns_and_trace(db):
    """The tool writes the four last_review_* columns on the target Task
    and logs a mirror AgentTrace row so the history is queryable.
    """
    from db.models import AgentRun, AgentTrace
    from llm.runs import start_run
    from llm.tools import RecordTaskReviewTool

    task = Task(
        id=NumberAllocator.allocate_next(db, entity_type="task", org_id=1),
        org_id=1,
        creator_id=1,
        title="Kitchen sink repair",
    )
    db.add(task)
    db.flush()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        with start_run(
            source="task_review",
            task_id=str(task.id),
            agent_version="rentmate-test",
            execution_path="local",
            trigger_input="review me",
        ):
            payload = json.loads(_run_tool(
                RecordTaskReviewTool(),
                task_id=str(task.id),
                status="needs_action",
                summary="Waiting on plumber quote; follow up in 24h.",
                next_step="Ping vendor for quote status.",
            ))

    assert payload["status"] == "ok"
    db.refresh(task)
    assert task.last_review_status == "needs_action"
    assert task.last_review_summary.startswith("Waiting on plumber quote")
    assert task.last_review_next_step == "Ping vendor for quote status."
    assert task.last_reviewed_at is not None

    traces = (
        db.query(AgentTrace)
        .join(
            AgentRun,
            (AgentTrace.org_id == AgentRun.org_id) & (AgentTrace.run_id == AgentRun.id),
        )
        .filter(AgentRun.task_id == str(task.id), AgentTrace.trace_type == "task_review")
        .all()
    )
    assert len(traces) == 1
    assert traces[0].summary == "Waiting on plumber quote; follow up in 24h."


def test_ask_manager_posts_to_task_ai_conversation(db):
    """ask_manager inserts the agent's question into the task's AI
    conversation as an AI-authored message; the manager reads it in the
    task chat just like any other agent reply.
    """
    from llm.tools import AskManagerTool

    convo = Conversation(
        org_id=1,
        creator_id=1,
        subject="Leak",
        conversation_type=ConversationType.TASK_AI,
        is_group=False,
        is_archived=False,
    )
    db.add(convo)
    db.flush()
    task = Task(
        id=NumberAllocator.allocate_next(db, entity_type="task", org_id=1),
        org_id=1,
        creator_id=1,
        title="Kitchen sink",
        ai_conversation_id=convo.id,
    )
    db.add(task)
    db.flush()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        payload = json.loads(_run_tool(
            AskManagerTool(),
            task_id=str(task.id),
            question="Should I approve the $450 plumber quote?",
        ))

    assert payload["status"] == "ok"
    assert payload["action_card_kind"] == "question"
    messages = db.query(Message).filter_by(conversation_id=convo.id).all()
    assert len(messages) == 1
    msg = messages[0]
    assert msg.is_ai is True
    assert "plumber quote" in msg.body
    # Routes through ActionCardBubble: ACTION + meta.action_card.kind="question".
    assert msg.message_type == MessageType.ACTION
    assert msg.meta is not None
    assert msg.meta["action_card"]["kind"] == "question"
    assert msg.meta["action_card"]["title"] == "Should I approve the $450 plumber quote?"
    notifications = db.query(Notification).filter_by(
        recipient_user_id=task.creator_id,
        conversation_id=convo.id,
        task_id=task.id,
        kind="manager_attention",
    ).all()
    assert len(notifications) == 1
    assert notifications[0].title.startswith("Task needs your input")
    assert notifications[0].body == "Should I approve the $450 plumber quote?"


def test_ask_manager_errors_when_task_has_no_ai_conversation(db):
    from llm.tools import AskManagerTool

    task = Task(
        id=NumberAllocator.allocate_next(db, entity_type="task", org_id=1),
        org_id=1,
        creator_id=1,
        title="Orphan",
    )
    db.add(task)
    db.flush()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        payload = json.loads(_run_tool(
            AskManagerTool(),
            task_id=str(task.id),
            question="Anything?",
        ))

    assert payload["status"] == "error"
    assert "AI conversation" in payload["message"]


def test_record_task_review_rejects_unknown_status(db):
    from llm.tools import RecordTaskReviewTool

    task = Task(
        id=NumberAllocator.allocate_next(db, entity_type="task", org_id=1),
        org_id=1,
        creator_id=1,
        title="Task",
    )
    db.add(task)
    db.flush()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        payload = json.loads(_run_tool(
            RecordTaskReviewTool(),
            task_id=str(task.id),
            status="zooming",
            summary="nope",
        ))

    assert payload["status"] == "error"
    assert "status" in payload["message"]
    db.refresh(task)
    assert task.last_reviewed_at is None


# ── lookup_tenants tool ──────────────────────────────────────────────────────


def test_lookup_tenants_returns_external_ids(db):
    user = User(
        org_id=1,
        creator_id=1,
        user_type="tenant",
        first_name="Priya",
        last_name="Patel",
        email="priya@example.com",
        phone="+15550009999",
    )
    db.add(user)
    db.flush()
    tenant = Tenant(org_id=1, creator_id=1, user_id=user.id)
    db.add(tenant)
    db.flush()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        payload = json.loads(_run_tool(LookupTenantsTool(), active_only=False))

    assert payload["count"] == 1
    row = payload["tenants"][0]
    assert row["tenant_id"] == tenant.external_id
    assert row["name"] == "Priya Patel"
    assert row["email"] == "priya@example.com"
    assert row["phone"] == "+15550009999"
    assert row["lease_active"] is False


def _seed_property(db, *, name=None, address_line1="123 Main St", city="Bellevue", state="WA", org_id=1):
    prop = Property(
        org_id=org_id,
        creator_id=org_id,
        name=name,
        address_line1=address_line1,
        city=city,
        state=state,
        property_type="multi_family",
    )
    db.add(prop)
    db.flush()
    return prop


def test_lookup_properties_returns_match_by_name(db):
    _seed_property(db, name="The Meadows", address_line1="1842 Meadow Lane")
    _seed_property(db, name="Pinecrest Apartments", address_line1="3310 Pine Street")

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        payload = json.loads(_run_tool(LookupPropertiesTool(), query="meadow"))

    assert payload["count"] == 1
    assert payload["properties"][0]["name"] == "The Meadows"


def test_lookup_properties_returns_match_by_address(db):
    _seed_property(db, name="The Meadows", address_line1="1842 Meadow Lane", city="Bellevue")
    _seed_property(db, name="Northshore", address_line1="221 Bothell Way", city="Bothell")

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        payload = json.loads(_run_tool(LookupPropertiesTool(), query="bothell"))

    assert payload["count"] == 1
    assert payload["properties"][0]["name"] == "Northshore"


def test_lookup_properties_returns_empty_when_no_match(db):
    _seed_property(db, name="The Meadows")

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        payload = json.loads(_run_tool(LookupPropertiesTool(), query="bothell"))

    assert payload["properties"] == []
    assert "bothell" in payload["message"].lower()
    assert "lookup_properties" in payload["message"] or "ask the manager" in payload["message"].lower()


def test_lookup_properties_exact_id_lookup(db):
    target = _seed_property(db, name="The Meadows")
    _seed_property(db, name="Pinecrest")

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        payload = json.loads(_run_tool(LookupPropertiesTool(), property_id=str(target.id)))

    assert payload["count"] == 1
    assert payload["properties"][0]["property_id"] == str(target.id)


def test_propose_task_rejects_unknown_property_id(db):
    vendor = User(
        org_id=1, creator_id=1, user_type="vendor",
        first_name="Sarah", last_name="Chen",
        role_label="Landscaper", phone="+15550005555",
    )
    db.add(vendor)
    db.flush()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        payload = json.loads(_run_tool(
            ProposeTaskTool(),
            title="Tree trimming at Bothell",
            category="maintenance",
            vendor_id=vendor.external_id,
            goal="Trim the trees at the Bothell property and confirm completion.",
            steps=[
                {"key": "quote", "label": "Get a quote", "status": "active"},
                {"key": "schedule", "label": "Schedule the work", "status": "pending"},
                {"key": "confirm", "label": "Confirm completion", "status": "pending"},
            ],
            property_id="00000000-0000-0000-0000-000000000bad",
        ))

    assert payload["status"] == "error"
    assert "lookup_properties" in payload["message"]
    # And nothing was staged.
    assert db.query(Suggestion).count() == 0


def test_lookup_tenants_filters_by_query(db):
    for first, last, email in [
        ("Priya", "Patel", "priya@example.com"),
        ("Alex", "Nakamura", "alex@example.com"),
    ]:
        u = User(
            org_id=1, creator_id=1, user_type="tenant",
            first_name=first, last_name=last, email=email,
        )
        db.add(u)
        db.flush()
        db.add(Tenant(org_id=1, creator_id=1, user_id=u.id))
    db.flush()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        payload = json.loads(_run_tool(
            LookupTenantsTool(), query="priya", active_only=False,
        ))

    assert payload["count"] == 1
    assert payload["tenants"][0]["name"] == "Priya Patel"


# ── task_id active-conversation override ─────────────────────────────────────


def _seed_task_with_ai_conversation(db, *, title: str = "Task") -> Task:
    convo = Conversation(
        org_id=1, creator_id=1, subject=title,
        conversation_type=ConversationType.TASK_AI,
        is_group=False, is_archived=False,
    )
    db.add(convo)
    db.flush()
    task = Task(
        id=NumberAllocator.allocate_next(db, entity_type="task", org_id=1),
        org_id=1, creator_id=1, title=title,
        ai_conversation_id=convo.id,
    )
    db.add(task)
    db.flush()
    return task


def test_record_task_review_overrides_wrong_task_id_with_active_conversation(db):
    """Active conversation wins over a hallucinated agent-supplied task_id —
    the review lands on the correct task even when the agent passes the
    wrong one."""
    from llm.tools import RecordTaskReviewTool

    correct_task = _seed_task_with_ai_conversation(db, title="Correct task")
    other_task = Task(
        id=NumberAllocator.allocate_next(db, entity_type="task", org_id=1),
        org_id=1, creator_id=1, title="Other task",
    )
    db.add(other_task)
    db.flush()

    token = active_conversation_id.set(str(correct_task.ai_conversation_id))
    try:
        with patch("db.session.SessionLocal.session_factory", return_value=db), \
             patch.object(db, "close", lambda: None):
            payload = json.loads(_run_tool(
                RecordTaskReviewTool(),
                task_id=str(other_task.id),  # WRONG — hallucinated
                status="on_track",
                summary="Looks good.",
            ))
    finally:
        active_conversation_id.reset(token)

    assert payload["status"] == "ok"
    assert payload["task_id"] == str(correct_task.id)
    db.refresh(correct_task)
    db.refresh(other_task)
    assert correct_task.last_review_status == "on_track"
    assert other_task.last_review_status is None


def test_update_task_progress_overrides_wrong_task_id_with_active_conversation(db):
    correct_task = _seed_task_with_ai_conversation(db, title="Correct task")
    correct_task.steps = [
        {"key": "step_a", "label": "Step A", "status": "active"},
    ]
    other_task = Task(
        id=NumberAllocator.allocate_next(db, entity_type="task", org_id=1),
        org_id=1, creator_id=1, title="Other task",
        steps=[{"key": "step_a", "label": "Step A", "status": "active"}],
    )
    db.add(other_task)
    db.flush()

    token = active_conversation_id.set(str(correct_task.ai_conversation_id))
    try:
        with patch("db.session.SessionLocal.session_factory", return_value=db), \
             patch.object(db, "close", lambda: None):
            payload = json.loads(_run_tool(
                UpdateTaskProgressTool(),
                task_id=str(other_task.id),  # WRONG — hallucinated
                step_key="step_a",
                status="done",
            ))
    finally:
        active_conversation_id.reset(token)

    assert payload["status"] == "ok"
    db.refresh(correct_task)
    db.refresh(other_task)
    assert correct_task.steps[0]["status"] == "done"
    assert other_task.steps[0]["status"] == "active"


def test_update_task_progress_rejects_unknown_status(db):
    """status='waiting' (a review-status word, not a step status) must be
    rejected up front with a tool-friendly error — not crash via Pydantic."""
    task = _seed_task_with_ai_conversation(db, title="A task")
    task.steps = [{"key": "step_a", "label": "Step A", "status": "active"}]
    db.flush()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        payload = json.loads(_run_tool(
            UpdateTaskProgressTool(),
            task_id=str(task.id),
            step_key="step_a",
            status="waiting",
        ))

    assert payload["status"] == "error"
    assert "waiting" in payload["message"]
    assert "record_task_review" in payload["message"]


def test_update_task_progress_tolerates_legacy_invalid_stored_status(db):
    """A task with a step stored as 'waiting' (legacy/bad data) must not
    lock the agent out — the loader coerces unknown stored statuses to
    'pending' so progress updates can still complete."""
    task = _seed_task_with_ai_conversation(db, title="A task")
    task.steps = [
        {"key": "step_a", "label": "Step A", "status": "waiting"},
        {"key": "step_b", "label": "Step B", "status": "pending"},
    ]
    db.flush()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        payload = json.loads(_run_tool(
            UpdateTaskProgressTool(),
            task_id=str(task.id),
            step_key="step_b",
            status="active",
        ))

    assert payload["status"] == "ok"
    db.refresh(task)
    by_key = {s["key"]: s for s in task.steps}
    # Legacy 'waiting' has been normalized through the loader so the next
    # write doesn't carry the bad value forward.
    assert by_key["step_a"]["status"] == "pending"
    assert by_key["step_b"]["status"] == "active"


def test_propose_task_rejects_invalid_step_status_with_tool_friendly_error(db):
    vendor = User(
        org_id=1, creator_id=1, user_type="vendor",
        first_name="Sarah", last_name="Chen",
        role_label="Landscaper", phone="+15550005555",
    )
    db.add(vendor)
    db.flush()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        payload = json.loads(_run_tool(
            ProposeTaskTool(),
            title="Tree trimming",
            category="maintenance",
            vendor_id=vendor.external_id,
            goal="Trim the trees and confirm completion.",
            steps=[
                {"key": "wait", "label": "Wait for vendor", "status": "waiting"},
                {"key": "schedule", "label": "Schedule", "status": "pending"},
            ],
        ))

    assert payload["status"] == "error"
    assert "step" in payload["message"].lower()
    assert "record_task_review" in payload["message"]
    assert db.query(Suggestion).count() == 0


def test_ask_manager_overrides_wrong_task_id_with_active_conversation(db):
    from llm.tools import AskManagerTool

    correct_task = _seed_task_with_ai_conversation(db, title="Correct task")
    other_task = Task(
        id=NumberAllocator.allocate_next(db, entity_type="task", org_id=1),
        org_id=1, creator_id=1, title="Other task",
    )
    db.add(other_task)
    db.flush()

    token = active_conversation_id.set(str(correct_task.ai_conversation_id))
    try:
        with patch("db.session.SessionLocal.session_factory", return_value=db), \
             patch.object(db, "close", lambda: None):
            payload = json.loads(_run_tool(
                AskManagerTool(),
                task_id=str(other_task.id),  # WRONG — hallucinated
                question="What should I do?",
            ))
    finally:
        active_conversation_id.reset(token)

    assert payload["status"] == "ok"
    assert payload["task_id"] == str(correct_task.id)
    assert payload["conversation_id"] == str(correct_task.ai_conversation_id)
    messages = db.query(Message).filter_by(
        conversation_id=correct_task.ai_conversation_id
    ).all()
    assert len(messages) == 1
    assert messages[0].meta["action_card"]["kind"] == "question"
