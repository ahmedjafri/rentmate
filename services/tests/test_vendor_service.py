import pytest

from db.models import User
from gql.types import CreateVendorInput, UpdateVendorInput
from services.vendor_service import VendorService


def test_create_vendor_requires_phone_and_type(db):
    with pytest.raises(ValueError, match="Vendor type is required"):
        VendorService.create_vendor(db, CreateVendorInput(name="Bob", phone="", vendor_type=None))

    with pytest.raises(ValueError, match="Phone number is required"):
        VendorService.create_vendor(db, CreateVendorInput(name="Bob", phone="", vendor_type="Plumber"))


def test_create_update_delete_vendor_use_external_surface(db):
    vendor = VendorService.create_vendor(
        db,
        CreateVendorInput(
            name="Bob Plumber",
            company="Pipe Co",
            vendor_type="Plumber",
            phone="+15550001111",
            email="bob@example.com",
            notes="24/7",
        ),
    )

    assert vendor.external_id
    assert vendor.org_id == 1
    assert vendor.creator_id == 1
    assert vendor.extra["portal_token"]

    updated = VendorService.update_vendor(
        db,
        UpdateVendorInput(
            uid=vendor.external_id,
            name="Bob Electric",
            company="Wire Co",
            vendor_type="Electrician",
            phone="+15550002222",
            email="wire@example.com",
            notes="licensed",
        ),
    )

    assert updated.name == "Bob Electric"
    assert updated.role_label == "Electrician"
    assert updated.phone == "+15550002222"

    assert VendorService.delete_vendor(db, vendor.external_id) is True
    assert db.query(User).filter_by(external_id=vendor.external_id, user_type="vendor").one_or_none() is None


def test_vendor_portal_helpers_round_trip(db):
    vendor = VendorService.create_vendor(
        db,
        CreateVendorInput(name="Portal Vendor", vendor_type="Plumber", phone="+15550003333"),
    )

    found, token = VendorService.authenticate_by_token(db, vendor.extra["portal_token"])

    assert found.id == vendor.id
    assert VendorService.validate_vendor_token(token)["vendor_id"] == vendor.external_id
    assert VendorService.get_portal_url(vendor).endswith(vendor.extra["portal_token"])
    assert VendorService.ensure_portal_token(db, vendor) == vendor.extra["portal_token"]
