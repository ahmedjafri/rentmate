"""Property and tenant creation tools."""
import json
import re
from typing import Any

from backends.local_auth import resolve_account_id, resolve_org_id
from db.models import MessageType

from llm.tools._common import Tool, _action_card_field, _queue_chat_message


_PLACEHOLDER_NAME_TOKENS = {
    "tenant",
    "unknown",
    "unnamed",
    "n/a",
    "na",
    "tbd",
    "missing",
}


def _looks_like_placeholder_person_name(first_name: str, last_name: str) -> bool:
    tokens = {
        token
        for token in re.split(r"[^a-z0-9]+", f"{first_name} {last_name}".strip().lower())
        if token
    }
    if not tokens:
        return True
    return bool(tokens) and tokens.issubset(_PLACEHOLDER_NAME_TOKENS)


class CreatePropertyTool(Tool):
    """Create a new property with optional units — used during onboarding or manual setup."""

    @property
    def name(self) -> str:
        return "create_property"

    @property
    def description(self) -> str:
        return (
            "Create a new property with optional units. Provide at minimum an address. "
            "Optionally specify property name, city, state, postal_code, property_type "
            "(single_family or multi_family), and unit_labels (list of strings like "
            "['1A', '1B', '2A']). If unit_count is provided instead of labels, units "
            "will be auto-labeled (Unit 1, Unit 2, etc.)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["address"],
            "properties": {
                "address": {"type": "string", "description": "Street address of the property"},
                "name": {"type": "string", "description": "Optional display name for the property"},
                "city": {"type": "string", "description": "City"},
                "state": {"type": "string", "description": "State abbreviation (e.g. WA, CA)"},
                "postal_code": {"type": "string", "description": "ZIP/postal code"},
                "property_type": {
                    "type": "string",
                    "enum": ["single_family", "multi_family"],
                    "description": "Property type (default: multi_family)",
                },
                "unit_labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Explicit unit labels like ['1A', '1B', '2A']",
                },
                "unit_count": {
                    "type": "integer",
                    "description": "Number of units (auto-labeled). Ignored if unit_labels provided.",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        from db.session import SessionLocal
        from gql.services.property_service import PropertyService
        from gql.services.settings_service import (
            get_onboarding_state,
            set_onboarding_path,
            update_onboarding_step,
        )
        from llm.tracing import log_trace

        address = kwargs["address"]
        unit_labels = kwargs.get("unit_labels")
        unit_count = kwargs.get("unit_count")

        # Auto-generate labels from count if no explicit labels
        if not unit_labels and unit_count and unit_count > 0:
            unit_labels = [f"Unit {i}" for i in range(1, unit_count + 1)]

        # Infer property type: single unit (or none specified) → single_family
        property_type = kwargs.get("property_type")
        if not property_type:
            effective_units = len(unit_labels) if unit_labels else (unit_count or 1)
            property_type = "single_family" if effective_units <= 1 else "multi_family"

        from llm.tools._common import tool_session
        try:
            with tool_session() as db:
                from sqlalchemy import func

                from db.models import Property as SqlProperty, Unit as SqlUnit

                normalized_address = address.strip()
                existing = (
                    db.query(SqlProperty)
                    .filter(
                        SqlProperty.org_id == resolve_org_id(),
                        SqlProperty.creator_id == resolve_account_id(),
                        func.lower(func.trim(SqlProperty.address_line1)) == normalized_address.lower(),
                    )
                    .first()
                )
                if existing:
                    existing_units = (
                        db.query(SqlUnit)
                        .filter(SqlUnit.property_id == existing.id)
                        .order_by(SqlUnit.created_at)
                        .all()
                    )
                    return json.dumps({
                        "status": "already_exists",
                        "property_id": str(existing.id),
                        "address": existing.address_line1,
                        "name": existing.name,
                        "units": [{"id": str(u.id), "label": u.label} for u in existing_units],
                        "message": f"Property at '{existing.address_line1}' already exists.",
                    })

                prop, units = PropertyService.create_property(
                    db,
                    address=address,
                    property_type=property_type,
                    name=kwargs.get("name"),
                    city=kwargs.get("city"),
                    state=kwargs.get("state"),
                    postal_code=kwargs.get("postal_code"),
                    unit_labels=unit_labels,
                )

                # Update onboarding progress if active
                onboarding = get_onboarding_state(db)
                if onboarding and onboarding.get("status") == "active":
                    update_onboarding_step(db, step="add_property")
                    if not onboarding.get("path_picked"):
                        set_onboarding_path(db, path="manual")
                    log_trace(
                        "onboarding", "tool", "First property created",
                        tool_name="create_property",
                        detail={"property_id": prop.id, "address": address},
                    )

                # Snapshot values before commit expires the instances.
                prop_id = str(prop.id)
                prop_address = prop.address_line1
                prop_name = prop.name
                unit_data = [{"id": str(u.id), "label": u.label} for u in units]

            fields = [
                field
                for field in [
                    _action_card_field("Address", prop_address),
                    _action_card_field("Type", "Single family" if property_type == "single_family" else "Multi-family"),
                    _action_card_field("Created units", len(unit_data)),
                ]
                if field
            ]
            _queue_chat_message(
                body=f"Created property {prop_name or prop_address}",
                message_type=MessageType.ACTION,
                action_card={
                    "kind": "property",
                    "title": prop_name or prop_address,
                    "summary": f"Created property at {prop_address}" if not prop_name else f"Created property at {prop_address}.",
                    "fields": fields,
                    "links": [{
                        "label": "Open property",
                        "entity_type": "property",
                        "entity_id": prop_id,
                    }],
                    "units": [
                        {"uid": u["id"], "label": u["label"], "property_id": prop_id}
                        for u in unit_data
                    ],
                },
            )

            unit_str = ", ".join(u["label"] for u in unit_data) if unit_data else "none"
            return json.dumps({
                "status": "ok",
                "property_id": prop_id,
                "address": prop_address,
                "name": prop_name,
                "units": unit_data,
                "message": f"Created property '{prop_name or prop_address}' with {len(unit_data)} unit(s): {unit_str}",
            })
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})


