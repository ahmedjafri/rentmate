# gql/types.py
import strawberry
import typing
from datetime import date as _date, datetime as _datetime
from db.models import MessageType
from db.enums import (  # noqa: F401 — re-exported
    TaskCategory, Urgency, TaskSource,
    AutomationSource, AgentSource, SuggestionSource, SuggestionOption,
)


def _utc_iso(dt: _datetime | None) -> str:
    """Format a naive-UTC datetime as an ISO 8601 string with Z suffix."""
    if dt is None:
        return ""
    return dt.isoformat() + "Z"


# ---------------------------------------------------------------------------
# Input types
# ---------------------------------------------------------------------------

@strawberry.type
class UserType:
    uid: str
    username: str
    role: typing.Optional[str] = None

@strawberry.input
class LoginInput:
    password: str
    username: typing.Optional[str] = None

@strawberry.type
class AuthPayload:
    token: str
    user: UserType

@strawberry.input
class HouseInput:
    name: str
    address: str

@strawberry.input
class TenantInput:
    name: str

@strawberry.input
class LeaseInput:
    tenant_uid: str
    house_uid: str
    start_date: str
    end_date: str
    rent_amount: float

@strawberry.input
class UpdatePropertyInput:
    uid: str
    name: typing.Optional[str] = None
    address: typing.Optional[str] = None
    property_type: typing.Optional[str] = None

@strawberry.input
class CreateTaskInput:
    title: str
    source: str
    task_status: str = "active"
    category: typing.Optional[str] = None
    urgency: typing.Optional[str] = None
    priority: typing.Optional[str] = None
    task_mode: typing.Optional[str] = None
    property_id: typing.Optional[str] = None
    unit_id: typing.Optional[str] = None
    confidential: bool = False

@strawberry.input
class SpawnTaskInput:
    parent_conversation_id: str
    objective: str
    category: typing.Optional[str] = None
    urgency: typing.Optional[str] = None
    priority: typing.Optional[str] = None
    task_mode: str = "autonomous"
    source: str = "manual"

@strawberry.input
class AddDocumentTagInput:
    document_id: str
    tag_type: str
    property_id: typing.Optional[str] = None
    unit_id: typing.Optional[str] = None
    tenant_id: typing.Optional[str] = None

@strawberry.input
class SendMessageInput:
    conversation_id: str
    body: str
    message_type: str = MessageType.MESSAGE
    sender_name: str = "You"
    is_ai: bool = False
    draft_reply: typing.Optional[str] = None

@strawberry.input
class UpdateTaskInput:
    uid: str
    task_mode: typing.Optional[str] = None
    task_status: typing.Optional[str] = None

@strawberry.input
class CreateTenantWithLeaseInput:
    first_name: str
    last_name: str
    property_id: str
    unit_id: str
    lease_start: str   # YYYY-MM-DD
    lease_end: str     # YYYY-MM-DD
    rent_amount: float
    email: typing.Optional[str] = None
    phone: typing.Optional[str] = None

@strawberry.input
class CreatePropertyInput:
    address: str
    property_type: str = "multi_family"
    name: typing.Optional[str] = None
    city: typing.Optional[str] = None
    state: typing.Optional[str] = None
    postal_code: typing.Optional[str] = None
    unit_labels: typing.Optional[typing.List[str]] = None

@strawberry.input
class UpdateUnitNotesInput:
    uid: str
    notes: typing.Optional[str] = None


@strawberry.input
class AddLeaseForTenantInput:
    tenant_id: str
    property_id: str
    unit_id: str
    lease_start: str   # YYYY-MM-DD
    lease_end: str     # YYYY-MM-DD
    rent_amount: float


# ---------------------------------------------------------------------------
# Return types (with from_sql converters)
# ---------------------------------------------------------------------------

@strawberry.type
class ExtraPropertyType:
    key: str
    value: str


@strawberry.type
class UnitType:
    uid: str
    label: str
    is_occupied: bool = False
    notes: typing.Optional[str] = None
    tenant_name: typing.Optional[str] = None
    lease_end_date: typing.Optional[str] = None
    pending_task_count: int = 0


