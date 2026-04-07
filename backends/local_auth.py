import os
from datetime import UTC, datetime, timedelta

import jwt

JWT_SECRET = os.getenv("JWT_SECRET", "rentmate-local-secret")
RENTMATE_PASSWORD = os.getenv("RENTMATE_PASSWORD", "rentmate")

DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"
DEFAULT_ACCOUNT_ID = DEFAULT_USER_ID  # In local auth, user == account
DEFAULT_USER_EMAIL = os.getenv("RENTMATE_ADMIN_EMAIL", "admin@localhost")


def resolve_account_id(user: dict | None = None) -> str:
    """Resolve the account_id from a user token payload.

    For local auth this always returns DEFAULT_ACCOUNT_ID.
    In production, this would look up the AccountUser table.
    """
    return DEFAULT_ACCOUNT_ID


class LocalAuthBackend:
    async def validate_token(self, token: str) -> dict:
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            uid = payload.get("sub", DEFAULT_USER_ID)
            email = payload.get("email", DEFAULT_USER_EMAIL)
            return {
                "uid": uid,
                "id": uid,
                "email": email,
                "username": email,
            }
        except Exception as e:
            raise ValueError(f"Invalid token: {e}")

    async def login(self, **credentials) -> str:
        password = credentials.get("password", "")
        if password != RENTMATE_PASSWORD:
            raise ValueError("Invalid password")
        payload = {
            "sub": DEFAULT_USER_ID,
            "email": DEFAULT_USER_EMAIL,
            "exp": datetime.now(UTC) + timedelta(days=30),
        }
        return jwt.encode(payload, JWT_SECRET, algorithm="HS256")
