import uuid
from datetime import UTC, datetime, date as _date
from sqlalchemy import select
from sqlalchemy.orm import Session
from db.models import Tenant as SqlTenant, Lease as SqlLease, Unit as SqlUnit
from gql.types import CreateTenantWithLeaseInput, AddLeaseForTenantInput


class TenantService:
    @staticmethod
    def delete_tenant(sess: Session, uid: str) -> bool:
        tenant = sess.execute(select(SqlTenant).where(SqlTenant.id == uid)).scalar_one_or_none()
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
            select(SqlUnit).where(SqlUnit.id == input.unit_id, SqlUnit.property_id == input.property_id)
        ).scalar_one_or_none()
        if not unit:
            raise ValueError(f"Unit {input.unit_id} not found on property {input.property_id}")

        tenant = SqlTenant(
            id=str(uuid.uuid4()),
            first_name=input.first_name,
            last_name=input.last_name,
            email=input.email,
            phone=input.phone,
            created_at=datetime.now(UTC),
        )
        sess.add(tenant)
        sess.flush()

        lease = SqlLease(
            id=str(uuid.uuid4()),
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
        tenant = sess.execute(select(SqlTenant).where(SqlTenant.id == input.tenant_id)).scalar_one_or_none()
        if not tenant:
            raise ValueError(f"Tenant {input.tenant_id} not found")

        unit = sess.execute(
            select(SqlUnit).where(SqlUnit.id == input.unit_id, SqlUnit.property_id == input.property_id)
        ).scalar_one_or_none()
        if not unit:
            raise ValueError(f"Unit {input.unit_id} not found on property {input.property_id}")

        lease = SqlLease(
            id=str(uuid.uuid4()),
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