class CreateTenantTool(Tool):
    """Create a tenant and optionally a lease for them."""

    @property
    def name(self) -> str:
        return "create_tenant"

    @property
    def description(self) -> str:
        return (
            "Create a new tenant. Pass whatever information you have — only "
            "first_name and last_name are required. If property_id and unit_id "
            "are provided the tenant is linked to that unit. If lease dates and "
            "rent are also provided a full lease record is created. Any extra "
            "context (e.g. pet policy, move-in notes, partial lease details) "
            "can be passed in the 'notes' field and saved to the tenant's "
            "permanent context."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["first_name", "last_name"],
            "properties": {
                "first_name": {"type": "string", "description": "Tenant first name"},
                "last_name": {"type": "string", "description": "Tenant last name"},
                "email": {"type": "string", "description": "Email address"},
                "phone": {"type": "string", "description": "Phone number"},
                "property_id": {"type": "string", "description": "Property ID to attach lease to"},
                "unit_id": {"type": "string", "description": "Unit ID within the property"},
                "lease_start": {"type": "string", "description": "Lease start date (YYYY-MM-DD)"},
                "lease_end": {"type": "string", "description": "Lease end date (YYYY-MM-DD)"},
                "rent_amount": {"type": "number", "description": "Monthly rent amount"},
                "notes": {"type": "string", "description": "Context or notes about this tenant to save permanently"},
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        from db.models import Tenant as SqlTenant, User
        from db.models.account import create_shadow_user
        from db.session import SessionLocal

        first_name = kwargs["first_name"]
        last_name = kwargs["last_name"]

        if _looks_like_placeholder_person_name(first_name, last_name):
            return json.dumps({
                "status": "error",
                "message": (
                    "Tenant name is required before creating a tenant record. "
                    "Ask the user for the tenant's full name first. A phone number is helpful but optional."
                ),
            })

        from llm.tools._common import tool_session
        try:
            with tool_session() as db:
                import uuid
                from datetime import UTC, datetime

                # Check for existing tenant by name
                from sqlalchemy import func
                existing = (
                    db.query(SqlTenant)
                    .join(User, SqlTenant.user_id == User.id)
                    .filter(
                        func.lower(User.first_name) == first_name.lower(),
                        func.lower(User.last_name) == last_name.lower(),
                    )
                    .first()
                )
                if existing:
                    return json.dumps({
                        "status": "already_exists",
                        "tenant_id": str(existing.external_id),
                        "message": f"Tenant {first_name} {last_name} already exists.",
                    })

                # Always create the tenant first
                shadow_user = create_shadow_user(
                    db,
                    org_id=1,
                    creator_id=resolve_account_id(),
                    first_name=first_name,
                    last_name=last_name,
                    email=kwargs.get("email"),
                    phone=kwargs.get("phone"),
                    user_type="tenant",
                )
                tenant = SqlTenant(
                    creator_id=resolve_account_id(),
                    user_id=shadow_user.id,
                    created_at=datetime.now(UTC),
                )
                db.add(tenant)
                db.flush()

                result: dict[str, Any] = {
                    "status": "ok",
                    "tenant_id": str(tenant.external_id),
                    "message": f"Created tenant {first_name} {last_name}.",
                }

                # Link tenant to unit if property_id + unit_id provided
                property_row = None
                unit = None
                if kwargs.get("property_id") and kwargs.get("unit_id"):
                    from sqlalchemy import select

                    from db.models import Property as SqlProperty, Unit as SqlUnit
                    property_row = db.execute(
                        select(SqlProperty).where(SqlProperty.id == kwargs["property_id"])
                    ).scalar_one_or_none()
                    unit = db.execute(
                        select(SqlUnit).where(
                            SqlUnit.id == kwargs["unit_id"],
                            SqlUnit.property_id == kwargs["property_id"],
                        )
                    ).scalar_one_or_none()
                    if unit:
                        unit.tenant_id = tenant.id
                        result["unit_label"] = unit.label
                        result["message"] = f"Created tenant {first_name} {last_name} on {unit.label}."

                # Create lease if we have enough detail (dates required)
                if unit and kwargs.get("lease_start") and kwargs.get("lease_end"):
                    from datetime import date as _date

                    from db.models import Lease as SqlLease
                    lease = SqlLease(
                        id=str(uuid.uuid4()),
                        creator_id=resolve_account_id(),
                        tenant_id=tenant.id,
                        unit_id=unit.id,
                        property_id=kwargs["property_id"],
                        start_date=_date.fromisoformat(kwargs["lease_start"]),
                        end_date=_date.fromisoformat(kwargs["lease_end"]),
                        rent_amount=kwargs.get("rent_amount", 0),
                        payment_status="current",
                        created_at=datetime.now(UTC),
                    )
                    db.add(lease)
                    result["lease_id"] = str(lease.id)
                    result["message"] = f"Created tenant {first_name} {last_name} with lease on {unit.label}."

                # Capture any partial lease info that didn't make it into a record
                partial_bits = []
                if kwargs.get("rent_amount") and "lease_id" not in result:
                    partial_bits.append(f"rent ${kwargs['rent_amount']}/mo")
                if kwargs.get("lease_start") and "lease_id" not in result:
                    partial_bits.append(f"start {kwargs['lease_start']}")
                if kwargs.get("lease_end") and "lease_id" not in result:
                    partial_bits.append(f"end {kwargs['lease_end']}")

                # Combine explicit notes + partial lease info into tenant context
                context_parts = []
                notes = kwargs.get("notes", "").strip()
                if notes:
                    context_parts.append(notes)
                if partial_bits:
                    context_parts.append(f"Lease info (partial): {', '.join(partial_bits)}")

                if context_parts:
                    from sqlalchemy.orm.attributes import flag_modified
                    new_ctx = "\n".join(context_parts)
                    existing_ctx = tenant.context or ""
                    tenant.context = (existing_ctx + "\n" + new_ctx).strip() if existing_ctx else new_ctx
                    flag_modified(tenant, "context")

                # Snapshot values before session exit (commit will expire instances).
                snapshot = {
                    "shadow_email": shadow_user.email,
                    "shadow_phone": shadow_user.phone,
                    "property_label": (property_row.name or property_row.address_line1) if property_row else None,
                    "property_id": str(property_row.id) if property_row else None,
                    "unit_label": unit.label if unit else None,
                    "unit_id": str(unit.id) if unit else None,
                    "unit_property_id": str(unit.property_id) if unit else None,
                    "tenant_external_id": str(tenant.external_id),
                }

            fields = [
                field
                for field in [
                    _action_card_field("Email", snapshot["shadow_email"]),
                    _action_card_field("Phone", snapshot["shadow_phone"]),
                    _action_card_field("Property", snapshot["property_label"]),
                    _action_card_field("Unit", snapshot["unit_label"]),
                    _action_card_field("Lease start", kwargs.get("lease_start")),
                    _action_card_field("Lease end", kwargs.get("lease_end")),
                    _action_card_field("Rent", f"${kwargs['rent_amount']}/mo" if kwargs.get("rent_amount") else None),
                ]
                if field
            ]
            links = [{
                "label": "Open tenant",
                "entity_type": "tenant",
                "entity_id": snapshot["tenant_external_id"],
            }]
            if snapshot["property_id"]:
                links.append({
                    "label": "Property",
                    "entity_type": "property",
                    "entity_id": snapshot["property_id"],
                })
            if snapshot["unit_id"]:
                links.append({
                    "label": "Unit",
                    "entity_type": "unit",
                    "entity_id": snapshot["unit_id"],
                    "property_id": snapshot["unit_property_id"],
                })
            _queue_chat_message(
                body=result["message"],
                message_type=MessageType.ACTION,
                action_card={
                    "kind": "tenant",
                    "title": f"{first_name} {last_name}",
                    "summary": result["message"],
                    "fields": fields,
                    "links": links,
                    "units": [],
                },
            )
            return json.dumps(result)
        except Exception as e:
            import traceback
            print(f"[create_tenant] ERROR: {e}")
            traceback.print_exc()
            return json.dumps({"status": "error", "message": str(e)})


__all__ = ["CreatePropertyTool", "CreateTenantTool"]
