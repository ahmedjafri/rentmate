"""Tests for handlers/documents.py endpoints."""
import os
import unittest
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from backends.local_auth import DEFAULT_USER_ID
from db.models import Document, DocumentTag, Property
from handlers.deps import get_db
from main import app


def _make_property(db, prop_id):
    prop = Property(
        id=prop_id,
        address_line1="1 Main St",
        city="Toronto",
    )
    db.add(prop)
    db.flush()
    return prop


def make_token():
    import jwt
    return jwt.encode(
        {"sub": DEFAULT_USER_ID, "email": "admin@localhost"},
        os.getenv("JWT_SECRET", "rentmate-local-secret"),
        algorithm="HS256",
    )


AUTH = {"Authorization": f"Bearer {make_token()}"}


def _make_doc(db, doc_id=None, status="done", extracted_data=None):
    doc = Document(
        id=doc_id or str(uuid.uuid4()),
        filename="test.pdf",
        content_type="application/pdf",
        storage_path=f"documents/{doc_id or 'test'}/test.pdf",
        document_type="lease",
        status=status,
        sha256_checksum="abc123",
        extracted_data=extracted_data,
        created_at=datetime.utcnow(),
    )
    db.add(doc)
    db.flush()
    return doc


# ---------------------------------------------------------------------------
# GET /documents
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("db")
class TestListDocuments(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        app.dependency_overrides[get_db] = lambda: self.db

    def tearDown(self):
        app.dependency_overrides = {}

    def test_requires_auth(self):
        response = self.client.get("/api/documents")
        assert response.status_code == 401

    def test_returns_empty_list(self):
        response = self.client.get("/api/documents", headers=AUTH)
        assert response.status_code == 200
        assert response.json() == []

    def test_returns_document_list(self):
        _make_doc(self.db, doc_id="doc-001")
        response = self.client.get("/api/documents", headers=AUTH)
        assert response.status_code == 200
        docs = response.json()
        assert len(docs) == 1
        assert docs[0]["id"] == "doc-001"
        assert docs[0]["filename"] == "test.pdf"
        assert docs[0]["status"] == "done"


# ---------------------------------------------------------------------------
# GET /document/{document_id}
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("db")
class TestGetDocument(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        app.dependency_overrides[get_db] = lambda: self.db

    def tearDown(self):
        app.dependency_overrides = {}

    def test_requires_auth(self):
        response = self.client.get("/api/document/some-id")
        assert response.status_code == 401

    def test_not_found(self):
        response = self.client.get("/api/document/nonexistent", headers=AUTH)
        assert response.status_code == 404

    def test_returns_document(self):
        _make_doc(self.db, doc_id="doc-get-1")
        response = self.client.get("/api/document/doc-get-1", headers=AUTH)
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "doc-get-1"
        assert data["filename"] == "test.pdf"
        assert data["status"] == "done"


# ---------------------------------------------------------------------------
# DELETE /document/{document_id}
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("db")
class TestDeleteDocument(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        app.dependency_overrides[get_db] = lambda: self.db

    def tearDown(self):
        app.dependency_overrides = {}

    def test_requires_auth(self):
        response = self.client.delete("/api/document/some-id")
        assert response.status_code == 401

    def test_not_found(self):
        response = self.client.delete("/api/document/nonexistent", headers=AUTH)
        assert response.status_code == 404

    def test_deletes_document(self):
        _make_doc(self.db, doc_id="doc-del-1")
        with (
            patch("backends.wire.storage_backend.delete", new_callable=AsyncMock),
            patch("backends.wire.vector_backend.delete_document"),
        ):
            response = self.client.delete("/api/document/doc-del-1", headers=AUTH)
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        assert self.db.query(Document).filter_by(id="doc-del-1").one_or_none() is None

    def test_delete_ignores_storage_error(self):
        _make_doc(self.db, doc_id="doc-del-2")
        with (
            patch("backends.wire.storage_backend.delete", new_callable=AsyncMock, side_effect=Exception("storage down")),
            patch("backends.wire.vector_backend.delete_document"),
        ):
            response = self.client.delete("/api/document/doc-del-2", headers=AUTH)
        assert response.status_code == 200
        assert response.json() == {"ok": True}


# ---------------------------------------------------------------------------
# GET /document/{document_id}/suggestions
# ---------------------------------------------------------------------------


# TestGetDocumentSuggestions removed — document suggestion endpoints replaced
# by agent-driven create_suggestion tool.


# ---------------------------------------------------------------------------
# GET /document/{document_id}/tags and POST /document/{document_id}/tags
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("db")
class TestDocumentTags(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        app.dependency_overrides[get_db] = lambda: self.db

    def tearDown(self):
        app.dependency_overrides = {}

    def test_get_tags_empty(self):
        _make_doc(self.db, doc_id="doc-tag-1")
        response = self.client.get("/api/document/doc-tag-1/tags", headers=AUTH)
        assert response.status_code == 200
        assert response.json() == []

    def test_add_tag(self):
        prop = _make_property(self.db, "prop-tag-x")
        _make_doc(self.db, doc_id="doc-tag-2")
        response = self.client.post(
            "/api/document/doc-tag-2/tags",
            json={"tag_type": "property", "property_id": prop.id},
            headers=AUTH,
        )
        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert data["existed"] is False

    def test_add_tag_deduplicates(self):
        prop = _make_property(self.db, "prop-tag-y")
        _make_doc(self.db, doc_id="doc-tag-3")
        # Add once
        self.client.post(
            "/api/document/doc-tag-3/tags",
            json={"tag_type": "property", "property_id": prop.id},
            headers=AUTH,
        )
        # Add again
        response = self.client.post(
            "/api/document/doc-tag-3/tags",
            json={"tag_type": "property", "property_id": prop.id},
            headers=AUTH,
        )
        assert response.status_code == 200
        assert response.json()["existed"] is True

    def test_add_tag_missing_type(self):
        _make_doc(self.db, doc_id="doc-tag-4")
        response = self.client.post(
            "/api/document/doc-tag-4/tags",
            json={"property_id": "prop-z"},
            headers=AUTH,
        )
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# DELETE /document-tag/{tag_id}
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("db")
class TestDeleteDocumentTag(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        app.dependency_overrides[get_db] = lambda: self.db

    def tearDown(self):
        app.dependency_overrides = {}

    def test_not_found(self):
        response = self.client.delete("/api/document-tag/nonexistent", headers=AUTH)
        assert response.status_code == 404

    def test_deletes_tag(self):
        prop = _make_property(self.db, "prop-dtag-1")
        _make_doc(self.db, doc_id="doc-dtag-1")
        tag = DocumentTag(
            id="tag-001",
            document_id="doc-dtag-1",
            tag_type="property",
            property_id=prop.id,
        )
        self.db.add(tag)
        self.db.flush()

        response = self.client.delete("/api/document-tag/tag-001", headers=AUTH)
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        assert self.db.query(DocumentTag).filter_by(id="tag-001").one_or_none() is None


# ---------------------------------------------------------------------------
# POST /upload-document
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("db")
class TestUploadDocument(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        app.dependency_overrides[get_db] = lambda: self.db

    def tearDown(self):
        app.dependency_overrides = {}

    def test_requires_auth(self):
        response = self.client.post(
            "/api/upload-document",
            files={"file": ("test.pdf", b"content", "application/pdf")},
        )
        assert response.status_code == 401

    def test_uploads_new_document(self):
        with (
            patch("backends.wire.storage_backend.upload", new_callable=AsyncMock),
            patch("llm.document_processor.process_document"),
        ):
            response = self.client.post(
                "/api/upload-document",
                files={"file": ("test.pdf", b"unique-content-123", "application/pdf")},
                data={"document_type": "lease"},
                headers=AUTH,
            )
        assert response.status_code == 200
        assert "document_id" in response.json()
        assert response.json().get("duplicate") is None

    def test_returns_duplicate_on_same_checksum(self):
        file_bytes = b"duplicate-content"
        import hashlib
        checksum = hashlib.sha256(file_bytes).hexdigest()
        existing_doc = _make_doc(self.db, doc_id="doc-dup-1")
        existing_doc.sha256_checksum = checksum
        self.db.flush()

        response = self.client.post(
            "/api/upload-document",
            files={"file": ("test.pdf", file_bytes, "application/pdf")},
            headers=AUTH,
        )
        assert response.status_code == 200
        data = response.json()
        assert data.get("duplicate") is True
        assert data["document_id"] == "doc-dup-1"
