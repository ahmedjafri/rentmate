# gql/types.py
import strawberry
import typing
from datetime import date as _date


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
class AddTaskMessageInput:
    task_id: str
    body: str
    message_type: str = "message"
    sender_name: str = "You"
    is_ai: bool = False

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
            unit_list=[UnitType(uid=str(u.id), label=u.label, is_occupied=False) for u in units],
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

        unit_list = [
            UnitType(uid=str(u.id), label=u.label, is_occupied=u.id in active_unit_ids)
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
class TaskChatMessageType:
    uid: str
    body: typing.Optional[str] = None
    message_type: typing.Optional[str] = None
    sender_name: typing.Optional[str] = None
    is_ai: bool = False
    is_system: bool = False
    draft_reply: typing.Optional[str] = None
    approval_status: typing.Optional[str] = None
    related_task_ids: typing.Optional[strawberry.scalars.JSON] = None
    sent_at: str = ""

    @classmethod
    def from_sql(cls, msg: typing.Any) -> "TaskChatMessageType":
        return cls(
            uid=str(msg.id),
            body=msg.body,
            message_type=msg.message_type,
            sender_name=msg.sender_name,
            is_ai=msg.is_ai,
            is_system=msg.is_system,
            draft_reply=getattr(msg, "draft_reply", None),
            approval_status=getattr(msg, "approval_status", None),
            related_task_ids=getattr(msg, "related_task_ids", None),
            sent_at=str(msg.sent_at),
        )


@strawberry.type
class TaskType:
    uid: str
    title: typing.Optional[str] = None
    is_task: bool = False
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
    messages: typing.List[TaskChatMessageType] = strawberry.field(default_factory=list)
    tenant_name: typing.Optional[str] = None
    unit_label: typing.Optional[str] = None
    ai_triage_suggestion: typing.Optional[str] = None
    vendor_assigned: typing.Optional[str] = None
    parent_conversation_id: typing.Optional[str] = None
    ancestor_ids: typing.Optional[typing.List[str]] = None
    require_vendor_type: typing.Optional[str] = None
    assigned_vendor_id: typing.Optional[str] = None
    assigned_vendor_name: typing.Optional[str] = None

    @classmethod
    def from_sql(cls, c: typing.Any) -> "TaskType":
        from db.models import ParticipantType as PT
        messages = [TaskChatMessageType.from_sql(m) for m in getattr(c, "messages", [])]

        tenant_name = None
        if getattr(c, "lease", None) and c.lease.tenant:
            t = c.lease.tenant
            tenant_name = f"{t.first_name} {t.last_name}".strip()

        unit_label = None
        if getattr(c, "unit", None):
            unit_label = c.unit.label
        elif getattr(c, "lease", None) and c.lease.unit:
            unit_label = c.lease.unit.label

        msgs = getattr(c, "messages", [])
        context_msgs = [m for m in msgs if getattr(m, "message_type", None) == "context"]
        ai_triage_suggestion = context_msgs[0].body if context_msgs else None

        vendor_msgs = [m for m in msgs if m.sender_type == PT.EXTERNAL_CONTACT and m.sender_name]
        vendor_assigned = vendor_msgs[0].sender_name if vendor_msgs else None

        extra = getattr(c, 'extra', None) or {}
        return cls(
            uid=str(c.id),
            title=c.subject,
            is_task=c.is_task,
            task_status=c.task_status,
            task_mode=c.task_mode,
            source=c.source,
            category=c.category,
            urgency=c.urgency,
            priority=c.priority,
            confidential=c.confidential,
            last_message_at=str(c.last_message_at) if c.last_message_at else None,
            property_id=str(c.property_id) if c.property_id else None,
            unit_id=str(c.unit_id) if c.unit_id else None,
            created_at=str(c.created_at),
            messages=messages,
            tenant_name=tenant_name,
            unit_label=unit_label,
            ai_triage_suggestion=ai_triage_suggestion,
            vendor_assigned=vendor_assigned,
            parent_conversation_id=str(c.parent_conversation_id) if getattr(c, 'parent_conversation_id', None) else None,
            ancestor_ids=getattr(c, 'ancestor_ids', None) or [],
            require_vendor_type=extra.get('require_vendor_type'),
            assigned_vendor_id=extra.get('assigned_vendor_id'),
            assigned_vendor_name=extra.get('assigned_vendor_name'),
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
            created_at=str(tag.created_at),
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
            created_at=str(v.created_at),
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

    @classmethod
    def from_sql(cls, c: typing.Any) -> "ConversationSummaryType":
        return cls(
            uid=str(c.id),
            conversation_type=c.conversation_type or "tenant",
            title=c.subject,
            last_message_at=str(c.last_message_at) if c.last_message_at else None,
            updated_at=str(c.updated_at),
        )
