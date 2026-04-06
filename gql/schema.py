# gql/schema.py
import typing
import strawberry
from datetime import date
from strawberry.schema.config import StrawberryConfig

from db.queries import (
    fetch_leases,
    fetch_messages,
    fetch_properties,
    fetch_task,
    fetch_tasks,
    fetch_tenants,
    fetch_conversations,
    fetch_vendors,
)
from db.lib import spawn_task_from_conversation as _spawn_task
from .auth_mutations import Mutation as AuthMutation
from .types import (
    UserType, HouseType, TenantType, LeaseType, TaskType, SuggestionType,
    ChatMessageType, DocumentTagType, ConversationSummaryType, SpawnTaskInput,
    CreateTaskInput, AddDocumentTagInput, SendMessageInput, UpdateTaskInput,
    CreatePropertyInput, UpdatePropertyInput, CreateTenantWithLeaseInput, AddLeaseForTenantInput,
    VendorType, CreateVendorInput, UpdateVendorInput, VENDOR_TYPES,
    UnitType, UpdateUnitNotesInput,
)
from .services.task_service import TaskService
from .services import chat_service, suggestion_service
from .services.property_service import PropertyService
from .services.tenant_service import TenantService
from .services.document_service import DocumentService
from .services.vendor_service import VendorService


# ---------------------------
# Context helpers
# ---------------------------

def _session(info):
    sess = info.context.get("db_session")
    if not sess:
        raise RuntimeError("DB session not found in context (db_session).")
    return sess


def _current_user(info):
    user = info.context.get("user")
    if not user:
        raise ValueError("Not authenticated")
    return user


# ---------------------------
# Query
# ---------------------------

@strawberry.type
class Query:

    @strawberry.field(description="Return the current authenticated user")
    def me(self, info) -> UserType:
        user = _current_user(info)
        return UserType(
            uid=str(user.get("id") or user.get("uid")),
            username=user.get("username") or user.get("email") or "user",
            role="admin",
        )

    @strawberry.field(description="Returns all properties with their tenants and leases")
    def houses(self, info) -> typing.List[HouseType]:
        _current_user(info)
        db = _session(info)
        today = date.today()
        properties = fetch_properties(db)
        # Compute pending task counts per unit across all properties
        from sqlalchemy import select as sa_select, func
        from db.models import Task as TaskModel
        active_statuses = ('active', 'suggested', 'paused')
        rows = db.execute(
            sa_select(TaskModel.unit_id, func.count(TaskModel.id))
            .where(TaskModel.unit_id.isnot(None))
            .where(TaskModel.task_status.in_(active_statuses))
            .group_by(TaskModel.unit_id)
        ).all()
        unit_task_map = {r[0]: r[1] for r in rows}
        for p in properties:
            p._unit_task_counts = {
                u.id: unit_task_map.get(u.id, 0) for u in p.units
            }
        return [HouseType.from_sql(p, today) for p in properties]

    @strawberry.field(description="Returns all tenants with their leases and properties")
    def tenants(self, info) -> typing.List[TenantType]:
        _current_user(info)
        today = date.today()
        return [TenantType.from_sql(t, today) for t in fetch_tenants(_session(info))]

    @strawberry.field(description="Returns tasks. Filters: category, status (comma-separated), source")
    def tasks(
        self,
        info,
        category: typing.Optional[str] = None,
        status: typing.Optional[str] = None,
        source: typing.Optional[str] = None,
    ) -> typing.List[TaskType]:
        _current_user(info)
        return [TaskType.from_sql(c) for c in fetch_tasks(_session(info), category=category, status=status, source=source)]

    @strawberry.field(description="Returns a single task by uid, including its full message thread")
    def task(self, info, uid: str) -> typing.Optional[TaskType]:
        _current_user(info)
        c = fetch_task(_session(info), uid)
        return TaskType.from_sql(c) if c else None

    @strawberry.field(description="Returns all leases with nested tenant and property")
    def leases(self, info) -> typing.List[LeaseType]:
        _current_user(info)
        return [LeaseType.from_sql(l) for l in fetch_leases(_session(info))]

    @strawberry.field(description="Returns messages for a conversation by uid")
    def conversation_messages(self, info, uid: str) -> typing.List[ChatMessageType]:
        _current_user(info)
        return [ChatMessageType.from_sql(m) for m in fetch_messages(_session(info), uid)]

    @strawberry.field(description="Returns all vendors")
    def vendors(self, info) -> typing.List[VendorType]:
        _current_user(info)
        return [VendorType.from_sql(v) for v in fetch_vendors(_session(info))]

    @strawberry.field(description="Returns the list of valid vendor type labels")
    def vendor_types(self, info) -> typing.List[str]:
        _current_user(info)
        return VENDOR_TYPES

    @strawberry.field(description="Returns suggestions, optionally filtered by status")
    def suggestions(
        self,
        info,
        status: typing.Optional[str] = None,
        limit: int = 50,
    ) -> typing.List[SuggestionType]:
        _current_user(info)
        from sqlalchemy.orm import joinedload, selectinload
        from db.models import Suggestion, Conversation, Message
        from sqlalchemy import select as sa_select
        db = _session(info)
        q = sa_select(Suggestion).options(
            joinedload(Suggestion.ai_conversation).selectinload(Conversation.messages),
            joinedload(Suggestion.property),
        ).order_by(Suggestion.created_at.desc()).limit(limit)
        if status:
            q = q.where(Suggestion.status == status)
        rows = db.execute(q).unique().scalars().all()
        return [SuggestionType.from_sql(s) for s in rows]

    @strawberry.field(description="Returns conversations by type (tenant/vendor/user_ai/task_ai)")
    def conversations(
        self,
        info,
        conversation_type: str,
        limit: int = 50,
        offset: int = 0,
    ) -> typing.List[ConversationSummaryType]:
        _current_user(info)
        return [ConversationSummaryType.from_sql(c) for c in fetch_conversations(_session(info), conversation_type, limit=limit, offset=offset)]


