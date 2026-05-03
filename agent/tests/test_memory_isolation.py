from unittest.mock import MagicMock, patch

from agent.memory_store import DbMemoryStore
from db.models import AgentMemory, Property, Tenant, Unit, User
from integrations.local_auth import reset_request_context, set_request_context


def test_memory_context_excludes_other_org_entity_context_and_notes(db):
    foreign_creator = User(id=200, org_id=2, email="org2-admin@example.com", active=True)
    local_tenant_user = User(
        id=2,
        org_id=1,
        creator_id=1,
        user_type="tenant",
        first_name="Local",
        last_name="Tenant",
        active=True,
    )
    foreign_tenant_user = User(
        id=201,
        org_id=2,
        creator_id=200,
        user_type="tenant",
        first_name="Foreign",
        last_name="Tenant",
        active=True,
    )
    local_vendor = User(
        id=3,
        org_id=1,
        creator_id=1,
        user_type="vendor",
        first_name="Local",
        last_name="Vendor",
        context="local vendor context",
        active=True,
    )
    foreign_vendor = User(
        id=202,
        org_id=2,
        creator_id=200,
        user_type="vendor",
        first_name="Foreign",
        last_name="Vendor",
        context="foreign vendor context",
        active=True,
    )
    db.add_all([foreign_creator, local_tenant_user, foreign_tenant_user, local_vendor, foreign_vendor])
    db.flush()

    local_property = Property(
        id="prop-local",
        org_id=1,
        creator_id=1,
        address_line1="1 Local St",
        property_type="multi_family",
        source="manual",
        context="local property context",
    )
    foreign_property = Property(
        id="prop-foreign",
        org_id=2,
        creator_id=200,
        address_line1="2 Foreign St",
        property_type="multi_family",
        source="manual",
        context="foreign property context",
    )
    local_unit = Unit(
        id="unit-local",
        org_id=1,
        creator_id=1,
        property_id=local_property.id,
        label="1A",
        context="local unit context",
    )
    foreign_unit = Unit(
        id="unit-foreign",
        org_id=2,
        creator_id=200,
        property_id=foreign_property.id,
        label="2B",
        context="foreign unit context",
    )
    local_tenant = Tenant(
        org_id=1,
        creator_id=1,
        user_id=local_tenant_user.id,
        context="local tenant context",
    )
    foreign_tenant = Tenant(
        org_id=2,
        creator_id=200,
        user_id=foreign_tenant_user.id,
        context="foreign tenant context",
    )
    db.add_all([local_property, foreign_property, local_unit, foreign_unit, local_tenant, foreign_tenant])
    db.flush()

    db.add_all([
        AgentMemory(
            id="mem-local",
            org_id=1,
            creator_id=1,
            memory_type="note:general",
            content="local memory note",
        ),
        AgentMemory(
            id="mem-foreign",
            org_id=2,
            creator_id=200,
            memory_type="note:general",
            content="foreign memory note",
        ),
    ])
    db.commit()

    token = set_request_context(account_id=1, org_id=1)
    try:
        mock_sl = MagicMock()
        mock_sl.session_factory.return_value = db
        with patch("db.session.SessionLocal", mock_sl):
            store = DbMemoryStore("1")
            context = store.get_memory_context()
            notes = store.get_notes()
    finally:
        reset_request_context(token)

    assert "local property context" in context
    assert "local unit context" in context
    assert "local tenant context" in context
    assert "local vendor context" in context
    assert "local memory note" in context

    assert "foreign property context" not in context
    assert "foreign unit context" not in context
    assert "foreign tenant context" not in context
    assert "foreign vendor context" not in context
    assert "foreign memory note" not in context
    assert notes == [{"content": "local memory note", "entity_type": "general"}]
