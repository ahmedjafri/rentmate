from unittest.mock import patch

import pytest

from llm.doc_gen_loader import load_doc_gen_module
from llm.doc_gen_runtime import RenderDocumentResult

generated_documents = load_doc_gen_module("generated_documents")


def test_render_document_wraps_html_and_returns_pdf_bytes():
    with patch.object(
        generated_documents,
        "get_doc_gen_client",
        return_value=type(
            "FakeClient",
            (),
            {"render_document": lambda self, request: RenderDocumentResult(b"%PDF-1.4 html pdf", "weasyprint")},
        )(),
    ):
        rendered = generated_documents.render_document(
            title="14-Day Notice",
            html_content="<h2>Notice</h2><div class='field-row'><div class='field-label'>Tenant</div><div class='field-value'>Bob</div></div>",
        )

    assert rendered.renderer == "weasyprint"
    assert rendered.pdf_bytes.startswith(b"%PDF-1.4")
    assert "<title>14-Day Notice</title>" in rendered.html
    assert "Prepared By RentMate" in rendered.html
    assert "RentMate" in rendered.html
    assert "<h2>Notice</h2>" in rendered.html


def test_render_document_uses_text_fallback_when_html_missing():
    with patch.object(
        generated_documents,
        "get_doc_gen_client",
        return_value=type(
            "FakeClient",
            (),
            {"render_document": lambda self, request: RenderDocumentResult(b"%PDF-1.4 text fallback", "weasyprint")},
        )(),
    ):
        rendered = generated_documents.render_document(title="Fallback", text_content="Tenant: Bob Ferguson\n\n- Pay in full")

    assert "field-label" in rendered.html
    assert "Bob Ferguson" in rendered.html
    assert "bullet-list" in rendered.html


def test_render_document_raises_when_helper_fails():
    with patch.object(
        generated_documents,
        "get_doc_gen_client",
        return_value=type(
            "FakeClient",
            (),
            {"render_document": lambda self, request: (_ for _ in ()).throw(RuntimeError("weasyprint exploded"))},
        )(),
    ), pytest.raises(RuntimeError, match="weasyprint exploded"):
        generated_documents.render_document(title="Failure", html_content="<p>bad</p>")
