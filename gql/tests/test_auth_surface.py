import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from gql.schema import schema


def _context(db, user=None):
    return {"db_session": db, "user": user}


def test_me_returns_external_uid_not_internal_id(db):
    result = schema.execute_sync(
        """
        query {
          me {
            uid
            username
          }
        }
        """,
        context_value=_context(
            db,
            user={
                "id": 1,
                "uid": "user-external-123",
                "email": "admin@example.com",
                "username": "admin@example.com",
            },
        ),
    )

    assert result.errors is None
    assert result.data == {
        "me": {
            "uid": "user-external-123",
            "username": "admin@example.com",
        }
    }


def test_login_mutation_returns_external_uid_only():
    fake_user = SimpleNamespace(id=1, external_id="user-external-123", email="admin@example.com")

    with patch("gql.auth_mutations.auth_backend.login", new=AsyncMock(return_value=("jwt-token", fake_user))):
        result = asyncio.run(
            schema.execute(
                """
                mutation {
                  login(input: { password: "pw", email: "admin@example.com" }) {
                    token
                    user {
                      uid
                      username
                    }
                  }
                }
                """
            )
        )

    assert result.errors is None
    assert result.data == {
        "login": {
            "token": "jwt-token",
            "user": {
                "uid": "user-external-123",
                "username": "admin@example.com",
            },
        }
    }