@strawberry.type
class HouseType:
    uid: str
    name: str
    address: str
    property_type: typing.Optional[str] = None
    source: typing.Optional[str] = None
    units: typing.Optional[int] = None
    occupied_units: typing.Optional[int] = None
    monthly_revenue: typing.Optional[float] = None
    unit_list: typing.List[UnitType] = strawberry.field(default_factory=list)
    tenants: typing.List["TenantType"] = strawberry.field(default_factory=list)
    leases: typing.List["LeaseType"] = strawberry.field(default_factory=list)

    @classmethod
    def from_new(cls, p: typing.Any, units: list) -> "HouseType":
        from db.queries import format_address
        return cls(
            uid=str(p.id),
            name=p.name or "",
            address=format_address(p),
            property_type=p.property_type,
            source=p.source,
            units=len(units),
            occupied_units=0,
            monthly_revenue=0.0,
            unit_list=[UnitType(uid=str(u.id), label=u.label, is_occupied=False, notes=u.notes) for u in units],
        )

    @classmethod
    def from_sql(cls, p: typing.Any, today: _date) -> "HouseType":
        from db.queries import format_address, tenant_display_name
        tenant_map: dict = {}
        lease_items: list = []
        active_unit_ids: set = set()
        monthly_revenue = 0.0

        for l in p.leases:
            t = l.tenant
            is_active = l.end_date >= today if l.end_date else False
            if t:
                t_key = str(t.id)
                if t_key not in tenant_map:
                    tenant_map[t_key] = TenantType(uid=str(t.id), name=tenant_display_name(t))
                if is_active and l.unit_id:
                    active_unit_ids.add(l.unit_id)
            if is_active:
                monthly_revenue += l.rent_amount or 0.0
            lease_items.append(LeaseType.from_sql(l))

        # Build per-unit tenant name and lease end date from active leases
        unit_tenant: dict = {}   # unit_id -> tenant display name
        unit_lease_end: dict = {}  # unit_id -> lease end date str
        for l in p.leases:
            is_active = l.end_date >= today if l.end_date else False
            if is_active and l.unit_id and l.tenant:
                unit_tenant[l.unit_id] = tenant_display_name(l.tenant)
                unit_lease_end[l.unit_id] = str(l.end_date)

        # Count pending tasks per unit (passed in via _unit_task_counts if available)
        unit_task_counts: dict = getattr(p, '_unit_task_counts', {})

        unit_list = [
            UnitType(
                uid=str(u.id),
                label=u.label,
                is_occupied=u.id in active_unit_ids,
                notes=u.notes,
                tenant_name=unit_tenant.get(u.id),
                lease_end_date=unit_lease_end.get(u.id),
                pending_task_count=unit_task_counts.get(u.id, 0),
            )
            for u in p.units
        ]
        return cls(
            uid=str(p.id),
            name=p.name or "",
            address=format_address(p),
            property_type=p.property_type or "multi_family",
            source=p.source or "manual",
            units=len(p.units),
            occupied_units=len(active_unit_ids),
            monthly_revenue=monthly_revenue,
            unit_list=unit_list,
            tenants=list(tenant_map.values()),
            leases=lease_items,
        )


@strawberry.type
class TenantType:
    uid: str
    name: str
    email: typing.Optional[str] = None
    unit_label: typing.Optional[str] = None
    lease_end_date: typing.Optional[str] = None
    rent_amount: typing.Optional[float] = None
    payment_status: typing.Optional[str] = None
    is_active: bool = False
    rents: typing.List[HouseType] = strawberry.field(default_factory=list)
    leases: typing.List["LeaseType"] = strawberry.field(default_factory=list)
    extra_properties: typing.List[ExtraPropertyType] = strawberry.field(default_factory=list)

    @classmethod
    def from_new(cls, tenant: typing.Any, unit: typing.Any, lease: typing.Any) -> "TenantType":
        return cls(
            uid=str(tenant.id),
            name=f"{tenant.first_name} {tenant.last_name}",
            email=tenant.email,
            unit_label=unit.label,
            lease_end_date=str(lease.end_date),
            rent_amount=lease.rent_amount,
            payment_status=lease.payment_status,
            is_active=True,
        )

    @classmethod
    def from_sql(cls, t: typing.Any, today: _date) -> "TenantType":
        from db.queries import format_address, tenant_display_name
        active_lease = next(
            (l for l in t.leases if l.end_date and l.end_date >= today),
            t.leases[0] if t.leases else None,
        )
        prop_map: dict = {}
        lease_items: list = []
        for l in t.leases:
            if l.property:
                key = str(l.property.id)
                if key not in prop_map:
                    prop_map[key] = HouseType(
                        uid=key,
                        name=l.property.name or "",
                        address=format_address(l.property),
                    )
            lease_items.append(LeaseType.from_sql(l))

        is_active = (
            active_lease is not None
            and active_lease.end_date is not None
            and active_lease.end_date >= today
        )
        extra_properties = [
            ExtraPropertyType(key=str(k), value=str(v))
            for k, v in (t.extra or {}).items()
        ]
        return cls(
            uid=str(t.id),
            name=tenant_display_name(t),
            email=t.email,
            unit_label=active_lease.unit.label if active_lease and active_lease.unit else None,
            lease_end_date=str(active_lease.end_date) if active_lease else None,
            rent_amount=active_lease.rent_amount if active_lease else None,
            payment_status=active_lease.payment_status if active_lease and hasattr(active_lease, "payment_status") else "current",
            is_active=is_active,
            rents=list(prop_map.values()),
            leases=lease_items,
            extra_properties=extra_properties,
        )


