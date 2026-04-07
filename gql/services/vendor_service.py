import uuid
from datetime import UTC, datetime
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import ExternalContact
from gql.types import CreateVendorInput, UpdateVendorInput, VENDOR_TYPES
from gql.services import portal_auth


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
        extra: dict = {"portal_token": portal_auth.generate_portal_token()}
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
        return portal_auth.find_by_portal_token(sess, ExternalContact, token)

    @staticmethod
    def authenticate_by_token(sess: Session, token: str) -> Tuple[ExternalContact, str]:
        vendor = VendorService._find_by_portal_token(sess, token)
        if not vendor:
            raise ValueError("Invalid portal link")
        jwt_token = portal_auth.create_portal_jwt("vendor", str(vendor.id))
        return vendor, jwt_token

    @staticmethod
    def get_portal_url(vendor: ExternalContact) -> str:
        token = (vendor.extra or {}).get("portal_token") or (vendor.extra or {}).get("invite_token")
        if not token:
            return ""
        return portal_auth.build_portal_url(token)

    @staticmethod
    def validate_vendor_token(token: str) -> dict:
        return portal_auth.validate_portal_jwt(token, "vendor")

    @staticmethod
    def ensure_portal_token(sess: Session, vendor: ExternalContact) -> str:
        return portal_auth.ensure_portal_token(vendor)
