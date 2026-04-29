"""Lease lifecycle helpers — read, update, terminate, multi-tenant roster.

Companion to ``TenantService`` which only owns the create paths
(``create_tenant_with_lease``, ``add_lease_for_tenant``). Splitting the
update/terminate/roster ops out keeps ``TenantService`` focused on
tenant CRUD and gives the agent a clean ``LeaseService`` surface to
hang the new lease tools off of.

Multi-tenant note: the underlying schema is a many-to-many via
``lease_tenants``. ``Lease.tenant_id`` is the legacy single-tenant
column we still write on insert (an ``after_insert`` trigger mirrors it
into the join table). The roster ops below operate on
``LeaseTenant`` rows directly and refuse to remove the last tenant on a
lease so the legacy ``tenant_id`` always points at a real row.
"""
from __future__ import annotations

from datetime import UTC, date as _date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backends.local_auth import resolve_account_id, resolve_org_id
from db.models import (
    Lease as SqlLease,
    LeaseTenant as SqlLeaseTenant,
    Property as SqlProperty,
    Tenant as SqlTenant,
    Unit as SqlUnit,
)


_VALID_PAYMENT_STATUSES = ("current", "late", "overdue")


class LeaseService:
    # ── Lookup ─────────────────────────────────────────────────────────

    @staticmethod
    def fetch_lease(sess: Session, lease_id: str) -> SqlLease | None:
        """Return the lease row by ``id`` scoped to the current org. The
        join-table-driven ``tenants`` relationship is loaded lazily by the
        caller as needed."""
        return sess.execute(
            select(SqlLease).where(
                SqlLease.id == str(lease_id),
                SqlLease.org_id == resolve_org_id(),
            )
        ).scalar_one_or_none()

    @staticmethod
    def list_leases(
        sess: Session,
        *,
        property_id: str | None = None,
        unit_id: str | None = None,
        tenant_id: str | None = None,
        active_only: bool = True,
        as_of: _date | None = None,
        limit: int = 100,
    ) -> list[SqlLease]:
        """Filter leases by property/unit/tenant + active-on-date.

        ``tenant_id`` is the tenant's external UUID. ``active_only`` keeps
        leases whose ``start_date <= as_of <= end_date``; combined with
        the multi-tenant join this is the agent's primary "what's the
        active lease for X" query.
        """
        as_of = as_of or _date.today()

        # Resolve tenant external_id → internal id once so we can filter
        # by the legacy single-tenant column AND the join table in a
        # single query.
        tenant_internal_id: int | None = None
        if tenant_id:
            tenant = sess.execute(
                select(SqlTenant).where(
                    SqlTenant.external_id == str(tenant_id),
                    SqlTenant.org_id == resolve_org_id(),
                )
            ).scalar_one_or_none()
            if tenant is None:
                return []
            tenant_internal_id = tenant.id

        stmt = select(SqlLease).where(SqlLease.org_id == resolve_org_id())
        if property_id:
            stmt = stmt.where(SqlLease.property_id == str(property_id))
        if unit_id:
            stmt = stmt.where(SqlLease.unit_id == str(unit_id))
        if tenant_internal_id is not None:
            # Use the join table so a tenant linked via
            # ``lease_tenants`` (but not necessarily the primary
            # ``tenant_id``) still surfaces.
            stmt = (
                stmt.join(
                    SqlLeaseTenant,
                    (SqlLeaseTenant.org_id == SqlLease.org_id)
                    & (SqlLeaseTenant.lease_id == SqlLease.id),
                )
                .where(SqlLeaseTenant.tenant_id == tenant_internal_id)
            )
        if active_only:
            stmt = stmt.where(SqlLease.start_date <= as_of, SqlLease.end_date >= as_of)
        stmt = stmt.order_by(SqlLease.start_date.desc()).limit(limit)
        return list(sess.execute(stmt).scalars().all())

    # ── Create ─────────────────────────────────────────────────────────

    @staticmethod
    def create_lease(
        sess: Session,
        *,
        property_id: str,
        unit_id: str,
        tenant_ids: list[str],
        start_date: _date,
        end_date: _date,
        rent_amount: float,
        payment_status: str = "current",
    ) -> SqlLease:
        """Create a lease for an existing property + unit and link one or
        more existing tenants. Sister to
        ``TenantService.create_tenant_with_lease`` for the case where
        every tenant already exists (so the agent doesn't have to fall
        back to ``create_suggestion`` with a hand-rolled action_payload
        when "add a lease for these existing tenants" is the actual ask).

        ``tenant_ids`` are external UUIDs from ``lookup_tenants`` and
        must be non-empty.
        """
        if not tenant_ids:
            raise ValueError("create_lease requires at least one tenant_id")
        if end_date < start_date:
            raise ValueError(
                f"end_date {end_date} cannot precede start_date {start_date}"
            )
        if rent_amount < 0:
            raise ValueError("rent_amount cannot be negative")
        if payment_status not in _VALID_PAYMENT_STATUSES:
            raise ValueError(
                f"payment_status must be one of {_VALID_PAYMENT_STATUSES}, got {payment_status!r}"
            )

        unit = sess.execute(
            select(SqlUnit).where(
                SqlUnit.id == str(unit_id),
                SqlUnit.property_id == str(property_id),
                SqlUnit.org_id == resolve_org_id(),
            )
        ).scalar_one_or_none()
        if unit is None:
            raise ValueError(
                f"Unit {unit_id} not found on property {property_id}"
            )

        tenants: list[SqlTenant] = []
        for ext_id in tenant_ids:
            tenant = sess.execute(
                select(SqlTenant).where(
                    SqlTenant.external_id == str(ext_id),
                    SqlTenant.org_id == resolve_org_id(),
                )
            ).scalar_one_or_none()
            if tenant is None:
                raise ValueError(f"Tenant {ext_id} not found")
            tenants.append(tenant)

        primary = tenants[0]
        lease = SqlLease(
            org_id=resolve_org_id(),
            creator_id=resolve_account_id(),
            tenant_id=primary.id,  # legacy single-tenant FK; mirrored into LeaseTenant by trigger
            unit_id=unit.id,
            property_id=str(property_id),
            start_date=start_date,
            end_date=end_date,
            rent_amount=float(rent_amount),
            payment_status=payment_status,
            created_at=datetime.now(UTC),
        )
        sess.add(lease)
        sess.flush()

        # Link any *additional* tenants past the primary; the after_insert
        # trigger already linked the primary one.
        for tenant in tenants[1:]:
            existing = sess.get(SqlLeaseTenant, (lease.org_id, lease.id, tenant.id))
            if existing is None:
                sess.add(SqlLeaseTenant(
                    org_id=lease.org_id,
                    lease_id=lease.id,
                    tenant_id=tenant.id,
                    created_at=datetime.now(UTC),
                ))
        sess.flush()
        sess.commit()
        return lease

    # ── Update ─────────────────────────────────────────────────────────

    @staticmethod
    def update_lease(
        sess: Session,
        *,
        lease_id: str,
        end_date: _date | None = None,
        rent_amount: float | None = None,
        payment_status: str | None = None,
    ) -> SqlLease:
        """Patch the editable fields on a lease.

        Only ``end_date``, ``rent_amount``, ``payment_status`` are
        editable — start_date and the FKs (tenant_id, unit_id,
        property_id) are immutable on a lease. To "move" a lease, end
        the old one and create a new one.
        """
        lease = LeaseService.fetch_lease(sess, lease_id)
        if lease is None:
            raise ValueError(f"Lease {lease_id} not found")

        if payment_status is not None:
            normalized = payment_status.strip().lower()
            if normalized not in _VALID_PAYMENT_STATUSES:
                raise ValueError(
                    f"payment_status must be one of {_VALID_PAYMENT_STATUSES}, got {payment_status!r}"
                )
            lease.payment_status = normalized

        if end_date is not None:
            if end_date < lease.start_date:
                raise ValueError(
                    f"end_date {end_date} cannot precede start_date {lease.start_date}"
                )
            lease.end_date = end_date

        if rent_amount is not None:
            if rent_amount < 0:
                raise ValueError("rent_amount cannot be negative")
            lease.rent_amount = float(rent_amount)

        sess.flush()
        sess.commit()
        return lease

    # ── Terminate ──────────────────────────────────────────────────────

    @staticmethod
    def terminate_lease(
        sess: Session,
        *,
        lease_id: str,
        effective_date: _date | None = None,
    ) -> SqlLease:
        """End a lease early by setting ``end_date`` to ``effective_date``
        (defaults to today). Preserves the row + join-table tenants so
        history stays intact — to fully delete use ``TenantService.delete_tenant``.
        """
        lease = LeaseService.fetch_lease(sess, lease_id)
        if lease is None:
            raise ValueError(f"Lease {lease_id} not found")
        when = effective_date or _date.today()
        if when < lease.start_date:
            raise ValueError(
                f"effective_date {when} cannot precede start_date {lease.start_date}"
            )
        lease.end_date = when
        sess.flush()
        sess.commit()
        return lease

    # ── Multi-tenant roster ────────────────────────────────────────────

    @staticmethod
    def add_tenant_to_lease(
        sess: Session,
        *,
        lease_id: str,
        tenant_id: str,
    ) -> SqlLease:
        """Attach an existing tenant to an existing lease via the
        ``lease_tenants`` join. Idempotent — adding the same tenant twice
        is a no-op."""
        lease = LeaseService.fetch_lease(sess, lease_id)
        if lease is None:
            raise ValueError(f"Lease {lease_id} not found")
        tenant = sess.execute(
            select(SqlTenant).where(
                SqlTenant.external_id == str(tenant_id),
                SqlTenant.org_id == resolve_org_id(),
            )
        ).scalar_one_or_none()
        if tenant is None:
            raise ValueError(f"Tenant {tenant_id} not found")

        existing = sess.get(SqlLeaseTenant, (lease.org_id, lease.id, tenant.id))
        if existing is None:
            sess.add(SqlLeaseTenant(
                org_id=lease.org_id,
                lease_id=lease.id,
                tenant_id=tenant.id,
                created_at=datetime.now(UTC),
            ))
            sess.flush()
            sess.commit()
        return lease

    @staticmethod
    def remove_tenant_from_lease(
        sess: Session,
        *,
        lease_id: str,
        tenant_id: str,
    ) -> SqlLease:
        """Detach a tenant from a lease's join-table roster. Refuses to
        remove the last tenant on a lease (the legacy ``Lease.tenant_id``
        FK is non-null and would dangle). For a true single-tenant lease
        with no replacement, terminate the lease instead."""
        lease = LeaseService.fetch_lease(sess, lease_id)
        if lease is None:
            raise ValueError(f"Lease {lease_id} not found")
        tenant = sess.execute(
            select(SqlTenant).where(
                SqlTenant.external_id == str(tenant_id),
                SqlTenant.org_id == resolve_org_id(),
            )
        ).scalar_one_or_none()
        if tenant is None:
            raise ValueError(f"Tenant {tenant_id} not found")

        link = sess.get(SqlLeaseTenant, (lease.org_id, lease.id, tenant.id))
        if link is None:
            return lease  # nothing to do — agent shouldn't error on idempotent removes

        # Refuse to wipe the last tenant.
        remaining = sess.execute(
            select(SqlLeaseTenant).where(
                SqlLeaseTenant.org_id == lease.org_id,
                SqlLeaseTenant.lease_id == lease.id,
                SqlLeaseTenant.tenant_id != tenant.id,
            )
        ).scalars().all()
        if not remaining:
            raise ValueError(
                f"Cannot remove the last tenant from lease {lease_id}; "
                "terminate the lease instead."
            )

        # Keep the legacy primary tenant_id pointing at a row that's still
        # on the join table, otherwise queries that join via tenant_id
        # would silently drop the lease.
        if lease.tenant_id == tenant.id:
            lease.tenant_id = remaining[0].tenant_id

        sess.delete(link)
        sess.flush()
        sess.commit()
        return lease

    # ── Read helpers used by the lookup tool ───────────────────────────

    @staticmethod
    def lease_to_payload(sess: Session, lease: SqlLease) -> dict[str, Any]:
        """Serialize a Lease for tool-style JSON output. Pulls tenants
        through the join table so multi-tenant leases echo every name."""
        # Resolve property + unit names without forcing eager loads.
        prop_row = sess.get(SqlProperty, lease.property_id) if lease.property_id else None
        unit_row = sess.get(SqlUnit, lease.unit_id) if lease.unit_id else None
        tenants = list(getattr(lease, "tenants", []) or [])
        tenant_payloads: list[dict[str, Any]] = []
        for t in tenants:
            user = getattr(t, "user", None)
            full = " ".join(filter(None, [
                getattr(user, "first_name", None),
                getattr(user, "last_name", None),
            ])).strip() if user else "Tenant"
            tenant_payloads.append({
                "tenant_id": str(t.external_id),
                "name": full or "Tenant",
            })
        today = _date.today()
        active = bool(lease.start_date <= today <= lease.end_date)
        return {
            "lease_id": str(lease.id),
            "property_id": str(lease.property_id) if lease.property_id else None,
            "property_name": getattr(prop_row, "name", None) or None,
            "unit_id": str(lease.unit_id) if lease.unit_id else None,
            "unit_label": getattr(unit_row, "label", None) or None,
            "start_date": lease.start_date.isoformat() if lease.start_date else None,
            "end_date": lease.end_date.isoformat() if lease.end_date else None,
            "rent_amount": float(lease.rent_amount) if lease.rent_amount is not None else None,
            "payment_status": lease.payment_status or None,
            "active": active,
            "tenants": tenant_payloads,
        }