@strawberry.type
class LeaseType:
    uid: str
    start_date: str
    end_date: str
    rent_amount: float
    tenant: typing.Optional[TenantType] = None
    house: typing.Optional[HouseType] = None

    @classmethod
    def from_sql(cls, l: typing.Any) -> "LeaseType":
        from db.queries import format_address, tenant_display_name
        return cls(
            uid=str(l.id),
            start_date=str(l.start_date),
            end_date=str(l.end_date),
            rent_amount=l.rent_amount,
            tenant=TenantType(uid=str(l.tenant.id), name=tenant_display_name(l.tenant)) if l.tenant else None,
            house=HouseType(
                uid=str(l.property.id),
                name=l.property.name or "",
                address=format_address(l.property),
            ) if l.property else None,
        )


@strawberry.type
class ChatMessageType:
    uid: str
    body: typing.Optional[str] = None
    message_type: typing.Optional[str] = None
    sender_name: typing.Optional[str] = None
    sender_type: typing.Optional[str] = None
    is_ai: bool = False
    is_system: bool = False
    draft_reply: typing.Optional[str] = None
    approval_status: typing.Optional[str] = None
    related_task_ids: typing.Optional[strawberry.scalars.JSON] = None
    suggestion_id: typing.Optional[str] = None
    sent_at: str = ""

    @classmethod
    def from_sql(cls, msg: typing.Any) -> "ChatMessageType":
        raw_st = getattr(msg, "sender_type", None)
        st_value = raw_st.value if hasattr(raw_st, "value") else str(raw_st) if raw_st else None
        related = getattr(msg, "related_task_ids", None) or {}
        return cls(
            uid=str(msg.id),
            body=msg.body,
            message_type=msg.message_type,
            sender_name=msg.sender_name,
            sender_type=st_value,
            is_ai=msg.is_ai,
            is_system=msg.is_system,
            draft_reply=getattr(msg, "draft_reply", None),
            approval_status=getattr(msg, "approval_status", None),
            related_task_ids=related if related else None,
            suggestion_id=related.get("suggestion_id") if isinstance(related, dict) else None,
            sent_at=_utc_iso(msg.sent_at),
        )


