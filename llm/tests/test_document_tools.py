import asyncio
import json
import logging
from unittest.mock import patch

from db.models import AgentTrace, Document
from llm.tools import AnalyzeDocumentTool, ReadDocumentTool


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
