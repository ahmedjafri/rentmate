import os
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from db.models.base import DEFAULT_ORG_ID

JWT_SECRET = os.getenv("JWT_SECRET", "rentmate-local-secret")
DEFAULT_ORG_EXTERNAL_ID = os.getenv(
    "RENTMATE_ORG_EXTERNAL_ID",
    str(uuid.uuid5(uuid.NAMESPACE_URL, "rentmate:self-hosted-org")),
)

# Request-scoped context — set by middleware/deps, read by tools/queries
_current_account_id: ContextVar[int | None] = ContextVar("current_account_id", default=None)
_current_org_id: ContextVar[int | None] = ContextVar("current_org_id", default=None)
_fallback_account_id: int | None = None
_fallback_org_id: int | None = None


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _check_password(password: str, *, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def resolve_account_id() -> int:
    """Return the user id for the current request.

    Reads from the request-scoped context var set by set_request_context().
    Raises if no context has been set — every request must authenticate.
    """
    ctx = _current_account_id.get(None)
    if ctx is None:
        if _fallback_account_id is not None:
            return _fallback_account_id
        raise RuntimeError("No account context set — did the request go through authentication?")
    return ctx


def resolve_org_id() -> int:
    """Return the org_id for the current request."""
    ctx = _current_org_id.get(None)
    if ctx is None:
        if _fallback_org_id is not None:
            return _fallback_org_id
        raise RuntimeError("No org context set — did the request go through authentication?")
    return ctx


def resolve_creator_id() -> int:
    """Backward-compatible alias for the current authenticated creator/user id."""
    return resolve_account_id()


def _lookup_account_id() -> int:
    """Backward-compatible helper used by older code paths."""
    return resolve_account_id()


def get_org_external_id(*, org_id: int | None = None) -> str:
    """Return the stable external org UUID for a local/self-hosted deployment."""
    if org_id not in (None, DEFAULT_ORG_ID):
        raise ValueError(f"Unknown org_id {org_id}")
    return DEFAULT_ORG_EXTERNAL_ID


def lookup_org_id(org_uid: str | None) -> int:
    """Resolve an external org UUID to the internal integer org_id."""
    if not org_uid or org_uid == DEFAULT_ORG_EXTERNAL_ID:
        return DEFAULT_ORG_ID
    raise ValueError(f"Unknown org UID: {org_uid}")


def set_request_context(*, account_id: int, org_id: int | None = None) -> object:
    """Set request-scoped context. Returns token for reset."""
    resolved_org_id = org_id if org_id is not None else DEFAULT_ORG_ID
    t1 = _current_account_id.set(account_id)
    t2 = _current_org_id.set(resolved_org_id)
    return (t1, t2)


def reset_request_context(token: object) -> None:
    """Reset context after request completes."""
    if isinstance(token, tuple):
        _current_account_id.reset(token[0])
        _current_org_id.reset(token[1])
    else:
        # Legacy single-token reset
        _current_account_id.reset(token)


def set_fallback_request_context(*, account_id: int, org_id: int | None = None) -> tuple[int | None, int | None]:
    """Set process-wide fallback auth context for worker-thread tool execution."""
    global _fallback_account_id, _fallback_org_id
    prev = (_fallback_account_id, _fallback_org_id)
    _fallback_account_id = account_id
    _fallback_org_id = org_id if org_id is not None else DEFAULT_ORG_ID
    return prev


def reset_fallback_request_context(token: tuple[int | None, int | None]) -> None:
    """Restore the previous fallback auth context."""
    global _fallback_account_id, _fallback_org_id
    _fallback_account_id, _fallback_org_id = token


class LocalAuthBackend:
    async def validate_token(self, token: str, *, db=None) -> dict:
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            sub = payload.get("sub", "")
            org_uid = payload.get("org_uid") or payload.get("org_external_id")
            org_id = lookup_org_id(org_uid)

            from db.models import User
            if db is None:
                from db.session import SessionLocal
                db = SessionLocal()
                should_close = True
            else:
                should_close = False
            try:
                user = db.query(User).filter_by(external_id=sub, org_id=org_id).first()
                if user is None and str(sub).isdigit():
                    user = db.query(User).filter_by(id=int(sub), org_id=org_id).first()
                if not user:
                    raise ValueError("User not found")
                return {
                    "id": user.id,
                    "uid": user.external_id,
                    "sub": user.external_id,
                    "account_id": user.id,
                    "org_id": user.org_id,
                    "org_uid": get_org_external_id(org_id=user.org_id),
                    "email": user.email or "",
                    "username": user.email or "",
                }
            finally:
                if should_close:
                    db.close()
        except jwt.exceptions.PyJWTError as e:
            raise ValueError(f"Invalid token: {e}")

    async def login(self, **credentials) -> tuple[str, "User"]:
        """Validate password against DB-stored hash. Creates account on first sign-up."""
        password = credentials.get("password", "")
        email = credentials.get("email") or ""
        if not password:
            raise ValueError("Password is required")

        from db.models import User
        from db.session import SessionLocal

        db = SessionLocal()
        try:
            acct = db.query(User).filter_by(email=email).first() if email else None

            if acct:
                # Existing account — check password
                if not acct.password_hash:
                    # Legacy account without a password hash — set it now
                    acct.password_hash = _hash_password(password)
                    db.commit()
                elif not _check_password(password, hashed=acct.password_hash):
                    raise ValueError("Invalid email or password")
            else:
                # No account with this email — create one (sign-up)
                if not email:
                    raise ValueError("Email is required to create an account")
                acct = User(email=email, password_hash=_hash_password(password))
                db.add(acct)
                db.flush()
                db.commit()

            db.refresh(acct)
            payload = {
                "sub": acct.external_id,
                "uid": acct.external_id,
                "org_uid": get_org_external_id(org_id=acct.org_id),
                "email": acct.email or "",
                "exp": datetime.now(UTC) + timedelta(days=30),
            }
            token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
            return token, acct
        finally:
            db.close()