@strawberry.type
class TaskType:
    uid: str
    task_number: typing.Optional[int] = None
    title: typing.Optional[str] = None
    task_status: typing.Optional[str] = None
    task_mode: typing.Optional[str] = None
    source: typing.Optional[str] = None
    category: typing.Optional[str] = None
    urgency: typing.Optional[str] = None
    priority: typing.Optional[str] = None
    confidential: bool = False
    last_message_at: typing.Optional[str] = None
    property_id: typing.Optional[str] = None
    unit_id: typing.Optional[str] = None
    created_at: str = ""
    messages: typing.List[ChatMessageType] = strawberry.field(default_factory=list)
    tenant_name: typing.Optional[str] = None
    unit_label: typing.Optional[str] = None
    ai_triage_suggestion: typing.Optional[str] = None
    vendor_assigned: typing.Optional[str] = None
    ai_conversation_id: typing.Optional[str] = None
    parent_conversation_id: typing.Optional[str] = None
    ancestor_ids: typing.Optional[typing.List[str]] = None
    require_vendor_type: typing.Optional[str] = None
    assigned_vendor_id: typing.Optional[str] = None
    assigned_vendor_name: typing.Optional[str] = None
    external_conversation_id: typing.Optional[str] = None
    suggestion_options: typing.Optional[strawberry.scalars.JSON] = None

    @classmethod
    def from_sql(cls, t: typing.Any) -> "TaskType":
        from db.models import ParticipantType as PT
        # Collect messages from the task's AI conversation
        ai_convo = getattr(t, "ai_conversation", None)
        all_msgs = list(getattr(ai_convo, "messages", [])) if ai_convo else []
        all_msgs.sort(key=lambda m: m.sent_at)

        messages = [ChatMessageType.from_sql(m) for m in all_msgs]

        tenant_name = None
        if getattr(t, "lease", None) and t.lease.tenant:
            ten = t.lease.tenant
            tenant_name = f"{ten.first_name} {ten.last_name}".strip()

        unit_label = None
        if getattr(t, "unit", None):
            unit_label = t.unit.label
        elif getattr(t, "lease", None) and t.lease.unit:
            unit_label = t.lease.unit.label

        context_msgs = [m for m in all_msgs if getattr(m, "message_type", None) == MessageType.CONTEXT]
        ai_triage_suggestion = context_msgs[0].body if context_msgs else None

        vendor_msgs = [m for m in all_msgs if m.sender_type == PT.EXTERNAL_CONTACT and m.sender_name]
        vendor_assigned = vendor_msgs[0].sender_name if vendor_msgs else None

        # Get extra from the AI conversation
        extra = getattr(ai_convo, 'extra', None) or {} if ai_convo else {}

        return cls(
            uid=str(t.id),
            task_number=t.task_number,
            title=t.title,
            task_status=t.task_status,
            task_mode=t.task_mode,
            source=t.source,
            category=t.category,
            urgency=t.urgency,
            priority=t.priority,
            confidential=t.confidential,
            last_message_at=_utc_iso(t.last_message_at) or None,
            property_id=str(t.property_id) if t.property_id else None,
            unit_id=str(t.unit_id) if t.unit_id else None,
            created_at=_utc_iso(t.created_at),
            messages=messages,
            tenant_name=tenant_name,
            unit_label=unit_label,
            ai_triage_suggestion=ai_triage_suggestion,
            vendor_assigned=vendor_assigned,
            ai_conversation_id=str(t.ai_conversation_id) if t.ai_conversation_id else None,
            parent_conversation_id=str(t.parent_conversation_id) if t.parent_conversation_id else None,
            ancestor_ids=[],
            require_vendor_type=extra.get('require_vendor_type'),
            assigned_vendor_id=extra.get('assigned_vendor_id'),
            assigned_vendor_name=extra.get('assigned_vendor_name'),
            external_conversation_id=str(t.external_conversation_id) if t.external_conversation_id else None,
            suggestion_options=extra.get('suggestion_options'),
        )


@strawberry.type
class SuggestionType:
    uid: str
    title: typing.Optional[str] = None
    body: typing.Optional[str] = None
    category: typing.Optional[str] = None
    urgency: typing.Optional[str] = None
    status: str = "pending"
    source: typing.Optional[str] = None
    automation_key: typing.Optional[str] = None
    options: typing.Optional[strawberry.scalars.JSON] = None
    action_taken: typing.Optional[str] = None
    property_id: typing.Optional[str] = None
    unit_id: typing.Optional[str] = None
    task_id: typing.Optional[str] = None
    vendor_name: typing.Optional[str] = None
    property_name: typing.Optional[str] = None
    draft_message: typing.Optional[str] = None
    messages: typing.List[ChatMessageType] = strawberry.field(default_factory=list)
    created_at: str = ""

    @classmethod
    def from_sql(cls, s: typing.Any) -> "SuggestionType":
        ai_convo = getattr(s, "ai_conversation", None)
        all_msgs = list(getattr(ai_convo, "messages", [])) if ai_convo else []
        all_msgs.sort(key=lambda m: m.sent_at)
        messages = [ChatMessageType.from_sql(m) for m in all_msgs]

        payload = s.action_payload or {}
        prop_name = None
        if s.property_id and hasattr(s, "property") and s.property:
            prop_name = getattr(s.property, "name", None) or getattr(s.property, "address_line1", None)

        return cls(
            uid=str(s.id),
            title=s.title,
            body=s.body,
            category=s.category,
            urgency=s.urgency,
            status=s.status,
            source=s.source,
            automation_key=s.automation_key,
            options=s.options,
            action_taken=s.action_taken,
            property_id=str(s.property_id) if s.property_id else None,
            unit_id=str(s.unit_id) if s.unit_id else None,
            task_id=str(s.task_id) if s.task_id else None,
            vendor_name=payload.get("vendor_name"),
            property_name=prop_name,
            draft_message=payload.get("draft_message"),
            messages=messages,
            created_at=_utc_iso(s.created_at),
        )


