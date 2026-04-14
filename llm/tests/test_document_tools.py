import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import patch

from pypdf import PdfWriter

from db.models import AgentTrace, Document
from llm.tools import AnalyzeDocumentTool, ReadDocumentTool


def _blank_pdf_bytes() -> bytes:
    from io import BytesIO

    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_read_document_returns_structured_failure_and_trace(db, caplog):
    doc = Document(
        id="doc-error-1",
        org_id=1,
        creator_id=1,
        filename="lease.pdf",
        document_type="lease",
        status="error",
        progress="ocr_failed",
        error_message="OCR extraction failed on page 2",
    )
    db.add(doc)
    db.commit()

    with caplog.at_level(logging.ERROR, logger="rentmate.llm.tools"):
        payload = json.loads(asyncio.run(ReadDocumentTool().execute(document_id=doc.id)))

    assert payload["status"] == "error"
    assert payload["message"] == "OCR extraction failed on page 2"
    assert payload["detail"]["document_status"] == "error"
    assert payload["detail"]["filename"] == "lease.pdf"
    assert "document in error state for lease.pdf" in caplog.text
    assert '"error_message": "OCR extraction failed on page 2"' in caplog.text

    trace = db.query(AgentTrace).filter_by(tool_name="read_document").order_by(AgentTrace.timestamp.desc()).first()
    assert trace is not None
    assert "lease.pdf" in trace.summary
    assert "OCR extraction failed on page 2" in (trace.detail or "")


def test_analyze_document_logs_exception_details(db, caplog):
    doc = Document(
        id="doc-error-2",
        org_id=1,
        creator_id=1,
        filename="notice.pdf",
        document_type="notice",
        status="pending",
    )
    db.add(doc)
    db.commit()

    with caplog.at_level(logging.ERROR, logger="rentmate.llm.tools"), \
         patch("llm.document_processor.process_document", side_effect=RuntimeError("processor offline")):
        payload = json.loads(asyncio.run(AnalyzeDocumentTool().execute(document_id=doc.id)))

    assert payload["status"] == "error"
    assert payload["message"] == "processor offline"
    assert payload["detail"]["error_type"] == "RuntimeError"
    assert "processor offline" in payload["detail"]["traceback"]
    assert "analyze_document: crashed: RuntimeError" in caplog.text
    assert '"document_id": "doc-error-2"' in caplog.text

    trace = db.query(AgentTrace).filter_by(tool_name="analyze_document").order_by(AgentTrace.timestamp.desc()).first()
    assert trace is not None
    assert "RuntimeError" in trace.summary
    assert "processor offline" in (trace.detail or "")


def test_analyze_document_full_tool_path_persists_and_reads_extracted_fields(db):
    doc = Document(
        id="doc-e2e-1",
        org_id=1,
        creator_id=1,
        filename="lease.pdf",
        document_type="lease",
        status="pending",
        storage_path="documents/doc-e2e-1/lease.pdf",
    )
    db.add(doc)
    db.commit()

    extraction_payload = {
        "leases": [
            {
                "tenant_first_name": "Bob",
                "tenant_last_name": "Ferguson",
                "tenant_email": None,
                "tenant_phone": None,
                "property_address": "123 Test St Seattle WA 98101",
                "unit_label": "Main",
                "lease_start_date": "2026-01-01",
                "lease_end_date": "2026-12-31",
                "monthly_rent": 2500,
                "property_type": "multi_family",
                "property_context": "Smoke detectors provided; landlord contact on file.",
                "unit_context": "Garage parking included; tenant pays electric.",
                "tenant_context": "No smoking; max two pets.",
                "lease_context": "Security deposit $3000; late fee $75 after 5th day.",
            }
        ]
    }

    fake_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(extraction_payload)))]
    )
    with (
        patch("db.session.SessionLocal.session_factory", return_value=db),
        patch.object(db, "close", lambda: None),
        patch("llm.document_processor._get_session_factory", return_value=lambda: db),
        patch("backends.wire.storage_backend.download", return_value=_blank_pdf_bytes()),
        patch("litellm.completion", return_value=fake_response),
    ):
        analyze_payload = json.loads(asyncio.run(AnalyzeDocumentTool().execute(document_id=doc.id)))
        read_payload = json.loads(asyncio.run(ReadDocumentTool().execute(document_id=doc.id)))

    assert analyze_payload["status"] == "ok"
    db.refresh(doc)
    assert doc.status == "done"
    lease = doc.extracted_data["leases"][0]
    assert lease["property_type"] == "multi_family"
    assert "Smoke detectors" in lease["property_context"]
    assert "Garage parking" in lease["unit_context"]
    assert "No smoking" in lease["tenant_context"]
    assert "Security deposit $3000" in lease["lease_context"]
    assert "Property: Smoke detectors provided" in doc.context
    assert "Lease: Security deposit $3000" in doc.context

    assert read_payload["status"] == "ok"
    extracted = read_payload["document"]["extracted_data"]["leases"][0]
    assert extracted["property_type"] == "multi_family"
    assert extracted["lease_context"].startswith("Security deposit $3000")
