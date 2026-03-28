import uuid
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session
from db.models import ExternalContact
from gql.types import CreateVendorInput, UpdateVendorInput, VENDOR_TYPES, VENDOR_CONTACT_METHODS


def _validate_vendor_type(vendor_type: str | None) -> None:
    if vendor_type is not None and vendor_type not in VENDOR_TYPES:
        raise ValueError(f"Invalid vendor type '{vendor_type}'. Must be one of: {', '.join(VENDOR_TYPES)}")


def _validate_contact_method(method: str | None) -> None:
    if method is not None and method not in VENDOR_CONTACT_METHODS:
        raise ValueError(f"Invalid contact method '{method}'. Must be one of: {', '.join(VENDOR_CONTACT_METHODS)}")


class VendorService:
    @staticmethod
    def create_vendor(sess: Session, input: CreateVendorInput) -> ExternalContact:
        _validate_vendor_type(input.vendor_type)
        _validate_contact_method(input.contact_method)
        vendor = ExternalContact(
            id=str(uuid.uuid4()),
            name=input.name,
            company=input.company,
            role_label=input.vendor_type,
            phone=input.phone,
            email=input.email,
            notes=input.notes,
            extra={"contact_method": input.contact_method or "rentmate"},
            created_at=datetime.utcnow(),
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
