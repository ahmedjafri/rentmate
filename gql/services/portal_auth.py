"""Shared portal authentication for vendor and tenant portals.

Provides token generation, JWT creation/validation, and portal URL building
used by both VendorService and TenantService.
"""
import os
import secrets
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified


_JWT_SECRET = os.getenv("JWT_SECRET", "rentmate-local-secret")
_JWT_ALGORITHM = "HS256"


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


def ensure_portal_token(entity) -> str:
    """Ensure an entity has a portal_token in its extra JSON field."""
    extra = dict(entity.extra or {})
    if not extra.get("portal_token"):
        extra["portal_token"] = generate_portal_token()
        entity.extra = extra
        flag_modified(entity, "extra")
    return extra["portal_token"]


def find_by_portal_token(db: Session, model_class, token: str):
    """Find an entity by portal_token (or legacy invite_token) in its extra JSON."""
    entities = db.execute(select(model_class)).scalars().all()
    for e in entities:
        extra = e.extra or {}
        if extra.get("portal_token") == token or extra.get("invite_token") == token:
            return e
    return None
