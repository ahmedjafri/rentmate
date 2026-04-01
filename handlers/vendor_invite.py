"""Public endpoints for vendor invite flow (no auth required).

The invite link IS the vendor's permanent access credential.
Visiting it accepts the invite and returns a JWT for portal access.
"""
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.orm import Session

from gql.services.vendor_service import VendorService
from handlers.deps import get_db

router = APIRouter(prefix="/api/vendor-invite")


@router.get("/{token}")
def get_invite_info(token: str, request: Request):
    db: Session = get_db(request)
    vendor = VendorService._find_by_invite_token(db, token)
    if not vendor:
        raise HTTPException(status_code=404, detail="Invalid or expired invite link")
    extra = vendor.extra or {}
    status = extra.get("invite_status", "pending")
    result = {
        "name": vendor.name,
        "company": vendor.company,
        "vendor_type": vendor.role_label,
        "invite_status": status,
    }
    # For returning vendors, include a fresh JWT so they can go straight to the portal
    if status == "accepted":
        _, jwt_token = VendorService.get_jwt_for_token(db, token)
        result["access_token"] = jwt_token
    return result


@router.post("/{token}/accept")
def accept_invite(token: str, request: Request):
    db: Session = get_db(request)
    try:
        vendor, jwt_token = VendorService.accept_invite(db, token)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True, "access_token": jwt_token, "vendor_id": str(vendor.id), "name": vendor.name}
