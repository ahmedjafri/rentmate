# gql/types.py
import typing
from datetime import date as _date, datetime as _datetime

import strawberry

from db.enums import (  # noqa: F401 — re-exported
    AgentSource,
    AutomationSource,
    RoutineState,
    SuggestionOption,
    SuggestionSource,
    SuggestionSourceEnum,
    SuggestionStatus,
    TaskCategory,
    TaskMode,
    TaskPriority,
    TaskSource,
    TaskStatus,
    Urgency,
)
from db.models import ConversationType, MessageType

TaskCategoryEnum = strawberry.enum(TaskCategory, name="TaskCategory")
TaskModeEnum = strawberry.enum(TaskMode, name="TaskMode")
TaskPriorityEnum = strawberry.enum(TaskPriority, name="TaskPriority")
TaskSourceEnum = strawberry.enum(TaskSource, name="TaskSource")
TaskStatusEnum = strawberry.enum(TaskStatus, name="TaskStatus")
UrgencyEnum = strawberry.enum(Urgency, name="Urgency")
SuggestionSourceEnumType = strawberry.enum(SuggestionSourceEnum, name="SuggestionSource")
SuggestionStatusEnum = strawberry.enum(SuggestionStatus, name="SuggestionStatus")
RoutineStateEnum = strawberry.enum(RoutineState, name="RoutineState")
ConversationTypeEnum = strawberry.enum(ConversationType, name="ConversationType")
MessageTypeEnum = strawberry.enum(MessageType, name="MessageType")


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
    email: typing.Optional[str] = None

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
    goal: str
    source: TaskSourceEnum
    task_status: TaskStatusEnum = TaskStatus.ACTIVE
    category: typing.Optional[TaskCategoryEnum] = None
    urgency: typing.Optional[UrgencyEnum] = None
    priority: typing.Optional[TaskPriorityEnum] = None
    task_mode: typing.Optional[TaskModeEnum] = None
    property_id: typing.Optional[str] = None
    unit_id: typing.Optional[str] = None
    confidential: bool = False

@strawberry.input
class SpawnTaskInput:
    parent_conversation_id: str
    objective: str
    category: typing.Optional[TaskCategoryEnum] = None
    urgency: typing.Optional[UrgencyEnum] = None
    priority: typing.Optional[TaskPriorityEnum] = None
    task_mode: TaskModeEnum = TaskMode.AUTONOMOUS
    source: TaskSourceEnum = TaskSource.MANUAL

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
    message_type: MessageTypeEnum = MessageType.MESSAGE
    sender_name: str = "You"
    is_ai: bool = False

@strawberry.input
class UpdateTaskInput:
    uid: int
    task_mode: typing.Optional[TaskModeEnum] = None
    task_status: typing.Optional[TaskStatusEnum] = None
    category: typing.Optional[TaskCategoryEnum] = None
    urgency: typing.Optional[UrgencyEnum] = None

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
    context: typing.Optional[str] = None


