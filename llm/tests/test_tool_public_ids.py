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
    ParticipantType,
    Property,
    Suggestion,
    Task,
    Tenant,
    Unit,
    User,
)
from llm.generated_documents import RenderedDocument
from llm.tools import (
    CreateDocumentTool,
    CreatePropertyTool,
    CreateSuggestionTool,
    CreateTenantTool,
    CreateVendorTool,
    LookupVendorsTool,
    MessageExternalPersonTool,
    ProposeTaskTool,
    RecallMemoryTool,
    SaveMemoryTool,
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
    task = Task(org_id=1, creator_id=1, title="Fix sink")
    db.add_all([tenant, task])
    db.flush()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None), \
         patch("llm.action_policy.get_action_policy_settings", return_value={
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
    assert "blocked by outbound policy" in payload["policy_reason"]
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
            draft_message="Can you take a look at this leak?",
        ))

    suggestion = db.query(Suggestion).filter_by(id=payload["suggestion_id"]).one()
    assert suggestion.action_payload["vendor_id"] == vendor.external_id


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
    task = Task(org_id=1, creator_id=1, title="Garage door is broken", ai_conversation_id=convo.id)
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
    task.external_conversation_id = vendor_conv.id
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
    task = Task(org_id=1, creator_id=1, title="Garage door is broken", ai_conversation_id=convo.id)
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
    task.external_conversation_id = vendor_conv.id
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
    task = Task(org_id=1, creator_id=1, title="Eviction proceedings", ai_conversation_id=convo.id)
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
    task = Task(org_id=1, creator_id=1, title="Eviction proceedings", ai_conversation_id=convo.id)
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
    task = Task(org_id=1, creator_id=1, title="Eviction proceedings", ai_conversation_id=convo.id)
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
    task = Task(org_id=1, creator_id=1, title="Eviction proceedings", ai_conversation_id=convo.id)
    db.add(task)
    db.flush()
    db.add(Suggestion(
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
