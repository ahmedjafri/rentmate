from db.models import Property, Tenant, Unit, User
from gql.schema import schema


def _context(db, user=None):
    return {
        "db_session": db,
        "user": user or {"id": 1, "uid": "user-external-123", "email": "admin@example.com"},
    }


def _seed_entities(db):
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
    db.add_all([foreign_creator, local_tenant_user, foreign_tenant_user])
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
    db.add_all([local_property, foreign_property])
    db.flush()

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
    db.add_all([local_unit, foreign_unit])
    db.flush()

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
    db.add_all([local_tenant, foreign_tenant])
    db.flush()

    return {
        "local_property": local_property,
        "foreign_property": foreign_property,
        "local_unit": local_unit,
        "foreign_unit": foreign_unit,
        "local_tenant": local_tenant,
        "foreign_tenant": foreign_tenant,
    }


def test_property_unit_and_tenant_context_queries_exclude_other_org(db):
    entities = _seed_entities(db)

    result = schema.execute_sync(
        """
        query {
          houses {
            uid
            context
            unitList {
              uid
              context
            }
          }
          tenants {
            uid
            context
          }
        }
        """,
        context_value=_context(db),
    )

    assert result.errors is None, result.errors
    assert result.data == {
        "houses": [
            {
                "uid": entities["local_property"].id,
                "context": "local property context",
                "unitList": [
                    {"uid": entities["local_unit"].id, "context": "local unit context"},
                ],
            }
        ],
        "tenants": [
            {
                "uid": entities["local_tenant"].external_id,
                "context": "local tenant context",
            }
        ],
    }


def test_update_entity_context_rejects_foreign_property(db):
    entities = _seed_entities(db)

    result = schema.execute_sync(
        f"""
        mutation {{
          updateEntityContext(
            entityType: "property"
            entityId: "{entities['foreign_property'].id}"
            context: "attempted overwrite"
          )
        }}
        """,
        context_value=_context(db),
    )

    assert result.data is None
    assert result.errors is not None
    assert "property prop-foreign not found" in str(result.errors[0])
    assert db.get(Property, entities["foreign_property"].id).context == "foreign property context"


def test_update_entity_context_rejects_foreign_unit(db):
    entities = _seed_entities(db)

    result = schema.execute_sync(
        f"""
        mutation {{
          updateEntityContext(
            entityType: "unit"
            entityId: "{entities['foreign_unit'].id}"
            context: "attempted overwrite"
          )
        }}
        """,
        context_value=_context(db),
    )

    assert result.data is None
    assert result.errors is not None
    assert "unit unit-foreign not found" in str(result.errors[0])
    assert db.get(Unit, entities["foreign_unit"].id).context == "foreign unit context"


def test_update_entity_context_rejects_foreign_tenant(db):
    entities = _seed_entities(db)

    result = schema.execute_sync(
        f"""
        mutation {{
          updateEntityContext(
            entityType: "tenant"
            entityId: "{entities['foreign_tenant'].external_id}"
            context: "attempted overwrite"
          )
        }}
        """,
        context_value=_context(db),
    )

    assert result.data is None
    assert result.errors is not None
    assert f"tenant {entities['foreign_tenant'].external_id} not found" in str(result.errors[0])
    assert db.get(Tenant, entities["foreign_tenant"].id).context == "foreign tenant context"
