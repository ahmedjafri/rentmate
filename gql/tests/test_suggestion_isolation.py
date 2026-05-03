from db.models import Suggestion, User
from gql.schema import schema
from services.number_allocator import NumberAllocator


def _context(db, user=None):
    return {
        "db_session": db,
        "user": user or {"id": 1, "uid": "user-external-123", "email": "admin@example.com"},
    }


def test_suggestions_query_excludes_other_org_rows(db):
    visible_id = NumberAllocator.allocate_next(db, entity_type="suggestion", org_id=1)
    foreign_id = NumberAllocator.allocate_next(db, entity_type="suggestion", org_id=2)
    visible = Suggestion(id=visible_id, org_id=1, creator_id=1, title="Visible", status="pending")
    foreign_user = User(id=2, org_id=2, email="org2-admin@example.com", active=True)
    foreign = Suggestion(id=foreign_id, org_id=2, creator_id=2, title="Hidden", status="pending")
    db.add_all([visible, foreign_user, foreign])
    db.flush()

    result = schema.execute_sync(
        """
        query {
          suggestions {
            uid
            title
          }
        }
        """,
        context_value=_context(db),
    )

    assert result.errors is None
    assert result.data == {
        "suggestions": [
            {"uid": visible.id, "title": "Visible"},
        ]
    }


def test_act_on_suggestion_rejects_other_org_row(db):
    foreign_id = NumberAllocator.allocate_next(db, entity_type="suggestion", org_id=2)
    foreign_user = User(id=2, org_id=2, email="org2-admin@example.com", active=True)
    foreign = Suggestion(id=foreign_id, org_id=2, creator_id=2, title="Hidden", status="pending")
    db.add_all([foreign_user, foreign])
    db.flush()

    result = schema.execute_sync(
        f"""
        mutation {{
          actOnSuggestion(uid: {foreign.id}, action: "reject_task") {{
            uid
            status
          }}
        }}
        """,
        context_value=_context(db),
    )

    assert result.data is None
    assert result.errors is not None
    assert f"Suggestion {foreign.id} not found" in str(result.errors[0])
