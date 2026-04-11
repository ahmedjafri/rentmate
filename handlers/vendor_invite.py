"""Public endpoint for vendor portal token auth (no login required).

The short URL `/t/{token}` is a frontend route served by the SPA.
The frontend calls this API to exchange the token for a JWT.
"""
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.orm import Session

from gql.services.vendor_service import VendorService
from handlers.deps import get_db

router = APIRouter(prefix="/api/vendor-token")


@router.get("/{token}")
def get_vendor_token(token: str, request: Request):
    """Exchange a portal token for a JWT. No accept step needed."""
    db: Session = get_db(request)
    vendor = VendorService._find_by_portal_token(db, token)
    if not vendor:
        raise HTTPException(status_code=404, detail="Invalid portal link")
    _, jwt_token = VendorService.authenticate_by_token(db, token)
    return {
        "vendor_id": str(vendor.external_id),
        "name": vendor.name,
        "access_token": jwt_token,
    }
