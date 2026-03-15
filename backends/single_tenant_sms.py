from db.utils import normalize_phone

DEFAULT_ACCOUNT_ID = "00000000-0000-0000-0000-000000000001"


class SingleTenantSMSRouter:
    def resolve(self, db, from_number: str, to_number: str):
        if not from_number or not to_number:
            return None

        from_norm = normalize_phone(from_number)
        to_norm = normalize_phone(to_number)

        if not from_norm or not to_norm:
            return None

        from db.models import Tenant

        # inbound: from = tenant
        tenant = db.query(Tenant).filter(Tenant.phone == from_norm).one_or_none()
        if tenant:
            return DEFAULT_ACCOUNT_ID, tenant, "inbound"

        # outbound: to = tenant
        tenant = db.query(Tenant).filter(Tenant.phone == to_norm).one_or_none()
        if tenant:
            return DEFAULT_ACCOUNT_ID, tenant, "outbound"

        return None