@strawberry.type
class DocumentTagType:
    uid: str
    document_id: str
    tag_type: str
    property_id: typing.Optional[str] = None
    unit_id: typing.Optional[str] = None
    tenant_id: typing.Optional[str] = None
    created_at: str = ""

    @classmethod
    def from_sql(cls, tag: typing.Any) -> "DocumentTagType":
        return cls(
            uid=str(tag.id),
            document_id=str(tag.document_id),
            tag_type=tag.tag_type,
            property_id=str(tag.property_id) if tag.property_id else None,
            unit_id=str(tag.unit_id) if tag.unit_id else None,
            tenant_id=str(tag.tenant_id) if tag.tenant_id else None,
            created_at=_utc_iso(tag.created_at),
        )


VENDOR_TYPES: list[str] = [
    "Plumber", "Electrician", "HVAC", "General Contractor", "Handyman",
    "Landscaper", "Cleaning/Janitorial", "Pest Control", "Locksmith",
    "Roofer", "Painter", "Appliance Repair", "Inspector", "Snow Removal",
]


VENDOR_CONTACT_METHODS: list[str] = ["rentmate", "email", "phone"]


@strawberry.type
class VendorType:
    uid: str
    name: str
    company: typing.Optional[str] = None
    vendor_type: typing.Optional[str] = None
    phone: typing.Optional[str] = None
    email: typing.Optional[str] = None
    notes: typing.Optional[str] = None
    contact_method: str = "rentmate"
    invite_token: typing.Optional[str] = None
    invite_status: typing.Optional[str] = None
    created_at: str = ""

    @classmethod
    def from_sql(cls, v) -> "VendorType":
        extra = v.extra or {}
        return cls(
            uid=str(v.id),
            name=v.name,
            company=v.company,
            vendor_type=v.role_label,
            phone=v.phone,
            email=v.email,
            notes=v.notes,
            contact_method=extra.get("contact_method", "rentmate"),
            invite_token=extra.get("invite_token"),
            invite_status=extra.get("invite_status"),
            created_at=_utc_iso(v.created_at),
        )


@strawberry.input
class CreateVendorInput:
    name: str
    company: typing.Optional[str] = None
    vendor_type: typing.Optional[str] = None
    phone: typing.Optional[str] = None
    email: typing.Optional[str] = None
    notes: typing.Optional[str] = None
    contact_method: str = "rentmate"


@strawberry.input
class UpdateVendorInput:
    uid: str
    name: typing.Optional[str] = None
    company: typing.Optional[str] = None
    vendor_type: typing.Optional[str] = None
    phone: typing.Optional[str] = None
    email: typing.Optional[str] = None
    notes: typing.Optional[str] = None
    contact_method: typing.Optional[str] = None


@strawberry.type
class ConversationSummaryType:
    uid: str
    conversation_type: str
    title: typing.Optional[str] = None
    last_message_at: typing.Optional[str] = None
    updated_at: str = ""
    last_message_body: typing.Optional[str] = None
    last_message_sender_name: typing.Optional[str] = None
    property_name: typing.Optional[str] = None
    participant_count: int = 0
    unread_count: int = 0

    @classmethod
    def from_sql(cls, c: typing.Any) -> "ConversationSummaryType":
        last_msg = max(c.messages, key=lambda m: m.sent_at, default=None) if c.messages else None
        active_participants = [p for p in c.participants if p.is_active] if c.participants else []
        prop_name = None
        if c.property:
            prop_name = getattr(c.property, "name", None) or getattr(c.property, "address_line1", None)
        return cls(
            uid=str(c.id),
            conversation_type=c.conversation_type or "tenant",
            title=c.subject,
            last_message_at=_utc_iso(last_msg.sent_at) if last_msg else None,
            updated_at=_utc_iso(c.updated_at),
            last_message_body=last_msg.body[:120] if last_msg and last_msg.body else None,
            last_message_sender_name=last_msg.sender_name if last_msg else None,
            property_name=prop_name,
            participant_count=len(active_participants),
        )