@strawberry.type
class HouseType:
    uid: str
    name: str
    address: str
    property_type: typing.Optional[str] = None
    source: typing.Optional[str] = None
    context: typing.Optional[str] = None
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
            context=p.context,
            units=len(units),
            occupied_units=0,
            monthly_revenue=0.0,
            unit_list=[UnitType(uid=str(u.id), label=u.label, is_occupied=False, context=u.context) for u in units],
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
                t_key = str(t.external_id)
                if t_key not in tenant_map:
                    tenant_map[t_key] = TenantType(uid=str(t.external_id), name=tenant_display_name(t))
                if is_active and l.unit_id:
                    active_unit_ids.add(l.unit_id)
            if is_active:
                monthly_revenue += l.rent_amount or 0.0
            lease_items.append(LeaseType.from_sql(l))

        unit_list = [
            UnitType(uid=str(u.id), label=u.label, is_occupied=u.id in active_unit_ids, context=u.context)
            for u in p.units
        ]
        return cls(
            uid=str(p.id),
            name=p.name or "",
            address=format_address(p),
            property_type=p.property_type or "multi_family",
            source=p.source or "manual",
            context=p.context,
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
    context: typing.Optional[str] = None
    portal_url: typing.Optional[str] = None
    rents: typing.List[HouseType] = strawberry.field(default_factory=list)
    leases: typing.List["LeaseType"] = strawberry.field(default_factory=list)
    extra_properties: typing.List[ExtraPropertyType] = strawberry.field(default_factory=list)

    @classmethod
    def from_new(cls, tenant: typing.Any, *, unit: typing.Any, lease: typing.Any) -> "TenantType":
        return cls(
            uid=str(tenant.external_id),
            name=f"{tenant.user.first_name} {tenant.user.last_name}",
            email=tenant.user.email,
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
        from gql.services.tenant_service import TenantService
        return cls(
            uid=str(t.external_id),
            name=tenant_display_name(t),
            email=t.user.email if t.user else None,
            unit_label=active_lease.unit.label if active_lease and active_lease.unit else None,
            lease_end_date=str(active_lease.end_date) if active_lease else None,
            rent_amount=active_lease.rent_amount if active_lease else None,
            payment_status=active_lease.payment_status if active_lease and hasattr(active_lease, "payment_status") else "current",
            is_active=is_active,
            context=t.context,
            portal_url=TenantService.get_portal_url(t),
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
            tenant=TenantType(uid=str(l.tenant.external_id), name=tenant_display_name(l.tenant)) if l.tenant else None,
            house=HouseType(
                uid=str(l.property.id),
                name=l.property.name or "",
                address=format_address(l.property),
            ) if l.property else None,
        )


@strawberry.type
class ChatActionCardFieldType:
    label: str
    value: str


@strawberry.type
class ChatActionCardLinkType:
    label: str
    entity_type: str
    entity_id: str
    property_id: typing.Optional[str] = None


@strawberry.type
class ChatActionCardUnitType:
    uid: str
    label: str
    property_id: str


@strawberry.type
class ChatActionCardType:
    kind: str
    title: str
    summary: typing.Optional[str] = None
    fields: typing.List[ChatActionCardFieldType] = strawberry.field(default_factory=list)
    links: typing.List[ChatActionCardLinkType] = strawberry.field(default_factory=list)
    units: typing.List[ChatActionCardUnitType] = strawberry.field(default_factory=list)


@strawberry.type
class ChatMessageType:
    uid: str
    body: typing.Optional[str] = None
    message_type: typing.Optional[MessageTypeEnum] = None
    sender_name: typing.Optional[str] = None
    sender_type: typing.Optional[str] = None
    is_ai: bool = False
    is_system: bool = False
    draft_reply: typing.Optional[str] = None
    approval_status: typing.Optional[str] = None
    related_task_ids: typing.Optional[strawberry.scalars.JSON] = None
    suggestion_id: typing.Optional[str] = None
    action_card: typing.Optional[ChatActionCardType] = None
    sent_at: str = ""

    @classmethod
    def from_sql(cls, msg: typing.Any) -> "ChatMessageType":
        from gql.services.chat_service import parse_message_meta

        raw_st = getattr(msg, "sender_type", None)
        st_value = raw_st.value if hasattr(raw_st, "value") else str(raw_st) if raw_st else None
        meta = parse_message_meta(getattr(msg, "meta", None))
        return cls(
            uid=str(msg.id),
            body=msg.body,
            message_type=msg.message_type,
            sender_name=msg.sender_name,
            sender_type=st_value,
            is_ai=msg.is_ai,
            is_system=msg.is_system,
            draft_reply=meta.draft_reply,
            approval_status=getattr(msg, "approval_status", None),
            related_task_ids=meta.related_task_ids.model_dump(exclude_none=True) if meta.related_task_ids else None,
            suggestion_id=str(meta.related_task_ids.suggestion_id) if meta.related_task_ids and meta.related_task_ids.suggestion_id is not None else None,
            action_card=ChatActionCardType(
                kind=meta.action_card.kind,
                title=meta.action_card.title,
                summary=meta.action_card.summary,
                fields=[
                    ChatActionCardFieldType(label=field.label, value=field.value)
                    for field in (meta.action_card.fields or [])
                ],
                links=[
                    ChatActionCardLinkType(
                        label=link.label,
                        entity_type=link.entity_type,
                        entity_id=link.entity_id,
                        property_id=link.property_id,
                    )
                    for link in (meta.action_card.links or [])
                ],
                units=[
                    ChatActionCardUnitType(
                        uid=unit.uid,
                        label=unit.label,
                        property_id=unit.property_id,
                    )
                    for unit in (meta.action_card.units or [])
                ],
            ) if meta.action_card else None,
            sent_at=_utc_iso(msg.sent_at),
        )


@strawberry.type
class ConversationParticipantType:
    """A participant in a conversation."""
    name: str
    participant_type: str  # "tenant" | "vendor"
    entity_id: typing.Optional[str] = None
    portal_url: typing.Optional[str] = None

@strawberry.type
class LinkedConversationType:
    """Summary of a conversation linked to a task."""
    uid: str
    label: str
    conversation_type: ConversationTypeEnum
    last_message_at: typing.Optional[str] = None
    message_count: int = 0
    participants: typing.List[ConversationParticipantType] = strawberry.field(default_factory=list)

    @classmethod
    def from_sql(cls, conv: typing.Any, label: str) -> "LinkedConversationType":

        msgs = getattr(conv, "messages", []) or []
        last_msg = max(msgs, key=lambda m: m.sent_at, default=None) if msgs else None

        parts: list[ConversationParticipantType] = []
        for p in getattr(conv, "participants", []) or []:
            if not p.is_active:
                continue
            from db.models import ParticipantType as PT
            if p.participant_type == PT.TENANT:
                name = f"{p.user.first_name} {p.user.last_name}".strip()
                parts.append(ConversationParticipantType(
                    name=name, participant_type="tenant", entity_id=str(p.user_id),
                    portal_url="",
                ))
            elif p.participant_type == PT.EXTERNAL_CONTACT:
                name = f"{p.user.first_name or ''} {p.user.last_name or ''}".strip() or "Vendor"
                parts.append(ConversationParticipantType(
                    name=name, participant_type="vendor",
                    entity_id=str(p.user_id),
                    portal_url="",
                ))

        return cls(
            uid=str(conv.external_id),
            label=label,
            conversation_type=conv.conversation_type or "task_ai",
            last_message_at=_utc_iso(last_msg.sent_at) if last_msg else None,
            message_count=len(msgs),
            participants=parts,
        )


@strawberry.type
class TaskType:
    uid: int
    task_number: typing.Optional[int] = None
    title: typing.Optional[str] = None
    task_status: typing.Optional[TaskStatusEnum] = None
    task_mode: typing.Optional[TaskModeEnum] = None
    source: typing.Optional[TaskSourceEnum] = None
    category: typing.Optional[TaskCategoryEnum] = None
    urgency: typing.Optional[UrgencyEnum] = None
    priority: typing.Optional[TaskPriorityEnum] = None
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
    external_conversation_ids: typing.List[str] = strawberry.field(default_factory=list)
    external_conversation_id: typing.Optional[str] = strawberry.field(
        default=None,
        deprecation_reason="Use externalConversationIds instead.",
    )
    steps: typing.Optional[strawberry.scalars.JSON] = None
    goal: typing.Optional[str] = None
    suggestion_options: typing.Optional[strawberry.scalars.JSON] = None
    linked_conversations: typing.List[LinkedConversationType] = strawberry.field(default_factory=list)
    last_reviewed_at: typing.Optional[str] = None
    last_review_status: typing.Optional[str] = None
    last_review_summary: typing.Optional[str] = None
    last_review_next_step: typing.Optional[str] = None
    unread_count: int = 0

    @classmethod
    def from_sql(cls, t: typing.Any) -> "TaskType":
        from db.models import ParticipantType as PT
        from gql.services.chat_service import parse_conversation_extra
        def _latest_message_at(*conversations: typing.Any) -> typing.Optional[str]:
            latest = None
            for conv in conversations:
                if conv is None:
                    continue
                for msg in getattr(conv, "messages", []) or []:
                    sent_at = getattr(msg, "sent_at", None)
                    if sent_at is None:
                        continue
                    if latest is None or sent_at > latest:
                        latest = sent_at
            return _utc_iso(latest) if latest else None

        def _latest_activity_at(*values: typing.Any) -> typing.Any:
            latest = None
            for value in values:
                if value is None:
                    continue
                if latest is None or value > latest:
                    latest = value
            return latest

        def _first_active_participant_name(conv: typing.Any) -> typing.Optional[str]:
            """Return the first active non-account participant's first name, if any."""
            for p in getattr(conv, "participants", []) or []:
                if not getattr(p, "is_active", True):
                    continue
                ptype = getattr(p, "participant_type", None)
                if ptype == PT.ACCOUNT_USER:
                    continue
                user = getattr(p, "user", None)
                first = getattr(user, "first_name", None) if user else None
                if first:
                    return first
            return None
        # Collect messages from the task's AI conversation
        ai_convo = getattr(t, "ai_conversation", None)
        all_msgs = list(getattr(ai_convo, "messages", [])) if ai_convo else []
        all_msgs.sort(key=lambda m: m.sent_at)

        messages = [ChatMessageType.from_sql(m) for m in all_msgs]

        tenant_name = None
        if getattr(t, "lease", None) and t.lease.tenant:
            ten = t.lease.tenant
            tenant_name = f"{ten.user.first_name} {ten.user.last_name}".strip()

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
        extra = parse_conversation_extra(getattr(ai_convo, "extra", None) if ai_convo else None)

        # Build linked conversations list
        linked: list[LinkedConversationType] = []
        if ai_convo:
            linked.append(LinkedConversationType.from_sql(ai_convo, "AI"))
        parent_convo = getattr(t, "parent_conversation", None)
        ext_convos = list(getattr(t, "external_conversations", []) or [])
        parent_id = parent_convo.id if parent_convo else None

        # Collect external + parent conversations so we can disambiguate labels
        # when multiple of the same type are attached to one task (e.g. two
        # vendor quote threads on a single maintenance task).
        external_convos = []
        if parent_convo:
            external_convos.append(parent_convo)
        for ext_convo in ext_convos:
            if parent_id is not None and ext_convo.id == parent_id:
                continue
            external_convos.append(ext_convo)

        type_counts: dict[str, int] = {}
        for convo in external_convos:
            ctype = getattr(convo, "conversation_type", None) or "vendor"
            type_counts[ctype] = type_counts.get(ctype, 0) + 1

        for convo in external_convos:
            ctype = getattr(convo, "conversation_type", None) or "vendor"
            type_label = (
                "Tenant" if ctype == "tenant"
                else "Vendor" if ctype == "vendor"
                else ctype.replace("_", " ").title()
            )
            # When multiple conversations of the same type exist, disambiguate
            # with the contact's first name so tabs read "Mike" / "Sarah"
            # instead of two ambiguous "Vendor" tabs.
            contact_name = _first_active_participant_name(convo)
            if type_counts.get(ctype, 0) > 1 and contact_name:
                label = contact_name
            else:
                label = type_label
            linked.append(LinkedConversationType.from_sql(convo, label))
        last_message_at = _latest_message_at(ai_convo, parent_convo, *ext_convos)
        latest_activity_at = _latest_activity_at(
            getattr(t, "updated_at", None),
            getattr(t, "last_reviewed_at", None),
            getattr(t, "last_message_at", None),
            max(
                (
                    getattr(msg, "sent_at", None)
                    for conv in [ai_convo, parent_convo, *ext_convos]
                    for msg in (getattr(conv, "messages", []) or [])
                    if getattr(msg, "sent_at", None) is not None
                ),
                default=None,
            ),
        )
        last_seen_at = getattr(t, "last_seen_at", None)
        unread_count = 1 if latest_activity_at and (last_seen_at is None or latest_activity_at > last_seen_at) else 0

        return cls(
            uid=t.id,
            task_number=t.id,
            title=t.title,
            task_status=t.task_status,
            task_mode=t.task_mode,
            source=t.source,
            category=t.category,
            urgency=t.urgency,
            priority=t.priority,
            confidential=t.confidential,
            last_message_at=last_message_at,
            property_id=str(t.property_id) if t.property_id else None,
            unit_id=str(t.unit_id) if t.unit_id else None,
            created_at=_utc_iso(t.created_at),
            messages=messages,
            tenant_name=tenant_name,
            unit_label=unit_label,
            ai_triage_suggestion=ai_triage_suggestion,
            vendor_assigned=vendor_assigned,
            ai_conversation_id=str(ai_convo.external_id) if ai_convo and getattr(ai_convo, "external_id", None) else None,
            parent_conversation_id=str(parent_convo.external_id) if parent_convo and getattr(parent_convo, "external_id", None) else None,
            ancestor_ids=[],
            require_vendor_type=extra.require_vendor_type,
            assigned_vendor_id=str(extra.assigned_vendor_id) if extra.assigned_vendor_id is not None else None,
            assigned_vendor_name=extra.assigned_vendor_name,
            external_conversation_ids=[str(c.external_id) for c in ext_convos if getattr(c, "external_id", None)],
            external_conversation_id=str(ext_convos[0].external_id) if ext_convos and getattr(ext_convos[0], "external_id", None) else None,
            steps=t.steps,
            goal=t.goal,
            suggestion_options=extra.suggestion_options,
            linked_conversations=linked,
            last_reviewed_at=_utc_iso(t.last_reviewed_at) if t.last_reviewed_at else None,
            last_review_status=t.last_review_status,
            last_review_summary=t.last_review_summary,
            last_review_next_step=t.last_review_next_step,
            unread_count=unread_count,
        )


@strawberry.type
class SuggestionType:
    uid: int
    title: typing.Optional[str] = None
    body: typing.Optional[str] = None
    category: typing.Optional[TaskCategoryEnum] = None
    urgency: typing.Optional[UrgencyEnum] = None
    status: SuggestionStatusEnum = SuggestionStatus.PENDING
    source: typing.Optional[SuggestionSourceEnumType] = None
    automation_key: typing.Optional[str] = None
    options: typing.Optional[strawberry.scalars.JSON] = None
    action_taken: typing.Optional[str] = None
    suggestion_type: typing.Optional[str] = None
    risk_score: typing.Optional[int] = None
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
            uid=s.id,
            title=s.title,
            body=s.body,
            category=s.category,
            urgency=s.urgency,
            status=s.status,
            source=s.source,
            automation_key=s.automation_key,
            options=s.options,
            action_taken=s.action_taken,
            suggestion_type=getattr(s, "suggestion_type", None),
            risk_score=getattr(s, "risk_score", None),
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
class RoutineType:
    uid: int
    name: str
    prompt: str
    schedule: str
    schedule_display: typing.Optional[str] = None
    is_default: bool = False
    enabled: bool = True
    state: RoutineStateEnum = RoutineState.SCHEDULED
    repeat: typing.Optional[int] = None
    completed_count: int = 0
    next_run_at: typing.Optional[str] = None
    last_run_at: typing.Optional[str] = None
    last_status: typing.Optional[str] = None
    last_output: typing.Optional[str] = None
    simulated_at: typing.Optional[str] = None
    created_at: str = ""

    @classmethod
    def from_sql(cls, st: typing.Any) -> "RoutineType":
        return cls(
            uid=st.id,
            name=st.name,
            prompt=st.prompt,
            schedule=st.schedule,
            schedule_display=st.schedule_display,
            is_default=getattr(st, "is_default", False),
            enabled=st.enabled,
            state=st.state or RoutineState.SCHEDULED,
            repeat=st.repeat,
            completed_count=st.completed_count or 0,
            next_run_at=_utc_iso(st.next_run_at) if st.next_run_at else None,
            last_run_at=_utc_iso(st.last_run_at) if st.last_run_at else None,
            last_status=st.last_status,
            last_output=st.last_output,
            simulated_at=_utc_iso(st.simulated_at) if getattr(st, "simulated_at", None) else None,
            created_at=_utc_iso(st.created_at),
        )


@strawberry.type
class DocumentType:
    uid: str
    filename: str
    document_type: str
    status: str
    progress: typing.Optional[str] = None
    extracted_data: typing.Optional[strawberry.scalars.JSON] = None
    extraction_meta: typing.Optional[strawberry.scalars.JSON] = None
    context: typing.Optional[str] = None
    raw_text: typing.Optional[str] = None
    error_message: typing.Optional[str] = None
    created_at: typing.Optional[str] = None
    processed_at: typing.Optional[str] = None

    @classmethod
    def from_sql(cls, d: typing.Any) -> "DocumentType":
        return cls(
            uid=str(d.id),
            filename=d.filename,
            document_type=d.document_type or "lease",
            status=d.status or "pending",
            progress=d.progress,
            extracted_data=d.extracted_data,
            extraction_meta=d.extraction_meta,
            context=d.context,
            raw_text=d.raw_text,
            error_message=d.error_message,
            created_at=_utc_iso(d.created_at),
            processed_at=_utc_iso(d.processed_at) if d.processed_at else None,
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
        tenant_id = getattr(tag, "tenant_external_id", None)
        if tenant_id is None and getattr(tag, "tenant_id", None):
            tenant_id = str(tag.tenant_id)
        return cls(
            uid=str(tag.id),
            document_id=str(tag.document_id),
            tag_type=tag.tag_type,
            property_id=str(tag.property_id) if tag.property_id else None,
            unit_id=str(tag.unit_id) if tag.unit_id else None,
            tenant_id=tenant_id,
            created_at=_utc_iso(tag.created_at),
        )


VENDOR_TYPES: list[str] = [
    "Plumber", "Electrician", "HVAC", "General Contractor", "Handyman",
    "Landscaper", "Cleaning/Janitorial", "Pest Control", "Locksmith",
    "Roofer", "Painter", "Appliance Repair", "Inspector", "Snow Removal",
]


@strawberry.type
class VendorType:
    uid: str
    name: str
    company: typing.Optional[str] = None
    vendor_type: typing.Optional[str] = None
    phone: typing.Optional[str] = None
    email: typing.Optional[str] = None
    notes: typing.Optional[str] = None
    context: typing.Optional[str] = None
    portal_url: typing.Optional[str] = None
    created_at: str = ""

    @classmethod
    def from_sql(cls, v) -> "VendorType":
        from gql.services.vendor_service import VendorService
        return cls(
            uid=str(v.external_id),
            name=v.name,
            company=v.company,
            vendor_type=v.role_label,
            phone=v.phone,
            email=v.email,
            notes=v.notes,
            context=v.context,
            portal_url=VendorService.get_portal_url(v),
            created_at=_utc_iso(v.created_at),
        )


@strawberry.input
class CreateVendorInput:
    name: str
    phone: str
    company: typing.Optional[str] = None
    vendor_type: typing.Optional[str] = None
    email: typing.Optional[str] = None
    notes: typing.Optional[str] = None


@strawberry.input
class UpdateVendorInput:
    uid: str
    name: typing.Optional[str] = None
    company: typing.Optional[str] = None
    vendor_type: typing.Optional[str] = None
    phone: typing.Optional[str] = None
    email: typing.Optional[str] = None
    notes: typing.Optional[str] = None


@strawberry.type
class ConversationSummaryType:
    uid: str
    conversation_type: ConversationTypeEnum
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
        visible_msgs = [m for m in (c.messages or []) if m.message_type in (MessageType.MESSAGE, MessageType.THREAD)]
        last_msg = max(visible_msgs, key=lambda m: m.sent_at, default=None) if visible_msgs else None
        active_participants = [p for p in c.participants if p.is_active] if c.participants else []
        prop_name = None
        if c.property:
            prop_name = getattr(c.property, "name", None) or getattr(c.property, "address_line1", None)
        return cls(
            uid=str(c.external_id),
            conversation_type=c.conversation_type or "tenant",
            title=c.subject,
            last_message_at=_utc_iso(last_msg.sent_at) if last_msg else None,
            updated_at=_utc_iso(c.updated_at),
            last_message_body=last_msg.body[:120] if last_msg and last_msg.body else None,
            last_message_sender_name=last_msg.sender_name if last_msg else None,
            property_name=prop_name,
            participant_count=len(active_participants),
        )
