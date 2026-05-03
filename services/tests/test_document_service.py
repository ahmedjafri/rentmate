import pytest

from db.models import Document, DocumentTag, Property
from gql.types import AddDocumentTagInput
from services.document_service import (
    DocumentService,
    dump_document_extraction_data,
    dump_document_extraction_meta,
)


def test_add_document_tag_and_confirm_document(db):
    prop = Property(
        id="prop-1",
        org_id=1,
        creator_id=1,
        address_line1="123 Main St",
    )
    doc = Document(
        id="doc-1",
        org_id=1,
        creator_id=1,
        filename="lease.pdf",
        document_type="lease",
        status="processed",
    )
    db.add_all([prop, doc])
    db.commit()

    tag = DocumentService.add_document_tag(
        db,
        AddDocumentTagInput(document_id=doc.id, tag_type="property", property_id="prop-1"),
    )

    assert isinstance(tag, DocumentTag)
    assert tag.org_id == 1
    assert tag.document_id == doc.id
    assert DocumentService.confirm_document(db, doc.id) is True

    db.refresh(doc)
    assert doc.confirmed_at is not None


def test_confirm_document_rejects_unknown_document(db):
    with pytest.raises(ValueError, match="Document missing not found"):
        DocumentService.confirm_document(db, "missing")


def test_document_json_payloads_are_typed():
    extraction = dump_document_extraction_data({
        "leases": [{"tenant_first_name": "Jane", "property_address": "123 Main St"}],
    })
    meta = dump_document_extraction_meta(page_count=2, raw_text_chars=100, task_id="task-1")

    assert extraction == {
        "leases": [{"tenant_first_name": "Jane", "property_address": "123 Main St"}],
    }
    assert meta == {"task_id": "task-1", "page_count": 2, "raw_text_chars": 100}
