import os
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from db.models import Lease, MessageType, Property, Task, Tenant, Unit


def load_account_context(db: Session) -> str:
    account_name = os.environ.get("RENTMATE_ACCOUNT_NAME", "RentMate")
    properties = db.query(Property).all()
    today = date.today()
    active_leases = db.query(Lease).filter(Lease.end_date >= today).all()

    lines = [f"Account: {account_name}"]

    if properties:
        lines.append("Properties:")
        for prop in properties:
            parts = [prop.address_line1, prop.city, prop.state, prop.postal_code]
            addr = ", ".join(p for p in parts if p)
            label = prop.name or addr
            lines.append(f"  - {label} ({addr}) [id: {prop.id}]")

    if active_leases:
        lines.append("Active Leases:")
        for lease in active_leases:
            tenant = lease.tenant
            unit = lease.unit
            prop = lease.property
            if not tenant or not unit:
                continue
            name = f"{tenant.first_name} {tenant.last_name}".strip()
            phone = tenant.phone or "no phone"
            email = tenant.email or "no email"
            prop_label = prop.name if prop else "?"
            start = lease.start_date.strftime("%Y-%m-%d") if lease.start_date else "?"
            end = lease.end_date.strftime("%Y-%m-%d") if lease.end_date else "?"
            rent = f"${lease.rent_amount:,.0f}/mo" if lease.rent_amount else "?"
            status = lease.payment_status or "current"
            lines.append(
                f"  - {name} | {phone} | {email} | {prop_label} {unit.label} "
                f"| {start}–{end} | {rent} | payment: {status}"
            )

    return "\n".join(lines)


def _append_lease_context(lines: list[str], lease: Lease) -> None:
    """Append tenant and lease details to context lines."""
    tenant = lease.tenant
    if tenant:
        name = f"{tenant.first_name} {tenant.last_name}".strip()
        phone = tenant.phone or "no phone"
        email = tenant.email or "no email"
        lines.append(f"Current Tenant: {name} | {phone} | {email}")
        lines.append(f"Tenant ID: {tenant.id}")
    start = lease.start_date.strftime("%Y-%m-%d") if lease.start_date else "?"
    end = lease.end_date.strftime("%Y-%m-%d") if lease.end_date else "?"
    rent = f"${lease.rent_amount:,.0f}/mo" if lease.rent_amount else "?"
    lines.append(
        f"Lease: {start} to {end} | {rent} | payment: {lease.payment_status or 'current'}"
    )


