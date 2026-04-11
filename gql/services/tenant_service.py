from datetime import UTC, date as _date, datetime
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from backends.local_auth import resolve_account_id, resolve_org_id
from db.models import Lease as SqlLease, Tenant as SqlTenant, Unit as SqlUnit, User
from gql.services import portal_auth
from gql.types import AddLeaseForTenantInput, CreateTenantWithLeaseInput


class TenantService:
    @staticmethod
    def delete_tenant(sess: Session, uid: str) -> bool:
        tenant = sess.execute(
            select(SqlTenant).where(
                SqlTenant.external_id == uid,
                SqlTenant.org_id == resolve_org_id(),
                SqlTenant.creator_id == resolve_account_id(),
            )
        ).scalar_one_or_none()
        if not tenant:
            raise ValueError(f"Tenant {uid} not found")
        sess.delete(tenant)
        sess.commit()
        return True

    @staticmethod
    def create_tenant_with_lease(
        sess: Session, input: CreateTenantWithLeaseInput
    ) -> tuple[SqlTenant, SqlUnit, SqlLease]:
        unit = sess.execute(
            select(SqlUnit).where(
                SqlUnit.id == input.unit_id,
                SqlUnit.property_id == input.property_id,
                SqlUnit.org_id == resolve_org_id(),
            )
        ).scalar_one_or_none()
        if not unit:
            raise ValueError(f"Unit {input.unit_id} not found on property {input.property_id}")

        shadow_user = User(
            org_id=resolve_org_id(),
            creator_id=resolve_account_id(),
            user_type="tenant",
            first_name=input.first_name,
            last_name=input.last_name,
            email=input.email,
            phone=input.phone,
            active=True,
            created_at=datetime.now(UTC),
        )
        sess.add(shadow_user)
        sess.flush()

        tenant = SqlTenant(
            org_id=resolve_org_id(),
            creator_id=resolve_account_id(),
            user_id=shadow_user.id,
            created_at=datetime.now(UTC),
        )
        sess.add(tenant)
        sess.flush()

        lease = SqlLease(
            org_id=resolve_org_id(),
            creator_id=resolve_account_id(),
            tenant_id=tenant.id,
            unit_id=unit.id,
            property_id=input.property_id,
            start_date=_date.fromisoformat(input.lease_start),
            end_date=_date.fromisoformat(input.lease_end),
            rent_amount=input.rent_amount,
            payment_status="current",
            created_at=datetime.now(UTC),
        )
        sess.add(lease)
        sess.commit()
        return tenant, unit, lease

    @staticmethod
    def add_lease_for_tenant(
        sess: Session, input: AddLeaseForTenantInput
    ) -> tuple[SqlTenant, SqlUnit, SqlLease]:
        tenant = sess.execute(
            select(SqlTenant).where(
                SqlTenant.external_id == input.tenant_id,
                SqlTenant.org_id == resolve_org_id(),
                SqlTenant.creator_id == resolve_account_id(),
            )
        ).scalar_one_or_none()
        if not tenant:
            raise ValueError(f"Tenant {input.tenant_id} not found")

        unit = sess.execute(
            select(SqlUnit).where(
                SqlUnit.id == input.unit_id,
                SqlUnit.property_id == input.property_id,
                SqlUnit.org_id == resolve_org_id(),
            )
        ).scalar_one_or_none()
        if not unit:
            raise ValueError(f"Unit {input.unit_id} not found on property {input.property_id}")

        lease = SqlLease(
            org_id=resolve_org_id(),
            creator_id=resolve_account_id(),
            tenant_id=tenant.id,
            unit_id=unit.id,
            property_id=input.property_id,
            start_date=_date.fromisoformat(input.lease_start),
            end_date=_date.fromisoformat(input.lease_end),
            rent_amount=input.rent_amount,
            payment_status="current",
            created_at=datetime.now(UTC),
        )
        sess.add(lease)
        sess.commit()
        return tenant, unit, lease

    # ── Portal auth ──────────────────────────────────────────────────────

    @staticmethod
    def _find_by_portal_token(sess: Session, token: str) -> Optional[SqlTenant]:
        return portal_auth.find_by_portal_token(sess, model_class=SqlTenant, token=token)

    @staticmethod
    def authenticate_by_token(sess: Session, token: str) -> Tuple[SqlTenant, str]:
        tenant = TenantService._find_by_portal_token(sess, token)
        if not tenant:
            raise ValueError("Invalid portal link")
        jwt_token = portal_auth.create_portal_jwt("tenant", tenant.external_id)
        return tenant, jwt_token

    @staticmethod
    def get_portal_url(tenant: SqlTenant) -> str:
        token = portal_auth.parse_portal_entity_extra(tenant.extra).portal_token
        if not token:
            return ""
        return portal_auth.build_portal_url(token)

    @staticmethod
    def validate_tenant_token(token: str) -> dict:
        return portal_auth.validate_portal_jwt(token, "tenant")

    @staticmethod
    def ensure_portal_token(sess: Session, tenant: SqlTenant) -> str:
        return portal_auth.ensure_portal_token(tenant)
