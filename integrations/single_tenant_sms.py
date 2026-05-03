from sqlalchemy.orm import joinedload

from db.utils import normalize_phone


class SingleTenantSMSRouter:
    def resolve(self, db, *, from_number: str, to_number: str):
        """Resolve an SMS participant. Returns (creator_id, entity, direction, entity_type).

        entity_type is 'tenant' or 'vendor'.
        """
        if not from_number or not to_number:
            return None

        from_norm = normalize_phone(from_number)
        to_norm = normalize_phone(to_number)

        if not from_norm or not to_norm:
            return None

        from db.models import Tenant, User

        # inbound: from = tenant (phone is on User, joined through Tenant.user)
        tenant = (
            db.query(Tenant)
            .join(User, Tenant.user_id == User.id)
            .options(joinedload(Tenant.user))
            .filter(User.phone == from_norm)
            .one_or_none()
        )
        if tenant:
            return tenant.creator_id, tenant, "inbound", "tenant"

        # inbound: from = vendor
        vendor = db.query(User).filter(User.user_type == "vendor", User.phone == from_norm).one_or_none()
        if vendor:
            return vendor.creator_id, vendor, "inbound", "vendor"

        # outbound: to = tenant
        tenant = (
            db.query(Tenant)
            .join(User, Tenant.user_id == User.id)
            .options(joinedload(Tenant.user))
            .filter(User.phone == to_norm)
            .one_or_none()
        )
        if tenant:
            return tenant.creator_id, tenant, "outbound", "tenant"

        # outbound: to = vendor
        vendor = db.query(User).filter(User.user_type == "vendor", User.phone == to_norm).one_or_none()
        if vendor:
            return vendor.creator_id, vendor, "outbound", "vendor"

        return None