def build_task_context(db: Session, task_id: str) -> str:
    """
    Build a rich context string for a task, including
    task details, property, unit, current tenant, and account overview.
    task_id may be a Task.id or a Conversation.id linked to a task.
    """
    task = db.query(Task).filter_by(id=task_id).first()
    if not task:
        return load_account_context(db)

    lines = [
        f"Task ID: {task.id}",
        f"Task: {task.title}",
        f"Category: {task.category or 'general'}",
        f"Urgency: {task.urgency or 'normal'}",
        f"Status: {task.task_status or 'active'}",
        f"Mode: {task.task_mode or 'manual'}",
    ]

    # Task description (first context message from the AI conversation)
    ai_convo = task.ai_conversation
    all_msgs = list(ai_convo.messages) if ai_convo else []
    context_msgs = [m for m in all_msgs if m.message_type == MessageType.CONTEXT]
    if context_msgs:
        lines.append(f"Description: {context_msgs[0].body}")

    # Task-scoped notes (quotes, findings, scheduling)
    if task.context:
        lines.append(f"\nTask notes:\n{task.context}")

    # Property context (include ID so the agent can use it for save_memory)
    prop: Optional[Property] = None
    unit_obj: Optional[Unit] = None
    tenant_obj: Optional[Tenant] = None
    vendor_obj = None

    if task.property_id:
        prop = db.query(Property).filter_by(id=task.property_id).first()
        if prop:
            parts = [prop.address_line1, prop.city, prop.state, prop.postal_code]
            addr = ", ".join(p for p in parts if p)
            lines.append(f"Property: {prop.name or addr} ({addr})")
            lines.append(f"Property ID: {prop.id}")

    # Unit + tenant + lease context
    today = date.today()
    if task.unit_id:
        unit_obj = db.query(Unit).filter_by(id=task.unit_id).first()
        if unit_obj:
            lines.append(f"Unit: {unit_obj.label}")
            lines.append(f"Unit ID: {unit_obj.id}")
            active = [l for l in unit_obj.leases if l.end_date >= today]
            if active:
                tenant_obj = active[0].tenant
                _append_lease_context(lines, active[0])
            else:
                lines.append("Unit is currently vacant.")
    elif task.property_id:
        # No unit set — find tenants via property's active leases
        active = db.query(Lease).filter(
            Lease.property_id == task.property_id,
            Lease.end_date >= today,
        ).all()
        if active:
            lease = active[0]
            tenant_obj = lease.tenant
            if lease.unit_id:
                unit_obj = db.query(Unit).filter_by(id=lease.unit_id).first()
                if unit_obj:
                    lines.append(f"Unit: {unit_obj.label}")
                    lines.append(f"Unit ID: {unit_obj.id}")
            _append_lease_context(lines, lease)

    # Vendor context (from AI conversation extra)
    ai_convo = task.ai_conversation
    if ai_convo:
        extra = ai_convo.extra or {}
        vendor_id = extra.get("assigned_vendor_id")
        vendor_name = extra.get("assigned_vendor_name")
        if vendor_id:
            from db.models import ExternalContact
            vendor_obj = db.query(ExternalContact).filter_by(id=vendor_id).first()
            if vendor_obj:
                lines.append(f"Assigned Vendor: {vendor_obj.name} | {vendor_obj.phone or 'no phone'} | {vendor_obj.email or 'no email'}")
                lines.append(f"Vendor ID: {vendor_obj.id}")
            elif vendor_name:
                lines.append(f"Assigned Vendor: {vendor_name}")
                lines.append(f"Vendor ID: {vendor_id}")

    # Entity context notes (agent memory saved via save_memory tool)
    context_notes: list[str] = []
    if prop and prop.context:
        context_notes.append(f"Property notes: {prop.context}")
    if unit_obj and unit_obj.context:
        context_notes.append(f"Unit notes: {unit_obj.context}")
    if tenant_obj and tenant_obj.context:
        context_notes.append(f"Tenant notes: {tenant_obj.context}")
    if vendor_obj and getattr(vendor_obj, 'context', None):
        context_notes.append(f"Vendor notes: {vendor_obj.context}")
    if context_notes:
        lines.append("")
        lines.extend(context_notes)

    # Pending suggestions — so the agent knows what's already queued
    from db.models import Suggestion
    pending_suggestions = db.query(Suggestion).filter(
        Suggestion.task_id == task.id,
        Suggestion.status == "pending",
    ).all()
    if pending_suggestions:
        lines.append("")
        lines.append("Pending suggestions (already queued, do NOT duplicate):")
        for s in pending_suggestions:
            action = (s.action_payload or {}).get("action", "unknown")
            draft = (s.action_payload or {}).get("draft_message", "")
            entry = f"  - [{action}] {s.title or 'untitled'}"
            if draft:
                entry += f" — draft: {draft[:80]}"
            lines.append(entry)

    # Only include the full account overview if the task doesn't already
    # have property context — avoids dumping all properties when only one
    # is relevant.
    if not task.property_id:
        lines.append("")
        lines.append(load_account_context(db))
    else:
        account_name = os.environ.get("RENTMATE_ACCOUNT_NAME", "RentMate")
        lines.append(f"\nAccount: {account_name}")

    return "\n".join(lines)


def build_vendor_safe_context(db: Session, task_id: str) -> str:
    """Build context for vendor-facing communications with tenant PII stripped.

    Includes only: property address, unit label, task details, category, urgency.
    Excludes: tenant names, emails, phones, lease dates, rent, payment status.
    """
    task = db.query(Task).filter_by(id=task_id).first()
    if not task:
        return ""

    lines = [
        f"Task: {task.title}",
        f"Category: {task.category or 'general'}",
        f"Urgency: {task.urgency or 'normal'}",
    ]

    ai_convo = task.ai_conversation
    all_msgs = list(ai_convo.messages) if ai_convo else []
    context_msgs = [m for m in all_msgs if m.message_type == MessageType.CONTEXT]
    if context_msgs:
        lines.append(f"Description: {context_msgs[0].body}")

    if task.property_id:
        prop = db.query(Property).filter_by(id=task.property_id).first()
        if prop:
            parts = [prop.address_line1, prop.city, prop.state, prop.postal_code]
            addr = ", ".join(p for p in parts if p)
            lines.append(f"Property: {addr}")

    if task.unit_id:
        unit = db.query(Unit).filter_by(id=task.unit_id).first()
        if unit:
            lines.append(f"Unit: {unit.label}")

    return "\n".join(lines)
