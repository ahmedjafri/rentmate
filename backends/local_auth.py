import os
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta

import jwt

JWT_SECRET = os.getenv("JWT_SECRET", "rentmate-local-secret")
RENTMATE_PASSWORD = os.getenv("RENTMATE_PASSWORD", "rentmate")

DEFAULT_USER_ID = "1"  # Filesystem path for agent workspace (data/agent/1/)
DEFAULT_USER_EMAIL = os.getenv("RENTMATE_ADMIN_EMAIL", "admin@localhost")

# Request-scoped creator context — set by middleware/deps, read by tools
_current_creator_id: ContextVar[int | None] = ContextVar("current_creator_id", default=None)
_current_user_id: ContextVar[int | None] = ContextVar("current_user_id", default=None)


def resolve_creator_id() -> int:
    """Return the creator_id for the current request.

    Reads from the request-scoped context var set by set_request_context().
    Raises if no context has been set — every request must authenticate.
    """
    ctx = _current_creator_id.get(None)
    if ctx is None:
        raise RuntimeError("No creator context set — did the request go through authentication?")
    return ctx


# Backward compat alias
resolve_account_id = resolve_creator_id


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


def set_request_context(*, user_id: int, creator_id: int) -> tuple:
    """Set request-scoped context vars. Returns tokens for reset."""
    t1 = _current_user_id.set(user_id)
    t2 = _current_creator_id.set(creator_id)
    return t1, t2


def reset_request_context(tokens: tuple) -> None:
    """Reset context vars after request completes."""
    _current_user_id.reset(tokens[0])
    _current_creator_id.reset(tokens[1])


class LocalAuthBackend:
    async def validate_token(self, token: str) -> dict:
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            uid = payload.get("sub", DEFAULT_USER_ID)
            email = payload.get("email", DEFAULT_USER_EMAIL)
            # Look up the actual account from the DB
            account_id = _lookup_account_id()
            return {
                "uid": uid,
                "id": uid,
                "email": email,
                "username": email,
                "creator_id": account_id,
            }
        except jwt.exceptions.PyJWTError as e:
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
