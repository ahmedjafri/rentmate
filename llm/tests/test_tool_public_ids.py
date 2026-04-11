import asyncio
import json
from unittest.mock import patch

from db.models import EntityNote, Suggestion, Task, Tenant, User
from llm.tools import (
    CreatePropertyTool,
    CreateTenantTool,
    CreateVendorTool,
    LookupVendorsTool,
    MessageExternalPersonTool,
    ProposeTaskTool,
    RecallMemoryTool,
    SaveMemoryTool,
    active_conversation_id,
    pending_suggestion_messages,
)


def _run_tool(tool, **kwargs):
    return asyncio.run(tool.execute(**kwargs))


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
         patch("llm.tools.resolve_account_id", return_value=property_owner.id):
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
         patch("llm.tools.resolve_account_id", return_value=property_owner.id):
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
