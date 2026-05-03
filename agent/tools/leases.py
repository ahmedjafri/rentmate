"""Lease lifecycle tools — read, update, terminate, multi-tenant roster.

Sister to ``llm/tools/entities.py`` (properties, tenants, units). Lease
*creation* still flows through ``CreateTenantTool`` (which writes the
first lease as a side-effect of creating the tenant). These tools
cover everything the agent can do *after* a lease exists:

- ``LookupLeasesTool``         — filter by property / unit / tenant / active
- ``UpdateLeaseTool``          — patch end_date / rent_amount / payment_status
- ``TerminateLeaseTool``       — end a lease early (sets end_date)
- ``AddTenantToLeaseTool``     — add a tenant to the multi-tenant roster
- ``RemoveTenantFromLeaseTool``— remove a tenant from the roster (refuses
                                 the last one — terminate the lease instead)

All five hit ``LeaseService`` (gql/services/lease_service.py) so the
mutation logic + validation lives in one place. Tools wrap the
``LeaseService`` calls in ``tool_session`` for savepoint isolation,
stringify dates from ISO YYYY-MM-DD, and return JSON in the
``{status, message, lease}`` shape the chat handler renders.
"""
from __future__ import annotations

import json
from datetime import date as _date
from typing import Any

from agent.tools._common import Tool, ToolMode, _reject_placeholder_id, tool_session


def _parse_iso_date(value: str | None, *, field_name: str) -> _date | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return _date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be YYYY-MM-DD; got {value!r}") from exc


