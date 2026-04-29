"""Tests for ``RememberAboutEntityTool`` — the heuristic gate, dedup,
and persistence to entity.context / EntityNote."""
from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from unittest.mock import patch

from db.models import EntityNote, Property, Tenant, User
from llm.tools import RememberAboutEntityTool


@contextmanager
def _bind_session(db, *, account_id: int = 1):
    with patch("rentmate.app.SessionLocal.session_factory", return_value=db), \
         patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch("llm.tools.memory.resolve_account_id", return_value=account_id), \
         patch.object(db, "close", lambda: None):
        yield


def _run(tool, **kwargs):
    return json.loads(asyncio.run(tool.execute(**kwargs)))


def _seed_property(db, address="100 Gate St"):
    prop = Property(
        org_id=1, creator_id=1,
        address_line1=address, property_type="single_family", source="manual",
    )
    db.add(prop)
    db.flush()
    return prop


def _seed_tenant(db):
    user = User(
        org_id=1, creator_id=1, user_type="tenant",
        first_name="Marcus", last_name="Johnson",
        email="marcus@example.com", phone="+15550001111", active=True,
    )
    db.add(user)
    db.flush()
    tenant = Tenant(org_id=1, creator_id=1, user_id=user.id)
    db.add(tenant)
    db.flush()
    return tenant


# ─── Happy path ────────────────────────────────────────────────────────


def test_save_preference_appends_to_entity_context_with_kind_stamp(db):
    prop = _seed_property(db)
    db.commit()
    with _bind_session(db):
        result = _run(
            RememberAboutEntityTool(),
            entity_type="property",
            entity_id=prop.id,
            note_kind="preference",
            content="Owner prefers weekend vendor visits; weekday access is not authorized.",
        )
    assert result["status"] == "ok", result
    db.refresh(prop)
    # Stamped with date + kind so retrieval can render it as a typed note.
    assert "(preference)" in prop.context
    assert "weekend vendor visits" in prop.context
    assert result["applied_summary"]


def test_visibility_private_writes_to_entity_note_not_entity_context(db):
    prop = _seed_property(db)
    db.commit()
    with _bind_session(db):
        result = _run(
            RememberAboutEntityTool(),
            entity_type="property",
            entity_id=prop.id,
            note_kind="quirk",
            content="Garage door lock sticks in winter; a quick spritz of silicone fixes it.",
            visibility="private",
        )
    assert result["status"] == "ok"
    db.refresh(prop)
    assert prop.context in (None, "")  # shared context untouched
    note = db.query(EntityNote).filter_by(
        entity_type="property", entity_id=prop.id,
    ).one()
    assert "silicone" in note.content


# ─── Length bounds ─────────────────────────────────────────────────────


def test_too_short_after_strip_is_rejected(db):
    prop = _seed_property(db)
    db.commit()
    with _bind_session(db):
        result = _run(
            RememberAboutEntityTool(),
            entity_type="property",
            entity_id=prop.id,
            note_kind="preference",
            content="Owner ok",
        )
    assert result["status"] == "error"
    assert "too short" in result["message"].lower()


def test_too_long_is_rejected(db):
    prop = _seed_property(db)
    db.commit()
    with _bind_session(db):
        result = _run(
            RememberAboutEntityTool(),
            entity_type="property",
            entity_id=prop.id,
            note_kind="preference",
            content="A. " + ("Owner prefers weekend vendor visits. " * 30),
        )
    assert result["status"] == "error"
    assert "too long" in result["message"].lower()


# ─── PII strip ─────────────────────────────────────────────────────────


def test_pii_stripped_then_persisted_without_email_phone_or_uuid(db):
    prop = _seed_property(db)
    db.commit()
    with _bind_session(db):
        result = _run(
            RememberAboutEntityTool(),
            entity_type="property",
            entity_id=prop.id,
            note_kind="preference",
            content=(
                "Owner prefers weekend visits; reach out at owner@example.com or "
                "+1 (555) 555-1234. Reference 11111111-2222-3333-4444-555555555555."
            ),
        )
    assert result["status"] == "ok"
    db.refresh(prop)
    blob = (prop.context or "")
    assert "@example.com" not in blob
    assert "555-1234" not in blob
    assert "11111111-2222-3333" not in blob
    # Core durable fact still survived the strip.
    assert "weekend" in blob.lower()


def test_content_that_was_only_pii_gets_rejected(db):
    prop = _seed_property(db)
    db.commit()
    with _bind_session(db):
        result = _run(
            RememberAboutEntityTool(),
            entity_type="property",
            entity_id=prop.id,
            note_kind="preference",
            content="owner@example.com  +15555551234  11111111-2222-3333-4444-555555555555",
        )
    # All-PII content collapses below length floor after strip.
    assert result["status"] == "error"
    assert "too short" in result["message"].lower() or "entirely IDs" in result["message"]


