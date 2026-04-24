"""Shared portal authentication for vendor and tenant portals.

Provides token generation, JWT creation/validation, and portal URL building
used by both VendorService and TenantService.
"""
import os
import secrets
from datetime import UTC, datetime, timedelta

import jwt
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from db.session import SessionLocal

_JWT_SECRET = os.getenv("JWT_SECRET", "rentmate-local-secret")
_JWT_ALGORITHM = "HS256"


class PortalEntityExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portal_token: str | None = None
    invite_token: str | None = None
    linked_user_id: int | None = None


def parse_portal_entity_extra(extra: dict | None) -> PortalEntityExtra:
    return PortalEntityExtra.model_validate(extra or {})


def dump_portal_entity_extra(extra: PortalEntityExtra) -> dict:
    return extra.model_dump(exclude_none=True)


def generate_portal_token() -> str:
    """Generate a short URL-safe token for portal access."""
    return secrets.token_urlsafe(6)


def build_portal_url(token: str) -> str:
    """Build the public-facing short URL for a portal token."""
    public_url = os.environ.get("RENTMATE_PUBLIC_URL", "").rstrip("/")
    if public_url:
        return f"{public_url}/t/{token}"
    port = os.environ.get("RENTMATE_PORT", "8000")
    return f"http://localhost:{port}/t/{token}"


def build_portal_conversation_url(token: str, conversation_external_id: str) -> str:
    """Build a login-less URL that deep-links to a specific conversation."""
    return f"{build_portal_url(token)}?conv={conversation_external_id}"


def create_portal_jwt(entity_type: str, entity_id: str) -> str:
    """Create a JWT for portal access (vendor or tenant)."""
    payload = {
        "type": entity_type,
        f"{entity_type}_id": entity_id,
        "exp": datetime.now(UTC) + timedelta(days=365),
    }
    return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)


def validate_portal_jwt(token: str, expected_type: str) -> dict:
    """Decode and validate a portal JWT, checking the entity type."""
    payload = jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])
    if payload.get("type") != expected_type:
        raise ValueError(f"Not a {expected_type} token")
    return payload


def ensure_portal_token(entity, db: "Session | None" = None) -> str:
    """Ensure an entity has a portal_token in its extra JSON field.

    If a new token is generated and ``db`` is provided, persists via a
    separate session to avoid breaking the caller's transaction.
    """
    extra = parse_portal_entity_extra(entity.extra)
    if not extra.portal_token:
        token = generate_portal_token()
        extra.portal_token = token
        entity.extra = dump_portal_entity_extra(extra)
        flag_modified(entity, "extra")
        # Persist in a separate session so we don't break the caller's
        if db is None:
            return extra.portal_token or ""
        try:
            write_db = SessionLocal.session_factory()
            try:
                model_cls = type(entity)
                write_db.execute(
                    update(model_cls)
                    .where(model_cls.id == entity.id)
                    .values(extra=dump_portal_entity_extra(extra))
                )
                write_db.commit()
            finally:
                write_db.close()
        except Exception:
            pass
    return extra.portal_token or ""


def find_by_portal_token(db: Session, *, model_class, token: str):
    """Find an entity by portal_token (or legacy invite_token) in its extra JSON."""
    entities = db.execute(select(model_class)).scalars().all()
    for e in entities:
        extra = parse_portal_entity_extra(e.extra)
        if extra.portal_token == token or extra.invite_token == token:
            return e
    return None
