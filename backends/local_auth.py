import os
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

JWT_SECRET = os.getenv("JWT_SECRET", "rentmate-local-secret")

# Request-scoped account context — set by middleware/deps, read by tools/queries
_current_account_id: ContextVar[int | None] = ContextVar("current_account_id", default=None)


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _check_password(password: str, *, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


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
            email = payload.get("email", "")
            account_id = int(uid) if str(uid).isdigit() else _lookup_account_id()
            return {
                "uid": uid,
                "id": uid,
                "email": email,
                "username": email,
                "account_id": account_id,
            }
        except jwt.exceptions.PyJWTError as e:
            raise ValueError(f"Invalid token: {e}")

    async def login(self, **credentials) -> tuple[str, "Account"]:
        """Validate password against DB-stored hash. Creates account on first sign-up."""
        password = credentials.get("password", "")
        email = credentials.get("email") or ""
        if not password:
            raise ValueError("Password is required")

        from db.models import Account
        from db.session import SessionLocal
        db = SessionLocal()
        try:
            acct = db.query(Account).filter_by(email=email).first() if email else None

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
                acct = Account(email=email, password_hash=_hash_password(password))
                db.add(acct)
                db.flush()
                db.commit()

            db.refresh(acct)
            account_id = acct.id
            account_email = acct.email or ""
        finally:
            db.close()

        payload = {
            "sub": str(account_id),
            "email": account_email,
            "exp": datetime.now(UTC) + timedelta(days=30),
        }
        token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
        return token, acct
