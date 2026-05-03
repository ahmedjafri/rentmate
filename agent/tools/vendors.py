"""Vendor tools: lookup and create."""
import json
from typing import Any

from agent.tools._common import Tool, ToolMode


class LookupVendorsTool(Tool):
    """Look up vendors in the system, optionally filtered by type."""

    mode = ToolMode.READ_ONLY

    @property
    def name(self) -> str:
        return "lookup_vendors"

    @property
    def description(self) -> str:
        return (
            "Search for vendors/contractors in the system. "
            "Returns a list of vendors with their external UUID, name, company, type, phone, and email. "
            "Optionally filter by vendor_type (e.g. 'Plumber', 'Electrician', 'Landscaper')."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "vendor_type": {
                    "type": "string",
                    "description": "Filter by vendor type (e.g. 'Plumber', 'Electrician'). Omit to list all vendors.",
                },
                "query": {
                    "type": "string",
                    "description": "Search by name or company (case-insensitive partial match). Omit to list all.",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        from db.models import User
        from db.session import SessionLocal

        vendor_type = kwargs.get("vendor_type")
        query = (kwargs.get("query") or "").strip().lower()

        db = SessionLocal.session_factory()
        try:
            vendors = db.query(User).filter_by(user_type="vendor").all()
            results = []
            for v in vendors:
                if vendor_type and (v.role_label or "").lower() != vendor_type.lower():
                    continue
                if query and query not in (v.name or "").lower() and query not in (v.company or "").lower():
                    continue
                results.append({
                    "id": str(v.external_id),
                    "name": v.name,
                    "company": v.company,
                    "vendor_type": v.role_label,
                    "phone": v.phone,
                    "email": v.email,
                })
            if not results:
                return json.dumps({"vendors": [], "message": "No vendors found matching the criteria."})
            return json.dumps({"vendors": results, "count": len(results)})
        finally:
            db.close()


class CreateVendorTool(Tool):
    """Create a new vendor/contractor in the system."""

    @property
    def name(self) -> str:
        return "create_vendor"

    @property
    def description(self) -> str:
        return (
            "Create a new vendor or contractor. Requires a name and phone number. "
            "Optionally include company, vendor_type (e.g. 'Plumber', 'Electrician', "
            "'HVAC', 'General Contractor', 'Handyman', 'Landscaper', 'Locksmith', "
            "'Roofer', 'Painter', 'Inspector'), and email."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["name", "phone", "vendor_type"],
            "properties": {
                "name": {"type": "string", "description": "Vendor's full name"},
                "phone": {"type": "string", "description": "Vendor's phone number"},
                "vendor_type": {"type": "string", "description": "Type of vendor (e.g. 'Plumber', 'Electrician', 'HVAC', 'General Contractor', 'Handyman', 'Landscaper', 'Locksmith', 'Roofer', 'Painter', 'Inspector')"},
                "company": {"type": "string", "description": "Company name (optional)"},
                "email": {"type": "string", "description": "Vendor's email address (optional)"},
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        from db.session import SessionLocal
        from gql.types import CreateVendorInput
        from services.vendor_service import VendorService

        db = SessionLocal.session_factory()
        try:
            vendor = VendorService.create_vendor(db, CreateVendorInput(
                name=kwargs["name"],
                phone=kwargs["phone"],
                company=kwargs.get("company"),
                vendor_type=kwargs.get("vendor_type"),
                email=kwargs.get("email"),
            ))
            return json.dumps({
                "status": "ok",
                "vendor_id": str(vendor.external_id),
                "name": vendor.name,
                "message": f"Vendor '{vendor.name}' created successfully.",
            })
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})
        finally:
            db.close()


__all__ = ["LookupVendorsTool", "CreateVendorTool"]
