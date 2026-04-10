import os
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta

import jwt

JWT_SECRET = os.getenv("JWT_SECRET", "rentmate-local-secret")
RENTMATE_PASSWORD = os.getenv("RENTMATE_PASSWORD", "rentmate")
DEFAULT_USER_EMAIL = os.getenv("RENTMATE_ADMIN_EMAIL", "admin@localhost")

# Request-scoped account context — set by middleware/deps, read by tools/queries
_current_account_id: ContextVar[int | None] = ContextVar("current_account_id", default=None)


def resolve_account_id() -> int:
    """Return the account_id for the current request.

    Reads from the request-scoped context var set by set_request_context().
    Raises if no context has been set — every request must authenticate.
    """
    ctx = _current_account_id.get(None)
    if ctx is None:
        raise RuntimeError("No account context set — did the request go through authentication?")
    return ctx


def _lookup_account_id() -> int:
    """Look up the first account's ID from the database."""
    from db.models import Account
    from db.session import SessionLocal
    db = SessionLocal()
    try:
        acct = db.query(Account).first()
        if not acct:
            raise RuntimeError("No account exists in the database")
        return acct.id
    finally:
        db.close()


def set_request_context(*, account_id: int) -> object:
    """Set request-scoped account context. Returns token for reset."""
    return _current_account_id.set(account_id)


def reset_request_context(token: object) -> None:
    """Reset account context after request completes."""
    _current_account_id.reset(token)


class LocalAuthBackend:
    async def validate_token(self, token: str) -> dict:
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            uid = payload.get("sub", "")
            email = payload.get("email", DEFAULT_USER_EMAIL)
            account_id = _lookup_account_id()
            return {
                "uid": uid,
                "id": uid,
                "email": email,
                "username": email,
                "account_id": account_id,
            }
        except jwt.exceptions.PyJWTError as e:
            raise ValueError(f"Invalid token: {e}")

    async def login(self, **credentials) -> str:
        password = credentials.get("password", "")
        if password != RENTMATE_PASSWORD:
            raise ValueError("Invalid password")
        account_id = _lookup_account_id()
        payload = {
            "sub": str(account_id),
            "email": DEFAULT_USER_EMAIL,
            "exp": datetime.now(UTC) + timedelta(days=30),
        }
        return jwt.encode(payload, JWT_SECRET, algorithm="HS256")
