import os
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from db.models import Task, Lease, MessageType, Property, Unit, Tenant


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
    ]

    # Task description (first context message from the AI conversation)
    ai_convo = task.ai_conversation
    all_msgs = list(ai_convo.messages) if ai_convo else []
    context_msgs = [m for m in all_msgs if m.message_type == MessageType.CONTEXT]
    if context_msgs:
        lines.append(f"Description: {context_msgs[0].body}")

    # Property context (include ID so the agent can use it for save_memory)
    prop: Optional[Property] = None
    if task.property_id:
        prop = db.query(Property).filter_by(id=task.property_id).first()
        if prop:
            parts = [prop.address_line1, prop.city, prop.state, prop.postal_code]
            addr = ", ".join(p for p in parts if p)
            lines.append(f"Property: {prop.name or addr} ({addr})")
            lines.append(f"Property ID: {prop.id}")

    # Unit + tenant + lease context
    if task.unit_id:
        unit = db.query(Unit).filter_by(id=task.unit_id).first()
        if unit:
            lines.append(f"Unit: {unit.label}")
            lines.append(f"Unit ID: {unit.id}")
            today = date.today()
            active = [l for l in unit.leases if l.end_date >= today]
            if active:
                lease = active[0]
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
            else:
                lines.append("Unit is currently vacant.")

    lines.append("")
    lines.append(load_account_context(db))
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
