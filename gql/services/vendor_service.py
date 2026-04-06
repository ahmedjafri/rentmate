import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Optional, Tuple

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import ExternalContact
from gql.types import CreateVendorInput, UpdateVendorInput, VENDOR_TYPES

_JWT_SECRET = os.getenv("JWT_SECRET", "rentmate-local-secret")
_JWT_ALGORITHM = "HS256"


def _validate_vendor_type(vendor_type: str | None) -> None:
    if vendor_type is not None and vendor_type not in VENDOR_TYPES:
        raise ValueError(f"Invalid vendor type '{vendor_type}'. Must be one of: {', '.join(VENDOR_TYPES)}")


class VendorService:
    @staticmethod
    def create_vendor(sess: Session, input: CreateVendorInput) -> ExternalContact:
        if not input.vendor_type:
            raise ValueError("Vendor type is required")
        _validate_vendor_type(input.vendor_type)
        if not input.phone:
            raise ValueError("Phone number is required")
        extra: dict = {"portal_token": secrets.token_urlsafe(6)}
        vendor = ExternalContact(
            id=str(uuid.uuid4()),
            name=input.name,
            company=input.company,
            role_label=input.vendor_type,
            phone=input.phone,
            email=input.email,
            notes=input.notes,
            extra=extra,
            created_at=datetime.now(UTC),
        )
        sess.add(vendor)
        sess.commit()
        sess.refresh(vendor)
        return vendor

    @staticmethod
    def update_vendor(sess: Session, input: UpdateVendorInput) -> ExternalContact:
        vendor = sess.execute(
            select(ExternalContact).where(ExternalContact.id == input.uid)
        ).scalar_one_or_none()
        if not vendor:
            raise ValueError(f"Vendor {input.uid} not found")
        if input.name is not None:
            vendor.name = input.name
        if input.company is not None:
            vendor.company = input.company
        if input.vendor_type is not None:
            _validate_vendor_type(input.vendor_type)
            vendor.role_label = input.vendor_type
        if input.phone is not None:
            vendor.phone = input.phone
        if input.email is not None:
            vendor.email = input.email
        if input.notes is not None:
            vendor.notes = input.notes
        sess.commit()
        return vendor

    @staticmethod
    def delete_vendor(sess: Session, uid: str) -> bool:
        vendor = sess.execute(
            select(ExternalContact).where(ExternalContact.id == uid)
        ).scalar_one_or_none()
        if not vendor:
            raise ValueError(f"Vendor {uid} not found")
        sess.delete(vendor)
        sess.commit()
        return True

    # ── Portal token auth ───────────────────────────────────────────────────

    @staticmethod
    def _find_by_portal_token(sess: Session, token: str) -> Optional[ExternalContact]:
        """Find vendor by portal_token, with fallback to legacy invite_token."""
        vendors = sess.execute(select(ExternalContact)).scalars().all()
        for v in vendors:
            extra = v.extra or {}
            if extra.get("portal_token") == token or extra.get("invite_token") == token:
                return v
        return None

    @staticmethod
    def authenticate_by_token(sess: Session, token: str) -> Tuple[ExternalContact, str]:
        """Look up vendor by portal token and return vendor + JWT. No accept step."""
        vendor = VendorService._find_by_portal_token(sess, token)
        if not vendor:
            raise ValueError("Invalid portal link")
        jwt_token = VendorService._create_vendor_jwt(vendor)
        return vendor, jwt_token

    @staticmethod
    def get_portal_url(vendor: ExternalContact) -> str:
        """Build the public-facing short URL for the vendor's chat portal."""
        token = (vendor.extra or {}).get("portal_token") or (vendor.extra or {}).get("invite_token")
        if not token:
            return ""
        public_url = os.environ.get("RENTMATE_PUBLIC_URL", "").rstrip("/")
        if public_url:
            return f"{public_url}/t/{token}"
        port = os.environ.get("RENTMATE_PORT", "8000")
        return f"http://localhost:{port}/t/{token}"

    @staticmethod
    def validate_vendor_token(token: str) -> dict:
        payload = jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])
        if payload.get("type") != "vendor":
            raise ValueError("Not a vendor token")
        return payload

    @staticmethod
    def _create_vendor_jwt(vendor: ExternalContact) -> str:
        payload = {
            "type": "vendor",
            "vendor_id": str(vendor.id),
            "exp": datetime.now(UTC) + timedelta(days=365),
        }
        return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)

    @staticmethod
    def ensure_portal_token(sess: Session, vendor: ExternalContact) -> str:
        """Ensure vendor has a portal_token, migrating from invite_token if needed."""
        extra = dict(vendor.extra or {})
        if not extra.get("portal_token"):
            extra["portal_token"] = secrets.token_urlsafe(6)
            vendor.extra = extra
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(vendor, "extra")
        return extra["portal_token"]
