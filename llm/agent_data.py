#!/usr/bin/env python3
"""
agent_data.py — Structured data access for RentMate agents.

Replaces raw-SQL agent_query.py with predefined, high-level operations that
go through the same ORM query layer as the application, preserving all
business-logic invariants (eager loading, derived fields, relationship traversal).

Usage:
    python agent_data.py <operation> [options]

Operations:
    properties                         All properties with units, occupancy, leases
    tenants                            All tenants with active lease info
    leases                             All leases with tenant and property
    tasks [--category X] [--status X]  Task list with optional filters
    task --id <uid>                    Single task with full message thread
    messages --id <uid>                Messages for a conversation/SMS thread
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

# Add project root so db/ can be imported
_root = Path(__file__).parent.parent
sys.path.insert(0, str(_root))

from db.models import ParticipantType  # noqa: E402
from db.queries import (  # noqa: E402
    fetch_leases,
    fetch_messages,
    fetch_properties,
    fetch_task,
    fetch_tasks,
    fetch_tenants,
    format_address,
    tenant_display_name,
)
from db.session import SessionLocal  # noqa: E402

# ---------------------------------------------------------------------------
# DB session (mirrors handlers/deps.py without pulling in FastAPI deps)
# ---------------------------------------------------------------------------

def _make_session():
    return SessionLocal()


# ---------------------------------------------------------------------------
# Serializers — convert ORM objects to agent-friendly dicts
# ---------------------------------------------------------------------------

def _public_id(entity) -> str:
    external_id = getattr(entity, "external_id", None)
    if isinstance(external_id, str) and external_id:
        return external_id
    return str(entity.id)

def _serialize_properties(props) -> list:
    today = date.today()
    results = []
    for p in props:
        active_unit_ids: set = set()
        monthly_revenue = 0.0
        leases = []
        for l in p.leases:
            is_active = bool(l.end_date and l.end_date >= today)
            if is_active:
                monthly_revenue += l.rent_amount or 0.0
                if l.unit_id:
                    active_unit_ids.add(l.unit_id)
            leases.append({
                "id": str(l.id),
                "tenant": tenant_display_name(l.tenant) if l.tenant else None,
                "tenant_id": _public_id(l.tenant) if l.tenant else None,
                "unit": l.unit.label if l.unit else None,
                "start_date": str(l.start_date),
                "end_date": str(l.end_date),
                "rent_amount": l.rent_amount,
                "is_active": is_active,
            })
        results.append({
            "id": str(p.id),
            "name": p.name or "",
            "address": format_address(p),
            "property_type": p.property_type,
            "units": [
                {"id": str(u.id), "label": u.label, "occupied": u.id in active_unit_ids}
                for u in p.units
            ],
            "total_units": len(p.units),
            "occupied_units": len(active_unit_ids),
            "monthly_revenue": monthly_revenue,
            "leases": leases,
        })
    return results


def _serialize_tenants(tenants) -> list:
    today = date.today()
    results = []
    for t in tenants:
        active_lease = next(
            (l for l in t.leases if l.end_date and l.end_date >= today),
            t.leases[0] if t.leases else None,
        )
        is_active = bool(active_lease and active_lease.end_date and active_lease.end_date >= today)
        results.append({
            "id": _public_id(t),
            "name": tenant_display_name(t),
            "email": t.email,
            "phone": t.phone,
            "is_active": is_active,
            "unit": active_lease.unit.label if active_lease and active_lease.unit else None,
            "property": active_lease.property.name if active_lease and active_lease.property else None,
            "property_id": str(active_lease.property_id) if active_lease and active_lease.property_id else None,
            "lease_end_date": str(active_lease.end_date) if active_lease else None,
            "rent_amount": active_lease.rent_amount if active_lease else None,
        })
    return results


def _serialize_leases(leases) -> list:
    today = date.today()
    return [
        {
            "id": str(l.id),
            "tenant": tenant_display_name(l.tenant) if l.tenant else None,
            "tenant_id": _public_id(l.tenant) if l.tenant else None,
            "property": l.property.name if l.property else None,
            "property_id": str(l.property_id) if l.property_id else None,
            "unit": l.unit.label if l.unit else None,
            "unit_id": str(l.unit_id) if l.unit_id else None,
            "start_date": str(l.start_date),
            "end_date": str(l.end_date),
            "rent_amount": l.rent_amount,
            "payment_status": l.payment_status,
            "is_active": bool(l.end_date and l.end_date >= today),
        }
        for l in leases
    ]


def _task_tenant_and_unit(c):
    tenant_name = None
    if c.lease and c.lease.tenant:
        tenant_name = tenant_display_name(c.lease.tenant)
    unit_label = None
    if c.unit:
        unit_label = c.unit.label
    elif c.lease and c.lease.unit:
        unit_label = c.lease.unit.label
    return tenant_name, unit_label


def _serialize_tasks(conversations) -> list:
    results = []
    for c in conversations:
        tenant_name, unit_label = _task_tenant_and_unit(c)
        results.append({
            "id": str(c.id),
            "title": c.subject,
            "status": c.task_status,
            "category": c.category,
            "urgency": c.urgency,
            "priority": c.priority,
            "source": c.source,
            "tenant_name": tenant_name,
            "unit_label": unit_label,
            "property_id": str(c.property_id) if c.property_id else None,
            "created_at": str(c.created_at),
            "last_message_at": str(c.last_message_at) if c.last_message_at else None,
        })
    return results


def _serialize_task(c) -> dict:
    if c is None:
        return {"error": "Task not found"}
    tenant_name, unit_label = _task_tenant_and_unit(c)
    vendor_msgs = [m for m in c.messages if m.sender_type == ParticipantType.EXTERNAL_CONTACT and m.sender_name]
    return {
        "id": str(c.id),
        "title": c.subject,
        "status": c.task_status,
        "category": c.category,
        "urgency": c.urgency,
        "priority": c.priority,
        "source": c.source,
        "tenant_name": tenant_name,
        "unit_label": unit_label,
        "vendor_assigned": vendor_msgs[0].sender_name if vendor_msgs else None,
        "property_id": str(c.property_id) if c.property_id else None,
        "created_at": str(c.created_at),
        "messages": [
            {
                "id": str(m.id),
                "body": m.body,
                "type": m.message_type,
                "sender": m.sender_name,
                "is_ai": m.is_ai,
                "sent_at": str(m.sent_at),
            }
            for m in sorted(c.messages, key=lambda m: m.sent_at)
        ],
    }


def _serialize_messages(msgs, conversation_id: str):
    if not msgs:
        return {"error": f"No messages found for conversation {conversation_id!r}"}
    return [
        {
            "id": str(m.id),
            "body": m.body,
            "type": m.message_type,
            "sender": m.sender_name,
            "is_ai": m.is_ai,
            "sent_at": str(m.sent_at),
        }
        for m in msgs
    ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="RentMate structured data access for agents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "operation",
        choices=["properties", "tenants", "leases", "tasks", "task", "messages"],
    )
    parser.add_argument("--id", help="Resource ID (required for 'task' and 'messages')")
    parser.add_argument("--category", help="Filter tasks by category (e.g. maintenance, lease)")
    parser.add_argument("--status", help="Filter tasks by status, comma-separated (e.g. open,in_progress)")
    args = parser.parse_args()

    db = _make_session()
    try:
        op = args.operation
        if op == "properties":
            result = _serialize_properties(fetch_properties(db))
        elif op == "tenants":
            result = _serialize_tenants(fetch_tenants(db))
        elif op == "leases":
            result = _serialize_leases(fetch_leases(db))
        elif op == "tasks":
            result = _serialize_tasks(fetch_tasks(db, category=args.category, status=args.status))
        elif op == "task":
            if not args.id:
                result = {"error": "'task' operation requires --id <uid>"}
            else:
                result = _serialize_task(fetch_task(db, args.id))
        elif op == "messages":
            if not args.id:
                result = {"error": "'messages' operation requires --id <conversation-id>"}
            else:
                result = _serialize_messages(fetch_messages(db, args.id), args.id)
        else:
            result = {"error": f"Unknown operation: {op!r}"}
    except Exception as exc:
        result = {"error": str(exc)}
    finally:
        db.close()

    print(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
