"""Public endpoint for tenant portal token auth (no login required)."""
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.orm import Session

from gql.services.tenant_service import TenantService
from handlers.deps import get_db

router = APIRouter(prefix="/api/tenant-token")


@router.get("/{token}")
def get_tenant_token(token: str, request: Request):
    """Exchange a portal token for a JWT. No accept step needed."""
    db: Session = get_db(request)
    tenant = TenantService._find_by_portal_token(db, token)
    if not tenant:
        raise HTTPException(status_code=404, detail="Invalid portal link")
    _, jwt_token = TenantService.authenticate_by_token(db, token)
    name = f"{tenant.first_name} {tenant.last_name}".strip()
    return {
        "tenant_id": str(tenant.id),
        "name": name,
        "access_token": jwt_token,
    }
