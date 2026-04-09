import os
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta

import jwt

JWT_SECRET = os.getenv("JWT_SECRET", "rentmate-local-secret")
RENTMATE_PASSWORD = os.getenv("RENTMATE_PASSWORD", "rentmate")

DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"
DEFAULT_ACCOUNT_ID = DEFAULT_USER_ID  # Seed account for first-run
DEFAULT_USER_EMAIL = os.getenv("RENTMATE_ADMIN_EMAIL", "admin@localhost")

# Request-scoped account context — set by middleware/deps, read by tools
_current_account_id: ContextVar[str] = ContextVar("current_account_id", default=DEFAULT_ACCOUNT_ID)
_current_user_id: ContextVar[str] = ContextVar("current_user_id", default=DEFAULT_USER_ID)


def resolve_account_id(user: dict | None = None) -> str:
    """Resolve the account_id for the current request.

    Checks (in order):
    1. Request-scoped context var (set by require_user)
    2. User dict from token payload → DB lookup
    3. DEFAULT_ACCOUNT_ID fallback
    """
    # Fast path: context var already set by middleware
    ctx = _current_account_id.get(DEFAULT_ACCOUNT_ID)
    if ctx != DEFAULT_ACCOUNT_ID:
        return ctx

    if not user:
        return DEFAULT_ACCOUNT_ID

    # Look up account from user_id via AccountUser table
    user_id = user.get("uid") or user.get("id") or user.get("sub")
    if not user_id:
        return DEFAULT_ACCOUNT_ID

    try:
        from db.models import AccountUser
        from db.session import SessionLocal
        db = SessionLocal()
        try:
            au = db.query(AccountUser).filter_by(user_id=user_id).first()
            if au:
                return au.account_id
        finally:
            db.close()
    except Exception:
        pass

    return DEFAULT_ACCOUNT_ID


def set_request_context(*, user_id: str, account_id: str) -> tuple:
    """Set request-scoped context vars. Returns tokens for reset."""
    t1 = _current_user_id.set(user_id)
    t2 = _current_account_id.set(account_id)
    return t1, t2


def reset_request_context(tokens: tuple) -> None:
    """Reset context vars after request completes."""
    _current_user_id.reset(tokens[0])
    _current_account_id.reset(tokens[1])


class LocalAuthBackend:
    async def validate_token(self, token: str) -> dict:
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            uid = payload.get("sub", DEFAULT_USER_ID)
            email = payload.get("email", DEFAULT_USER_EMAIL)
            account_id = resolve_account_id({"uid": uid})
            return {
                "uid": uid,
                "id": uid,
                "email": email,
                "username": email,
                "account_id": account_id,
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
