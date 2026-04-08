"""Tests for the document processing pipeline (llm/document_processor.py).

Exercises PDF extraction → text chunking → vector storage → LLM extraction,
using a real PDF file and mocking only external services (storage download,
LLM completion, vector embeddings).
"""
import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from db.models import Document
from llm.document_processor import _split_text, process_document

_SAMPLE_PDF = Path(__file__).resolve().parents[2] / "evals" / "sample_rental_agreement.pdf"


# ---------------------------------------------------------------------------
# _split_text unit tests
# ---------------------------------------------------------------------------


class TestSplitText:
    def test_empty_string(self):
        assert _split_text("") == []

    def test_short_text_single_chunk(self):
        result = _split_text("Hello world", chunk_size=800)
        assert result == ["Hello world"]

    def test_chunks_at_paragraph_boundary(self):
        text = ("A" * 400 + "\n\n" + "B" * 400)
        result = _split_text(text, chunk_size=500, overlap=50)
        assert len(result) >= 2
        assert all(len(c) <= 500 for c in result)

    def test_forward_progress_guaranteed(self):
        """Regression: _split_text must not loop forever when separators are
        close to `start`, which previously caused `start` to move backwards."""
        # Text with separators every 50 chars, overlap=100 — old bug would loop
        text = " ".join(["word"] * 500)
        result = _split_text(text, chunk_size=200, overlap=100)
        assert len(result) < 200  # should be ~15-20, not millions
        rejoined = " ".join(result)
        # All original words should appear at least once
        assert rejoined.count("word") >= 500

    def test_overlap_creates_shared_content(self):
        text = "The quick brown fox jumps over the lazy dog. " * 50
        result = _split_text(text, chunk_size=100, overlap=30)
        assert len(result) > 1
        # Adjacent chunks should share some text (overlap)
        for i in range(len(result) - 1):
            # The end of chunk[i] should overlap with start of chunk[i+1]
            tail = result[i][-20:]
            assert any(
                tail[j:] in result[i + 1] for j in range(len(tail))
            ), f"No overlap between chunk {i} and {i+1}"


# ---------------------------------------------------------------------------
# process_document integration test
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("db")
class TestProcessDocument:
    """End-to-end test of the document processing pipeline.

    Uses the real sample PDF but mocks:
    - storage_backend.download (returns the PDF bytes directly)
    - vector_backend._embed (avoids real embedding API call)
    - litellm.completion (avoids real LLM call)
    - _get_session_factory (uses the test DB session)
    """

    @pytest.fixture(autouse=True)
    def _setup(self, db: Session):
        self.db = db
        if not _SAMPLE_PDF.exists():
            pytest.skip(f"Sample PDF not found: {_SAMPLE_PDF}")

    def _create_doc(self) -> str:
        doc_id = str(uuid.uuid4())
        doc = Document(
            id=doc_id,
            filename="sample_rental_agreement.pdf",
            content_type="application/pdf",
            storage_path=f"documents/{doc_id}/sample_rental_agreement.pdf",
            document_type="lease",
            status="pending",
            sha256_checksum="test",
            created_at=datetime.now(UTC),
        )
        self.db.add(doc)
        self.db.flush()
        return doc_id

    def test_process_document_full_pipeline(self):
        """Upload a real PDF and verify text extraction, chunking, vector
        storage, and LLM extraction all succeed end-to-end."""
        doc_id = self._create_doc()
        pdf_bytes = _SAMPLE_PDF.read_bytes()

        fake_llm_response = MagicMock()
        fake_llm_response.choices = [
            MagicMock(message=MagicMock(content=json.dumps({
                "leases": [{
                    "tenant_first_name": "John",
                    "tenant_last_name": "Doe",
                    "property_address": "123 Main St",
                    "monthly_rent": 1500,
                }]
            })))
        ]

        # Track vector backend calls to verify keyword args
        vector_calls = []

        def _capture_add_document(doc_id_arg, *, chunks, metadatas):
            vector_calls.append({
                "doc_id": doc_id_arg,
                "num_chunks": len(chunks),
                "num_metadatas": len(metadatas),
            })

        with (
            patch("llm.document_processor._get_session_factory", return_value=lambda: self.db),
            patch("llm.document_processor._set_progress"),
            patch("backends.wire.storage_backend.download", new_callable=AsyncMock, return_value=pdf_bytes),
            patch("backends.wire.vector_backend.add_document", side_effect=_capture_add_document),
            patch("litellm.completion", return_value=fake_llm_response),
        ):
            asyncio.get_event_loop().run_until_complete(process_document(doc_id))

        doc = self.db.query(Document).filter_by(id=doc_id).one()

        # Status should be done
        assert doc.status == "done", f"Expected 'done', got '{doc.status}' (error: {doc.error_message})"
        assert doc.processed_at is not None

        # Raw text should have been extracted
        assert doc.raw_text is not None
        assert len(doc.raw_text) > 100

        # Extraction meta populated
        assert doc.extraction_meta is not None
        assert doc.extraction_meta["page_count"] > 0
        assert doc.extraction_meta["raw_text_chars"] > 0

        # Vector backend was called with keyword args and got chunks
        assert len(vector_calls) == 1
        assert vector_calls[0]["num_chunks"] > 0
        assert vector_calls[0]["num_metadatas"] == vector_calls[0]["num_chunks"]

        # LLM extraction produced results
        assert doc.extracted_data is not None
        assert "leases" in doc.extracted_data
        assert len(doc.extracted_data["leases"]) == 1
        assert doc.extracted_data["leases"][0]["tenant_first_name"] == "John"

    def test_process_document_propagates_errors(self):
        """If the storage download fails, the exception should propagate."""
        doc_id = self._create_doc()

        with (
            patch("llm.document_processor._get_session_factory", return_value=lambda: self.db),
            patch("llm.document_processor._set_progress"),
            patch("backends.wire.storage_backend.download", new_callable=AsyncMock, side_effect=FileNotFoundError("gone")),
        ):
            with pytest.raises(FileNotFoundError, match="gone"):
                asyncio.get_event_loop().run_until_complete(process_document(doc_id))

    def test_vector_backend_called_with_keyword_args(self):
        """Regression: add_document must be called with keyword-only
        chunks/metadatas args, not positional."""
        doc_id = self._create_doc()
        pdf_bytes = _SAMPLE_PDF.read_bytes()

        mock_vector = MagicMock()
        fake_llm = MagicMock()
        fake_llm.choices = [MagicMock(message=MagicMock(content='{"leases": []}'))]

        with (
            patch("llm.document_processor._get_session_factory", return_value=lambda: self.db),
            patch("llm.document_processor._set_progress"),
            patch("backends.wire.storage_backend.download", new_callable=AsyncMock, return_value=pdf_bytes),
            patch("backends.wire.vector_backend.add_document", mock_vector),
            patch("litellm.completion", return_value=fake_llm),
        ):
            asyncio.get_event_loop().run_until_complete(process_document(doc_id))

        # Verify add_document was called and used keyword args
        mock_vector.assert_called_once()
        call_kwargs = mock_vector.call_args
        assert "chunks" in call_kwargs.kwargs, "add_document must be called with chunks as keyword arg"
        assert "metadatas" in call_kwargs.kwargs, "add_document must be called with metadatas as keyword arg"
        assert len(call_kwargs.args) == 1, "Only doc_id should be positional"
