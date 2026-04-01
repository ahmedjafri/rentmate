import hashlib
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Optional, Tuple

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import ExternalContact
from gql.types import CreateVendorInput, UpdateVendorInput, VENDOR_TYPES, VENDOR_CONTACT_METHODS

_JWT_SECRET = os.getenv("JWT_SECRET", "rentmate-local-secret")
_JWT_ALGORITHM = "HS256"


def _validate_vendor_type(vendor_type: str | None) -> None:
    if vendor_type is not None and vendor_type not in VENDOR_TYPES:
        raise ValueError(f"Invalid vendor type '{vendor_type}'. Must be one of: {', '.join(VENDOR_TYPES)}")


def _validate_contact_method(method: str | None) -> None:
    if method is not None and method not in VENDOR_CONTACT_METHODS:
        raise ValueError(f"Invalid contact method '{method}'. Must be one of: {', '.join(VENDOR_CONTACT_METHODS)}")


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return f"{salt}:{h.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    salt, expected = stored.split(":", 1)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return h.hex() == expected


class VendorService:
    @staticmethod
    def create_vendor(sess: Session, input: CreateVendorInput) -> ExternalContact:
        _validate_vendor_type(input.vendor_type)
        _validate_contact_method(input.contact_method)
        method = input.contact_method or "rentmate"
        extra: dict = {"contact_method": method}
        if method == "rentmate":
            extra["invite_token"] = secrets.token_urlsafe(32)
            extra["invite_status"] = "pending"
        else:
            extra["invite_status"] = "n/a"
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
        if input.contact_method is not None:
            _validate_contact_method(input.contact_method)
            vendor.extra = {**(vendor.extra or {}), "contact_method": input.contact_method}
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

    # ── Invite flow ──────────────────────────────────────────────────────────

    @staticmethod
    def _find_by_invite_token(sess: Session, token: str) -> Optional[ExternalContact]:
        vendors = sess.execute(select(ExternalContact)).scalars().all()
        for v in vendors:
            if (v.extra or {}).get("invite_token") == token:
                return v
        return None

    @staticmethod
    def accept_vendor_invite(sess: Session, token: str) -> ExternalContact:
        vendor = VendorService._find_by_invite_token(sess, token)
        if not vendor:
            raise ValueError("Invalid or expired invite link")
        extra = dict(vendor.extra or {})
        if extra.get("invite_status") not in ("accepted", "registered"):
            extra["invite_status"] = "accepted"
        vendor.extra = extra
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(vendor, "extra")
        sess.commit()
        sess.refresh(vendor)
        return vendor

    @staticmethod
    def register_vendor(
        sess: Session, token: str, email: str, password: str
    ) -> Tuple[ExternalContact, str]:
        vendor = VendorService._find_by_invite_token(sess, token)
        if not vendor:
            raise ValueError("Invalid or expired invite link")
        # Check for duplicate email
        all_vendors = sess.execute(select(ExternalContact)).scalars().all()
        for v in all_vendors:
            if v.id != vendor.id and (v.extra or {}).get("vendor_email") == email:
                raise ValueError("Email already registered")
        extra = dict(vendor.extra or {})
        extra["invite_status"] = "registered"
        extra["vendor_email"] = email
        extra["password_hash"] = _hash_password(password)
        vendor.extra = extra
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(vendor, "extra")
        sess.commit()
        sess.refresh(vendor)
        jwt_token = VendorService._create_vendor_jwt(vendor, email)
        return vendor, jwt_token

    @staticmethod
    def authenticate_vendor(
        sess: Session, email: str, password: str
    ) -> Tuple[ExternalContact, str]:
        all_vendors = sess.execute(select(ExternalContact)).scalars().all()
        for v in all_vendors:
            extra = v.extra or {}
            if extra.get("vendor_email") == email and extra.get("password_hash"):
                if _verify_password(password, extra["password_hash"]):
                    jwt_token = VendorService._create_vendor_jwt(v, email)
                    return v, jwt_token
        raise ValueError("Invalid email or password")

    @staticmethod
    def validate_vendor_token(token: str) -> dict:
        payload = jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])
        if payload.get("type") != "vendor":
            raise ValueError("Not a vendor token")
        return payload

    @staticmethod
    def _create_vendor_jwt(vendor: ExternalContact, email: str) -> str:
        payload = {
            "type": "vendor",
            "vendor_id": str(vendor.id),
            "email": email,
            "exp": datetime.now(UTC) + timedelta(days=30),
        }
        return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)
