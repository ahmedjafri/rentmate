from __future__ import annotations

from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from backends.local_auth import resolve_account_id, resolve_org_id
from db.models import User
from gql.services import portal_auth
from gql.types import VENDOR_TYPES, CreateVendorInput, UpdateVendorInput

VENDOR_USER_TYPE = "vendor"


def _validate_vendor_type(vendor_type: str | None) -> None:
    if vendor_type is not None and vendor_type not in VENDOR_TYPES:
        raise ValueError(f"Invalid vendor type '{vendor_type}'. Must be one of: {', '.join(VENDOR_TYPES)}")


def split_display_name(name: str) -> tuple[str | None, str | None]:
    parts = (name or "").split(None, 1)
    return (parts[0], parts[1] if len(parts) > 1 else None) if parts else (None, None)


def get_vendor_query(sess: Session):
    return select(User).where(
        User.user_type == VENDOR_USER_TYPE,
        User.org_id == resolve_org_id(),
        User.creator_id == resolve_account_id(),
    )


def get_vendor_by_external_id(sess: Session, uid: str) -> User | None:
    return sess.execute(get_vendor_query(sess).where(User.external_id == uid)).scalar_one_or_none()


def get_vendor_by_id(sess: Session, vendor_id: int) -> User | None:
    return sess.execute(get_vendor_query(sess).where(User.id == vendor_id)).scalar_one_or_none()


class VendorService:
    @staticmethod
    def create_vendor(sess: Session, input: CreateVendorInput) -> User:
        if not input.vendor_type:
            raise ValueError("Vendor type is required")
        _validate_vendor_type(input.vendor_type)
        if not input.phone:
            raise ValueError("Phone number is required")

        first_name, last_name = split_display_name(input.name)
        extra = portal_auth.dump_portal_entity_extra(
            portal_auth.PortalEntityExtra(portal_token=portal_auth.generate_portal_token())
        )
        vendor = User(
            org_id=resolve_org_id(),
            creator_id=resolve_account_id(),
            user_type=VENDOR_USER_TYPE,
            first_name=first_name,
            last_name=last_name,
            company=input.company,
            role_label=input.vendor_type,
            phone=input.phone,
            email=input.email,
            notes=input.notes,
            extra=extra,
            active=True,
        )
        sess.add(vendor)
        sess.commit()
        sess.refresh(vendor)
        return vendor

    @staticmethod
    def update_vendor(sess: Session, input: UpdateVendorInput) -> User:
        vendor = get_vendor_by_external_id(sess, input.uid)
        if not vendor:
            raise ValueError(f"Vendor {input.uid} not found")
        if input.name is not None:
            vendor.first_name, vendor.last_name = split_display_name(input.name)
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
        vendor = get_vendor_by_external_id(sess, uid)
        if not vendor:
            raise ValueError(f"Vendor {uid} not found")
        sess.delete(vendor)
        sess.commit()
        return True

    @staticmethod
    def _find_by_portal_token(sess: Session, token: str) -> Optional[User]:
        return portal_auth.find_by_portal_token(sess, model_class=User, token=token)

    @staticmethod
    def authenticate_by_token(sess: Session, token: str) -> Tuple[User, str]:
        vendor = VendorService._find_by_portal_token(sess, token)
        if not vendor or vendor.user_type != VENDOR_USER_TYPE:
            raise ValueError("Invalid portal link")
        jwt_token = portal_auth.create_portal_jwt("vendor", str(vendor.external_id))
        return vendor, jwt_token

    @staticmethod
    def get_portal_url(vendor: User) -> str:
        extra = portal_auth.parse_portal_entity_extra(vendor.extra)
        token = extra.portal_token or extra.invite_token
        if not token:
            return ""
        return portal_auth.build_portal_url(token)

    @staticmethod
    def validate_vendor_token(token: str) -> dict:
        return portal_auth.validate_portal_jwt(token, "vendor")

    @staticmethod
    def ensure_portal_token(sess: Session, vendor: User) -> str:
        return portal_auth.ensure_portal_token(vendor, db=sess)