class CreateLeaseTool(Tool):
    """Create a new lease for an existing property/unit and existing tenant(s).

    Use when the manager asks to "add a lease" or "lease unit X to
    Marcus and Priya" — the property, unit, and tenants already exist.
    For a brand-new tenant who needs a lease in the same step, use
    ``create_tenant`` (which creates both in one shot)."""

    @property
    def name(self) -> str:
        return "create_lease"

    @property
    def description(self) -> str:
        return (
            "Create a new lease on an existing property + unit and link "
            "one or more existing tenants. tenant_ids must be the "
            "external UUIDs returned by lookup_tenants. Use "
            "create_tenant when the tenant doesn't exist yet (it "
            "creates the tenant + initial lease in one shot). Dates are "
            "YYYY-MM-DD; rent_amount is monthly dollars; payment_status "
            "defaults to 'current'. Never invent ids — call "
            "lookup_properties / lookup_tenants first."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": [
                "property_id", "unit_id", "tenant_ids",
                "start_date", "end_date", "rent_amount",
            ],
            "properties": {
                "property_id": {
                    "type": "string",
                    "description": "Real property UUID from lookup_properties.",
                },
                "unit_id": {
                    "type": "string",
                    "description": "Real unit UUID from the property's unit list.",
                },
                "tenant_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "One or more existing tenant external UUIDs from "
                        "lookup_tenants. The first id becomes the legacy "
                        "primary tenant; the rest get linked via the "
                        "lease_tenants join."
                    ),
                },
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD (>= start_date)"},
                "rent_amount": {"type": "number", "description": "Monthly rent in dollars."},
                "payment_status": {
                    "type": "string",
                    "enum": ["current", "late", "overdue"],
                    "description": "Defaults to 'current'.",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        from services.lease_service import LeaseService

        property_id = (kwargs.get("property_id") or "").strip()
        unit_id = (kwargs.get("unit_id") or "").strip()
        tenant_ids_raw = kwargs.get("tenant_ids") or []
        if isinstance(tenant_ids_raw, str):
            tenant_ids_raw = [tenant_ids_raw]
        tenant_ids = [str(t).strip() for t in tenant_ids_raw if str(t).strip()]

        if not property_id or not unit_id or not tenant_ids:
            return json.dumps({
                "status": "error",
                "message": "property_id, unit_id, and at least one tenant_id are required.",
            })
        for raw, name, lookup in (
            (property_id, "property_id", "lookup_properties"),
            (unit_id, "unit_id", "lookup_properties"),
        ):
            bad = _reject_placeholder_id(raw, name=name, lookup_tool=lookup)
            if bad:
                return json.dumps({"status": "error", "message": bad})
        for tid in tenant_ids:
            bad = _reject_placeholder_id(tid, name="tenant_id", lookup_tool="lookup_tenants")
            if bad:
                return json.dumps({"status": "error", "message": bad})

        try:
            start_date = _parse_iso_date(kwargs.get("start_date"), field_name="start_date")
            end_date = _parse_iso_date(kwargs.get("end_date"), field_name="end_date")
        except ValueError as exc:
            return json.dumps({"status": "error", "message": str(exc)})
        if start_date is None or end_date is None:
            return json.dumps({
                "status": "error",
                "message": "start_date and end_date are required (YYYY-MM-DD).",
            })

        rent_amount = kwargs.get("rent_amount")
        if rent_amount is None:
            return json.dumps({"status": "error", "message": "rent_amount is required."})

        try:
            with tool_session() as db:
                lease = LeaseService.create_lease(
                    db,
                    property_id=property_id,
                    unit_id=unit_id,
                    tenant_ids=tenant_ids,
                    start_date=start_date,
                    end_date=end_date,
                    rent_amount=float(rent_amount),
                    payment_status=(kwargs.get("payment_status") or "current"),
                )
                payload = LeaseService.lease_to_payload(db, lease)
        except ValueError as exc:
            return json.dumps({"status": "error", "message": str(exc)})

        return json.dumps({
            "status": "ok",
            "message": f"Created lease {payload['lease_id']}.",
            "lease": payload,
        })


class LookupLeasesTool(Tool):
    """Filter leases by property/unit/tenant/active status. Read-only."""

    mode = ToolMode.READ_ONLY

    @property
    def name(self) -> str:
        return "lookup_leases"

    @property
    def description(self) -> str:
        return (
            "Search leases. Returns each lease's id, property + unit, "
            "tenant roster, dates, rent_amount, payment_status, and "
            "whether it's currently active. Use whenever you need a "
            "lease_id for update_lease / terminate_lease / "
            "add_tenant_to_lease — never guess. Filters: property_id, "
            "unit_id, tenant_id (external UUID), and active_only "
            "(default true) which keeps leases whose start_date <= today "
            "<= end_date."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "property_id": {"type": "string", "description": "Property UUID filter."},
                "unit_id": {"type": "string", "description": "Unit UUID filter."},
                "tenant_id": {"type": "string", "description": "Tenant external UUID filter (matches multi-tenant roster, not just primary tenant)."},
                "active_only": {
                    "type": "boolean",
                    "description": "When true (default), only leases active today.",
                },
                "limit": {"type": "integer", "description": "Max rows (default 100, capped at 200)."},
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        from services.lease_service import LeaseService

        with tool_session() as db:
            limit = max(1, min(int(kwargs.get("limit") or 100), 200))
            leases = LeaseService.list_leases(
                db,
                property_id=(kwargs.get("property_id") or "").strip() or None,
                unit_id=(kwargs.get("unit_id") or "").strip() or None,
                tenant_id=(kwargs.get("tenant_id") or "").strip() or None,
                active_only=kwargs.get("active_only", True),
                limit=limit,
            )
            payload = [LeaseService.lease_to_payload(db, lease) for lease in leases]
        if not payload:
            return json.dumps({"leases": [], "count": 0, "message": "No leases match the filters."})
        return json.dumps({"leases": payload, "count": len(payload)})


class UpdateLeaseTool(Tool):
    """Edit ``end_date`` / ``rent_amount`` / ``payment_status`` on a lease."""

    @property
    def name(self) -> str:
        return "update_lease"

    @property
    def description(self) -> str:
        return (
            "Patch editable fields on an existing lease. Only end_date "
            "(YYYY-MM-DD), rent_amount, and payment_status are mutable; "
            "start_date and the property/unit/tenant FKs are immutable — "
            "to relocate a lease, terminate the old one and create a new "
            "one. payment_status must be one of current|late|overdue."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["lease_id"],
            "properties": {
                "lease_id": {
                    "type": "string",
                    "description": (
                        "Real lease id returned by lookup_leases. Never invent "
                        "an id, never pass placeholder text like "
                        "'lease_id_from_context' — call lookup_leases first."
                    ),
                },
                "end_date": {"type": "string", "description": "New end date YYYY-MM-DD (must be >= start_date)."},
                "rent_amount": {"type": "number", "description": "New monthly rent in dollars."},
                "payment_status": {
                    "type": "string",
                    "enum": ["current", "late", "overdue"],
                    "description": "Payment status flag.",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        from services.lease_service import LeaseService

        lease_id = (kwargs.get("lease_id") or "").strip()
        if not lease_id:
            return json.dumps({"status": "error", "message": "lease_id is required."})
        bad = _reject_placeholder_id(lease_id, name="lease_id", lookup_tool="lookup_leases")
        if bad:
            return json.dumps({"status": "error", "message": bad})

        try:
            end_date = _parse_iso_date(kwargs.get("end_date"), field_name="end_date")
        except ValueError as exc:
            return json.dumps({"status": "error", "message": str(exc)})

        try:
            with tool_session() as db:
                lease = LeaseService.update_lease(
                    db,
                    lease_id=lease_id,
                    end_date=end_date,
                    rent_amount=kwargs.get("rent_amount"),
                    payment_status=kwargs.get("payment_status"),
                )
                payload = LeaseService.lease_to_payload(db, lease)
        except ValueError as exc:
            return json.dumps({"status": "error", "message": str(exc)})
        return json.dumps({"status": "ok", "message": f"Updated lease {lease_id}.", "lease": payload})


class TerminateLeaseTool(Tool):
    """End a lease early by setting ``end_date`` (defaults to today)."""

    @property
    def name(self) -> str:
        return "terminate_lease"

    @property
    def description(self) -> str:
        return (
            "End a lease early. Sets end_date to effective_date "
            "(YYYY-MM-DD, defaults to today). Preserves the lease row + "
            "tenant roster so historical context survives — for full "
            "deletion use the tenant deletion path. effective_date "
            "cannot precede the lease's start_date."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["lease_id"],
            "properties": {
                "lease_id": {"type": "string"},
                "effective_date": {
                    "type": "string",
                    "description": "YYYY-MM-DD (default: today).",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        from services.lease_service import LeaseService

        lease_id = (kwargs.get("lease_id") or "").strip()
        if not lease_id:
            return json.dumps({"status": "error", "message": "lease_id is required."})
        bad = _reject_placeholder_id(lease_id, name="lease_id", lookup_tool="lookup_leases")
        if bad:
            return json.dumps({"status": "error", "message": bad})
        try:
            effective_date = _parse_iso_date(kwargs.get("effective_date"), field_name="effective_date")
        except ValueError as exc:
            return json.dumps({"status": "error", "message": str(exc)})

        try:
            with tool_session() as db:
                lease = LeaseService.terminate_lease(
                    db, lease_id=lease_id, effective_date=effective_date,
                )
                payload = LeaseService.lease_to_payload(db, lease)
        except ValueError as exc:
            return json.dumps({"status": "error", "message": str(exc)})
        return json.dumps({"status": "ok", "message": f"Terminated lease {lease_id}.", "lease": payload})


class AddTenantToLeaseTool(Tool):
    """Add an existing tenant to a lease's multi-tenant roster."""

    @property
    def name(self) -> str:
        return "add_tenant_to_lease"

    @property
    def description(self) -> str:
        return (
            "Add an existing tenant to an existing lease's roster (the "
            "lease_tenants join table). Use when a co-tenant moves into "
            "an active lease without recreating it. tenant_id is the "
            "tenant's external UUID from lookup_tenants. Idempotent: "
            "adding the same tenant twice is a no-op."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["lease_id", "tenant_id"],
            "properties": {
                "lease_id": {"type": "string"},
                "tenant_id": {"type": "string", "description": "Tenant external UUID."},
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        from services.lease_service import LeaseService

        lease_id = (kwargs.get("lease_id") or "").strip()
        tenant_id = (kwargs.get("tenant_id") or "").strip()
        if not lease_id or not tenant_id:
            return json.dumps({"status": "error", "message": "lease_id and tenant_id are required."})
        for raw, name, lookup in (
            (lease_id, "lease_id", "lookup_leases"),
            (tenant_id, "tenant_id", "lookup_tenants"),
        ):
            bad = _reject_placeholder_id(raw, name=name, lookup_tool=lookup)
            if bad:
                return json.dumps({"status": "error", "message": bad})

        try:
            with tool_session() as db:
                lease = LeaseService.add_tenant_to_lease(
                    db, lease_id=lease_id, tenant_id=tenant_id,
                )
                payload = LeaseService.lease_to_payload(db, lease)
        except ValueError as exc:
            return json.dumps({"status": "error", "message": str(exc)})
        return json.dumps({
            "status": "ok",
            "message": f"Tenant {tenant_id} on lease {lease_id}.",
            "lease": payload,
        })


class RemoveTenantFromLeaseTool(Tool):
    """Remove a tenant from a lease's roster — never the last one."""

    @property
    def name(self) -> str:
        return "remove_tenant_from_lease"

    @property
    def description(self) -> str:
        return (
            "Remove a tenant from an existing lease's roster (the "
            "lease_tenants join table). Refuses to remove the last "
            "tenant on a lease — use terminate_lease instead. "
            "Idempotent: removing a tenant that isn't on the lease is a "
            "no-op."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["lease_id", "tenant_id"],
            "properties": {
                "lease_id": {"type": "string"},
                "tenant_id": {"type": "string", "description": "Tenant external UUID."},
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        from services.lease_service import LeaseService

        lease_id = (kwargs.get("lease_id") or "").strip()
        tenant_id = (kwargs.get("tenant_id") or "").strip()
        if not lease_id or not tenant_id:
            return json.dumps({"status": "error", "message": "lease_id and tenant_id are required."})
        for raw, name, lookup in (
            (lease_id, "lease_id", "lookup_leases"),
            (tenant_id, "tenant_id", "lookup_tenants"),
        ):
            bad = _reject_placeholder_id(raw, name=name, lookup_tool=lookup)
            if bad:
                return json.dumps({"status": "error", "message": bad})

        try:
            with tool_session() as db:
                lease = LeaseService.remove_tenant_from_lease(
                    db, lease_id=lease_id, tenant_id=tenant_id,
                )
                payload = LeaseService.lease_to_payload(db, lease)
        except ValueError as exc:
            return json.dumps({"status": "error", "message": str(exc)})
        return json.dumps({
            "status": "ok",
            "message": f"Removed tenant {tenant_id} from lease {lease_id}.",
            "lease": payload,
        })


__all__ = [
    "AddTenantToLeaseTool",
    "CreateLeaseTool",
    "LookupLeasesTool",
    "RemoveTenantFromLeaseTool",
    "TerminateLeaseTool",
    "UpdateLeaseTool",
]