# ---------------------------
# Mutation
# ---------------------------

@strawberry.type
class Mutation(AuthMutation):

    @strawberry.mutation(description="Delete a tenant and all their leases (cascade)")
    def delete_tenant(self, info, uid: str) -> bool:
        _current_user(info)
        return TenantService.delete_tenant(_session(info), uid)

    @strawberry.mutation(description="Create a new task")
    def create_task(self, info, input: CreateTaskInput) -> TaskType:
        _current_user(info)
        db = _session(info)
        from db.models import ConversationType
        task = TaskService.create_task(db, input)
        ext_convo = chat_service.get_or_create_external_conversation(
            db,
            conversation_type=ConversationType.TENANT,
            subject=input.title,
            property_id=input.property_id,
            unit_id=input.unit_id,
        )
        task.external_conversation_id = ext_convo.id
        db.commit()
        db.refresh(task)
        return TaskType.from_sql(task)

    @strawberry.mutation(description="Transition task_status (e.g. suggested→active, active→resolved)")
    def update_task_status(self, info, uid: str, status: str) -> TaskType:
        _current_user(info)
        db = _session(info)
        task = TaskService.update_task_status(db, uid, status)
        db.commit()
        db.refresh(task)
        return TaskType.from_sql(task)

    @strawberry.mutation(description="Tag a document to a property, unit, or tenant")
    def add_document_tag(self, info, input: AddDocumentTagInput) -> DocumentTagType:
        _current_user(info)
        return DocumentTagType.from_sql(DocumentService.add_document_tag(_session(info), input))

    @strawberry.mutation(description="Add a message to any conversation")
    def send_message(self, info, input: SendMessageInput) -> ChatMessageType:
        _current_user(info)
        db = _session(info)
        msg = chat_service.send_message(
            db,
            conversation_id=input.conversation_id,
            body=input.body,
            message_type=input.message_type,
            sender_name=input.sender_name,
            is_ai=input.is_ai,
            draft_reply=input.draft_reply,
        )
        # Bump last_message_at on the linked task if any
        from sqlalchemy import select as _sel, or_
        from db.models import Task
        task = db.execute(
            _sel(Task).where(or_(
                Task.ai_conversation_id == input.conversation_id,
                Task.external_conversation_id == input.conversation_id,
            ))
        ).scalar_one_or_none()
        if task:
            from datetime import UTC, datetime
            task.last_message_at = datetime.now(UTC)
        db.commit()
        db.refresh(msg)
        return ChatMessageType.from_sql(msg)

    @strawberry.mutation(description="Send an SMS message to a vendor via Dialpad")
    def send_sms(self, info, vendor_id: str, body: str, task_id: typing.Optional[str] = None) -> ChatMessageType:
        _current_user(info)
        db = _session(info)
        from db.models import ExternalContact, ConversationType, Task

        vendor = db.query(ExternalContact).filter_by(id=vendor_id).first()
        if not vendor:
            raise ValueError(f"Vendor {vendor_id} not found")
        if not vendor.phone:
            raise ValueError(f"Vendor {vendor.name} has no phone number")

        # Find or create the vendor conversation
        conv = None
        if task_id:
            task = db.query(Task).filter_by(id=task_id).first()
            if task and task.external_conversation_id:
                conv = db.get(Conversation, task.external_conversation_id)
        if not conv:
            conv = chat_service.get_or_create_external_conversation(
                db,
                conversation_type=ConversationType.VENDOR,
                subject=f"SMS with {vendor.name}",
                vendor_id=vendor_id,
            )
            if task_id:
                task = db.query(Task).filter_by(id=task_id).first()
                if task:
                    task.external_conversation_id = conv.id

        # Persist the message
        msg = chat_service.send_message(
            db, conv.id,
            body=body,
            sender_name="You",
            is_ai=False,
        )
        db.commit()
        db.refresh(msg)

        # Dispatch SMS via Dialpad
        from handlers.chat import send_sms_reply, _get_dialpad_api_key, _get_dialpad_from_number
        api_key = _get_dialpad_api_key()
        from_num = _get_dialpad_from_number()
        if api_key:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                loop.create_task(send_sms_reply(from_num, vendor.phone, body, api_key))
            except RuntimeError:
                # No running event loop (shouldn't happen in FastAPI, but be safe)
                pass

        return ChatMessageType.from_sql(msg)

    @strawberry.mutation(description="Permanently delete a task and all its messages")
    def delete_task(self, info, uid: str) -> bool:
        _current_user(info)
        db = _session(info)
        result = TaskService.delete_task(db, uid)
        db.commit()
        return result

    @strawberry.mutation(description="Update task mode and/or status")
    def update_task(self, info, input: UpdateTaskInput) -> TaskType:
        _current_user(info)
        db = _session(info)
        task = TaskService.update_task(db, input)
        db.commit()
        db.refresh(task)
        return TaskType.from_sql(task)

    @strawberry.mutation(description="Archive (soft-delete) a conversation")
    def delete_conversation(self, info, uid: str) -> bool:
        _current_user(info)
        db = _session(info)
        from db.models import Conversation
        conv = db.query(Conversation).filter_by(id=uid).first()
        if not conv:
            raise ValueError(f"Conversation {uid} not found")
        conv.is_archived = True
        db.commit()
        return True

    @strawberry.mutation(description="Manually create a property with optional units")
    def create_property(self, info, input: CreatePropertyInput) -> HouseType:
        _current_user(info)
        prop, units = PropertyService.create_property(
            _session(info), input.address, input.property_type, input.name,
            input.city, input.state, input.postal_code, input.unit_labels,
        )
        return HouseType.from_new(prop, units)

    @strawberry.mutation(description="Update a property's name, address, or type")
    def update_property(self, info, input: UpdatePropertyInput) -> HouseType:
        _current_user(info)
        today = date.today()
        prop = PropertyService.update_property(_session(info), input)
        return HouseType.from_sql(prop, today)

    @strawberry.mutation(description="Update notes on a unit")
    def update_unit_notes(self, info, input: UpdateUnitNotesInput) -> UnitType:
        _current_user(info)
        db = _session(info)
        unit = PropertyService.update_unit_notes(db, input.uid, input.notes)
        return UnitType(
            uid=str(unit.id),
            label=unit.label,
            is_occupied=False,  # Caller can refetch houses for full status
            notes=unit.notes,
        )

    @strawberry.mutation(description="Delete a property and all its units/leases (cascade)")
    def delete_property(self, info, uid: str) -> bool:
        _current_user(info)
        return PropertyService.delete_property(_session(info), uid)

    @strawberry.mutation(description="Set confirmed_at on a document")
    def confirm_document(self, info, document_id: str) -> bool:
        _current_user(info)
        return DocumentService.confirm_document(_session(info), document_id)

    @strawberry.mutation(description="Attach a new lease to an existing tenant on an existing unit")
    def add_lease_for_tenant(self, info, input: AddLeaseForTenantInput) -> TenantType:
        _current_user(info)
        return TenantType.from_new(*TenantService.add_lease_for_tenant(_session(info), input))

    @strawberry.mutation(description="Create a tenant and attach a lease to an existing unit")
    def create_tenant_with_lease(self, info, input: CreateTenantWithLeaseInput) -> TenantType:
        _current_user(info)
        return TenantType.from_new(*TenantService.create_tenant_with_lease(_session(info), input))

    @strawberry.mutation(description="Assign a vendor to a task")
    def assign_vendor_to_task(self, info, task_id: str, vendor_id: str) -> TaskType:
        _current_user(info)
        db = _session(info)
        from sqlalchemy import select as sa_select
        from db.models import Task as TaskModel, ConversationType
        task = db.execute(
            sa_select(TaskModel).where(TaskModel.id == task_id)
        ).scalar_one_or_none()
        if not task:
            raise ValueError(f"Task {task_id} not found")
        ext_convo = chat_service.get_or_create_external_conversation(
            db,
            conversation_type=ConversationType.VENDOR,
            subject=task.title,
            property_id=task.property_id,
            unit_id=task.unit_id,
            vendor_id=vendor_id,
        )
        task.external_conversation_id = ext_convo.id
        task = TaskService.assign_vendor_to_task(db, task_id, vendor_id)
        db.commit()
        db.refresh(task)
        return TaskType.from_sql(task)

    @strawberry.mutation(description="Create a new vendor contact")
    def create_vendor(self, info, input: CreateVendorInput) -> VendorType:
        _current_user(info)
        return VendorType.from_sql(VendorService.create_vendor(_session(info), input))

    @strawberry.mutation(description="Update an existing vendor contact")
    def update_vendor(self, info, input: UpdateVendorInput) -> VendorType:
        _current_user(info)
        return VendorType.from_sql(VendorService.update_vendor(_session(info), input))

    @strawberry.mutation(description="Delete a vendor contact")
    def delete_vendor(self, info, uid: str) -> bool:
        _current_user(info)
        return VendorService.delete_vendor(_session(info), uid)

    @strawberry.mutation(description="Act on a suggestion (accept, reject, approve draft, etc.)")
    def act_on_suggestion(
        self,
        info,
        uid: str,
        action: str,
        edited_body: typing.Optional[str] = None,
    ) -> SuggestionType:
        _current_user(info)
        db = _session(info)
        from handlers.task_suggestions import SuggestionExecutor
        executor = SuggestionExecutor.for_suggestion(db, uid)
        suggestion, _task = executor.execute(uid, action, edited_body=edited_body)
        db.commit()
        return SuggestionType.from_sql(suggestion)

    @strawberry.mutation(description="Accept a vendor invite (no auth required)")
    def accept_vendor_invite(self, info, token: str) -> bool:
        VendorService.accept_invite(_session(info), token)
        return True

    @strawberry.mutation(description="Spawn a Task from an existing conversation, linking lineage")
    def spawn_task(self, info, input: SpawnTaskInput) -> TaskType:
        _current_user(info)
        sess = _session(info)
        task = _spawn_task(
            sess,
            parent_conversation_id=input.parent_conversation_id,
            objective=input.objective,
            category=input.category,
            urgency=input.urgency,
            priority=input.priority,
            task_mode=input.task_mode,
            source=input.source,
        )
        sess.commit()
        sess.refresh(task)
        return TaskType.from_sql(task)


# ---------------------------
# Schema
# ---------------------------

schema = strawberry.Schema(query=Query, mutation=Mutation, config=StrawberryConfig(auto_camel_case=True))
