
import pytest

from db.models import User
from gql.services import portal_auth


def test_build_portal_url_prefers_public_url(monkeypatch):
    monkeypatch.setenv("RENTMATE_PUBLIC_URL", "https://rentmate.example/")
    assert portal_auth.build_portal_url("abc123") == "https://rentmate.example/t/abc123"


def test_build_portal_url_falls_back_to_localhost(monkeypatch):
    monkeypatch.delenv("RENTMATE_PUBLIC_URL", raising=False)
    monkeypatch.setenv("RENTMATE_PORT", "9999")
    assert portal_auth.build_portal_url("abc123") == "http://localhost:9999/t/abc123"


def test_create_and_validate_portal_jwt_round_trip():
    token = portal_auth.create_portal_jwt("vendor", "vendor-ext-1")
    payload = portal_auth.validate_portal_jwt(token, "vendor")

    assert payload["type"] == "vendor"
    assert payload["vendor_id"] == "vendor-ext-1"


def test_validate_portal_jwt_rejects_wrong_type():
    token = portal_auth.create_portal_jwt("tenant", "tenant-ext-1")
    with pytest.raises(ValueError, match="Not a vendor token"):
        portal_auth.validate_portal_jwt(token, "vendor")


def test_ensure_portal_token_persists_to_entity(db):
    vendor = User(
        org_id=1,
        creator_id=1,
        user_type="vendor",
        first_name="Token",
        last_name="Vendor",
        phone="+15550001111",
    )
    db.add(vendor)
    db.commit()

    token = portal_auth.ensure_portal_token(vendor)

    assert token
    assert vendor.extra["portal_token"] == token


def test_find_by_portal_token_matches_portal_and_legacy_invite_tokens(db):
    current = User(
        org_id=1,
        creator_id=1,
        user_type="vendor",
        first_name="Current",
        phone="+15550002222",
        extra={"portal_token": "current-token"},
    )
    legacy = User(
        org_id=1,
        creator_id=1,
        user_type="vendor",
        first_name="Legacy",
        phone="+15550003333",
        extra={"invite_token": "legacy-token"},
    )
    db.add_all([current, legacy])
    db.commit()

    assert portal_auth.find_by_portal_token(db, model_class=User, token="current-token").id == current.id
    assert portal_auth.find_by_portal_token(db, model_class=User, token="legacy-token").id == legacy.id
    assert portal_auth.find_by_portal_token(db, model_class=User, token="missing") is None
