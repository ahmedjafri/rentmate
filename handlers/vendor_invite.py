"""Public endpoints for vendor invite accept flow (no auth required)."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from db.models import ExternalContact
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
    return {
        "name": vendor.name,
        "company": vendor.company,
        "vendor_type": vendor.role_label,
        "invite_status": extra.get("invite_status", "pending"),
    }


@router.post("/{token}/accept")
def accept_invite(token: str, request: Request):
    db: Session = get_db(request)
    try:
        VendorService.accept_vendor_invite(db, token)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True}


class RegisterBody(BaseModel):
    email: str
    password: str


@router.post("/{token}/register")
def register_vendor(token: str, body: RegisterBody, request: Request):
    db: Session = get_db(request)
    try:
        vendor, jwt_token = VendorService.register_vendor(db, token, body.email, body.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"access_token": jwt_token, "vendor_id": str(vendor.id), "name": vendor.name}
