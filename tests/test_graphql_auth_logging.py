import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from gql.schema import schema


def test_unauthenticated_graphql_logs_info_without_strawberry_error(caplog, db):
    caplog.set_level("INFO")

    result = schema.execute_sync(
        """
        query {
          conversations(conversationType: USER_AI, limit: 5) {
            uid
          }
        }
        """,
        context_value={"db_session": db, "user": None},
    )

    assert result.errors is not None
    assert result.errors[0].message == "Not authenticated"

    info_logs = [
        rec for rec in caplog.records
        if rec.name == "rentmate.auth" and rec.levelname == "INFO"
    ]
    assert any("GraphQL unauthenticated request: Not authenticated" in rec.getMessage() for rec in info_logs)
    assert not any(rec.name == "strawberry.execution" and rec.levelname == "ERROR" for rec in caplog.records)


def test_graphql_context_logs_invalid_token_at_info(caplog, db):
    from rentmate.app import get_context

    caplog.set_level("INFO")
    request = SimpleNamespace(
        headers={"Authorization": "Bearer bad-token"},
        state=SimpleNamespace(db_session=db),
    )

    with patch("backends.wire.auth_backend.validate_token", new=AsyncMock(side_effect=Exception("User not found"))):
        context = asyncio.run(get_context(request))

    assert context == {"user": None, "db_session": db}
    info_logs = [
        rec for rec in caplog.records
        if rec.name == "rentmate.gql" and rec.levelname == "INFO"
    ]
    assert any("Invalid token, error: User not found" in rec.getMessage() for rec in info_logs)