# ─── Operational/transient phrasing reject ─────────────────────────────


def test_operational_phrasing_rejected_with_pointer_to_add_task_note(db):
    prop = _seed_property(db)
    db.commit()
    with _bind_session(db):
        result = _run(
            RememberAboutEntityTool(),
            entity_type="property",
            entity_id=prop.id,
            note_kind="quirk",
            content="I am about to call the plumber tomorrow morning to fix the leak.",
        )
    assert result["status"] == "error"
    assert "operational" in result["message"].lower()
    assert "add_task_note" in result["message"]


# ─── note_kind shape gates ─────────────────────────────────────────────


def test_pattern_without_frequency_anchor_is_rejected(db):
    prop = _seed_property(db)
    db.commit()
    with _bind_session(db):
        result = _run(
            RememberAboutEntityTool(),
            entity_type="property",
            entity_id=prop.id,
            note_kind="pattern",
            content="The HVAC system has been giving us trouble across multiple service calls.",
        )
    assert result["status"] == "error"
    assert "frequency anchor" in result["message"]


def test_pattern_with_frequency_anchor_passes(db):
    prop = _seed_property(db)
    db.commit()
    with _bind_session(db):
        result = _run(
            RememberAboutEntityTool(),
            entity_type="property",
            entity_id=prop.id,
            note_kind="pattern",
            content="Third HVAC service call in 18 months; consider replacement next budget cycle.",
        )
    assert result["status"] == "ok"


def test_compliance_without_citation_is_rejected(db):
    prop = _seed_property(db)
    db.commit()
    with _bind_session(db):
        result = _run(
            RememberAboutEntityTool(),
            entity_type="property",
            entity_id=prop.id,
            note_kind="compliance",
            content="The unit has rules about how landlords interact with tenants on this property.",
        )
    assert result["status"] == "error"
    assert "citation" in result["message"].lower() or "regulatory" in result["message"].lower()


def test_compliance_with_citation_passes(db):
    prop = _seed_property(db)
    db.commit()
    with _bind_session(db):
        result = _run(
            RememberAboutEntityTool(),
            entity_type="property",
            entity_id=prop.id,
            note_kind="compliance",
            content="Unit is rent-controlled under SF Rent Ordinance §37.3; max annual increase 1.7%.",
        )
    assert result["status"] == "ok"


# ─── Dedup ─────────────────────────────────────────────────────────────


def test_near_duplicate_is_rejected_with_edit_memory_pointer(db):
    prop = _seed_property(db)
    db.commit()
    with _bind_session(db):
        first = _run(
            RememberAboutEntityTool(),
            entity_type="property",
            entity_id=prop.id,
            note_kind="preference",
            content="Owner prefers weekend vendor visits; weekday access is not authorized.",
        )
        second = _run(
            RememberAboutEntityTool(),
            entity_type="property",
            entity_id=prop.id,
            note_kind="preference",
            content="Owner prefers weekend visits and does not authorize weekday access.",
        )
    assert first["status"] == "ok"
    assert second["status"] == "error"
    assert "edit_memory" in second["message"]


# ─── ID hygiene ────────────────────────────────────────────────────────


def test_internal_pk_as_entity_id_is_rejected(db):
    """Pre-fix bug: tests passed ``entity_id=tenant.id`` (the integer
    pk) and the tool stored that pk in EntityNote, breaking retrieval
    (which matches by external_id). The new tool resolves entity_id
    via ``_load_entity_by_public_id``, which filters by external_id on
    models that have one (Tenant/User), so internal integer pks fail
    with a clear "not found"."""
    tenant = _seed_tenant(db)
    db.commit()
    with _bind_session(db):
        result = _run(
            RememberAboutEntityTool(),
            entity_type="tenant",
            entity_id=str(tenant.id),  # internal integer pk; should fail
            note_kind="preference",
            content="Prefers SMS for non-urgent contact; calls only for emergencies.",
        )
    assert result["status"] == "error"
    assert "not found" in result["message"]


def test_placeholder_entity_id_is_rejected_before_db_lookup(db):
    db.commit()
    with _bind_session(db):
        result = _run(
            RememberAboutEntityTool(),
            entity_type="property",
            entity_id="property_id_from_context",
            note_kind="preference",
            content="Owner prefers weekend vendor visits; weekday access is not authorized.",
        )
    assert result["status"] == "error"
    assert "placeholder" in result["message"].lower()


def test_tenant_entity_works_alongside_property(db):
    tenant = _seed_tenant(db)
    db.commit()
    with _bind_session(db):
        result = _run(
            RememberAboutEntityTool(),
            entity_type="tenant",
            entity_id=tenant.external_id,
            note_kind="preference",
            content="Prefers SMS for non-urgent contact; calls only for emergencies.",
        )
    assert result["status"] == "ok", result
    db.refresh(tenant)
    assert "SMS" in (tenant.context or "")
