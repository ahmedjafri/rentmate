import logging
import typing
from datetime import UTC, date, datetime

import strawberry
from graphql import GraphQLError
from strawberry.schema.config import StrawberryConfig
from strawberry.types import Info

from backends.local_auth import resolve_account_id, resolve_org_id
from db.lib import spawn_task_from_conversation as _spawn_task
from db.queries import (
    fetch_conversations,
    fetch_leases,
    fetch_messages,
    fetch_properties,
    fetch_task,
    fetch_tasks,
    fetch_tenants,
    fetch_vendors,
)

from .auth_mutations import Mutation as AuthMutation
from .services import chat_service
from .services.document_service import DocumentService
from .services.property_service import PropertyService
from .services.task_service import TaskProgressStep, TaskService, dump_task_steps
from .services.tenant_service import TenantService
from .services.vendor_service import VendorService
from .types import (
    VENDOR_TYPES,
    AddDocumentTagInput,
    AddLeaseForTenantInput,
    ChatMessageType,
    ConversationSummaryType,
    ConversationTypeEnum,
    CreatePropertyInput,
    CreateTaskInput,
    CreateTenantWithLeaseInput,
    CreateVendorInput,
    DocumentTagType,
    DocumentType,
    HouseType,
    LeaseType,
    RoutineType,
    SendMessageInput,
    SpawnTaskInput,
    SuggestionStatusEnum,
    SuggestionType,
    SuggestReplyInput,
    SuggestReplyResult,
    TaskCategoryEnum,
    TaskSourceEnum,
    TaskStatusEnum,
    TaskType,
    TenantSearchResult,
    TenantType,
    UpdatePropertyInput,
    UpdateTaskInput,
    UpdateVendorInput,
    UserType,
    VendorType,
)

# ---------------------------
# Context helpers
# ---------------------------

_auth_logger = logging.getLogger("rentmate.auth")
_UNAUTHENTICATED_CODE = "UNAUTHENTICATED"

def _session(info: Info):
    sess = info.context.get("db_session")
    if not sess:
        raise RuntimeError("DB session not found in context (db_session).")
    return sess


def _current_user(info: Info):
    user = info.context.get("user")
    if not user:
        raise GraphQLError("Not authenticated", extensions={"code": _UNAUTHENTICATED_CODE})
    return user


# ---------------------------
# Query
# ---------------------------

@strawberry.type
class Query:

    @strawberry.field(description="Return the current authenticated user")
    def me(self, info: Info) -> UserType:
        user = _current_user(info)
        return UserType(
            uid=str(user.get("uid") or user.get("sub") or ""),
            username=user.get("username") or user.get("email") or "user",
            role="admin",
        )

    @strawberry.field(description="Returns a single document by ID")
    def document(self, info: Info, uid: str) -> typing.Optional[DocumentType]:
        _current_user(info)
        from db.models import Document
        doc = _session(info).query(Document).filter_by(id=uid, org_id=resolve_org_id()).first()
        return DocumentType.from_sql(doc) if doc else None

    @strawberry.field(description="Returns all routines")
    def routines(self, info: Info, *, enabled: typing.Optional[bool] = None) -> typing.List[RoutineType]:
        _current_user(info)
        from db.models import Routine
        db = _session(info)
        q = db.query(Routine).filter_by(
            org_id=resolve_org_id(),
            creator_id=resolve_account_id(),
        ).order_by(Routine.created_at.desc())
        if enabled is not None:
            q = q.filter(Routine.enabled == enabled)
        return [RoutineType.from_sql(st) for st in q.all()]

    @strawberry.field(description="Returns a single routine by ID")
    def routine(self, info: Info, uid: int) -> typing.Optional[RoutineType]:
        _current_user(info)
        from db.models import Routine
        st = _session(info).query(Routine).filter_by(
            id=uid,
            org_id=resolve_org_id(),
            creator_id=resolve_account_id(),
        ).first()
        return RoutineType.from_sql(st) if st else None

    @strawberry.field(description="Get private (per-account) notes for an entity")
    def entity_note(self, info: Info, *, entity_type: str, entity_id: str) -> typing.Optional[str]:
        _current_user(info)
        from db.models import EntityNote
        db = _session(info)
        note = db.query(EntityNote).filter_by(
            creator_id=resolve_account_id(), entity_type=entity_type, entity_id=entity_id,
        ).first()
        return note.content if note else None

    @strawberry.field(description="Returns all properties with their tenants and leases")
    def houses(self, info: Info) -> typing.List[HouseType]:
        _current_user(info)
        today = date.today()
        return [HouseType.from_sql(p, today) for p in fetch_properties(_session(info))]

    @strawberry.field(description="Returns all tenants with their leases and properties")
    def tenants(self, info: Info) -> typing.List[TenantType]:
        _current_user(info)
        today = date.today()
        return [TenantType.from_sql(t, today) for t in fetch_tenants(_session(info))]

    @strawberry.field(description="Returns tasks. Filters: category, status list, source")
    def tasks(
        self,
        info: Info,
        *, category: typing.Optional[TaskCategoryEnum] = None,
        status: typing.Optional[typing.List[TaskStatusEnum]] = None,
        source: typing.Optional[TaskSourceEnum] = None,
    ) -> typing.List[TaskType]:
        _current_user(info)
        return [TaskType.from_sql(c) for c in fetch_tasks(_session(info), category=category, status=status, source=source)]

    @strawberry.field(description="Returns a single task by uid, including its full message thread")
    def task(self, info: Info, uid: int) -> typing.Optional[TaskType]:
        _current_user(info)
        c = fetch_task(_session(info), uid)
        return TaskType.from_sql(c) if c else None

    @strawberry.field(description="Returns all leases with nested tenant and property")
    def leases(self, info: Info) -> typing.List[LeaseType]:
        _current_user(info)
        return [LeaseType.from_sql(l) for l in fetch_leases(_session(info))]

    @strawberry.field(description="Returns messages for a conversation by uid")
    def conversation_messages(self, info: Info, uid: str) -> typing.List[ChatMessageType]:
        _current_user(info)
        return [ChatMessageType.from_sql(m) for m in fetch_messages(_session(info), uid)]

    @strawberry.field(description="Returns metadata for a single conversation (task link, participants).")
    def conversation(self, info: Info, uid: str) -> typing.Optional[ConversationSummaryType]:
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        from db.models import Conversation, ConversationParticipant
        _current_user(info)
        db = _session(info)
        conv = db.execute(
            select(Conversation)
            .where(Conversation.external_id == uid)
            .options(
                selectinload(Conversation.participants).selectinload(ConversationParticipant.user),
                selectinload(Conversation.messages),
                selectinload(Conversation.property),
                selectinload(Conversation.parent_task),
            )
        ).scalar_one_or_none()
        return ConversationSummaryType.from_sql(conv) if conv else None

    @strawberry.field(description="Returns all vendors")
    def vendors(self, info: Info) -> typing.List[VendorType]:
        _current_user(info)
        return [VendorType.from_sql(v) for v in fetch_vendors(_session(info))]

    @strawberry.field(description="Returns the list of valid vendor type labels")
    def vendor_types(self, info: Info) -> typing.List[str]:
        _current_user(info)
        return VENDOR_TYPES

    @strawberry.field(description="Returns suggestions, optionally filtered by status and/or document")
    def suggestions(
        self,
        info: Info,
        *, status: typing.Optional[SuggestionStatusEnum] = None,
        document_id: typing.Optional[str] = None,
        limit: int = 50,
    ) -> typing.List[SuggestionType]:
        _current_user(info)
        from sqlalchemy import select as sa_select
        from sqlalchemy.orm import joinedload

        from db.models import Conversation, Suggestion, Task
        db = _session(info)
        q = sa_select(Suggestion).options(
            joinedload(Suggestion.ai_conversation).selectinload(Conversation.messages),
            joinedload(Suggestion.property),
            joinedload(Suggestion.task)
            .selectinload(Task.external_conversations)
            .selectinload(Conversation.participants),
            joinedload(Suggestion.task)
            .joinedload(Task.parent_conversation)
            .selectinload(Conversation.participants),
        ).where(
            Suggestion.org_id == resolve_org_id(),
            Suggestion.creator_id == resolve_account_id(),
        ).order_by(Suggestion.created_at.desc()).limit(limit)
        if status:
            q = q.where(Suggestion.status == status)
        if document_id:
            q = q.where(Suggestion.document_id == document_id)
        rows = db.execute(q).unique().scalars().all()
        return [SuggestionType.from_sql(s) for s in rows]

    @strawberry.field(description="Returns conversations by type (tenant/vendor/user_ai/task_ai)")
    def conversations(
        self,
        info: Info,
        *, conversation_type: ConversationTypeEnum,
        limit: int = 50,
        offset: int = 0,
    ) -> typing.List[ConversationSummaryType]:
        _current_user(info)
        return [ConversationSummaryType.from_sql(c) for c in fetch_conversations(_session(info), conversation_type=conversation_type, limit=limit, offset=offset)]

    @strawberry.field(description="Fuzzy-match tenants by name / email / phone. Top 3 ranked.")
    def search_tenants(self, info: Info, *, query: str) -> typing.List[TenantSearchResult]:
        from gql.services.extension_service import rank_tenants
        _current_user(info)
        return [TenantSearchResult.from_dict(r) for r in rank_tenants(_session(info), query)]


# ---------------------------
# Mutation
# ---------------------------

@strawberry.type
class Mutation(AuthMutation):

    @strawberry.mutation(description="Delete a tenant and all their leases (cascade)")
    def delete_tenant(self, info: Info, uid: str) -> bool:
        _current_user(info)
        return TenantService.delete_tenant(_session(info), uid)

    @strawberry.mutation(description="Create a new task")
    def create_task(self, info: Info, input: CreateTaskInput) -> TaskType:
        _current_user(info)
        db = _session(info)
        from db.models import ConversationType
        task = TaskService.create_task(db, input)
        chat_service.get_or_create_external_conversation(
            db,
            conversation_type=ConversationType.TENANT,
            subject=input.title,
            property_id=input.property_id,
            unit_id=input.unit_id,
            parent_task_id=task.id,
        )
        db.commit()
        db.refresh(task)
        return TaskType.from_sql(task)

    @strawberry.mutation(description="Transition task_status (e.g. suggested→active, active→resolved)")
    def update_task_status(self, info: Info, *, uid: int, status: TaskStatusEnum) -> TaskType:
        _current_user(info)
        db = _session(info)
        task = TaskService.update_task_status(db, uid=uid, status=status)
        db.commit()
        db.refresh(task)
        return TaskType.from_sql(task)

    @strawberry.mutation(description="Set the manager-authored goal (intent) for a task")
    def update_task_goal(self, info: Info, *, uid: int, goal: str) -> TaskType:
        _current_user(info)
        db = _session(info)
        task = TaskService.update_task_goal(db, uid=uid, goal=goal)
        db.commit()
        db.refresh(task)
        return TaskType.from_sql(task)

    @strawberry.mutation(description="Tag a document to a property, unit, or tenant")
    def add_document_tag(self, info: Info, input: AddDocumentTagInput) -> DocumentTagType:
        _current_user(info)
        return DocumentTagType.from_sql(DocumentService.add_document_tag(_session(info), input))

    @strawberry.mutation(description="Add a message to any conversation")
    def send_message(self, info: Info, input: SendMessageInput) -> ChatMessageType:
        _current_user(info)
        db = _session(info)
        from db.models import Conversation
        conv = db.query(Conversation).filter_by(
            external_id=input.conversation_id,
            org_id=resolve_org_id(),
            creator_id=resolve_account_id(),
        ).first()
        if not conv:
            raise ValueError(f"Conversation {input.conversation_id} not found")
        msg = chat_service.send_message(
            db,
            conversation_id=conv.id,
            body=input.body,
            message_type=input.message_type,
            sender_name=input.sender_name,
            is_ai=input.is_ai,
        )
        # Bump last_message_at on the linked task if any
        from sqlalchemy import or_, select as _sel

        from db.models import Task
        task = db.execute(
            _sel(Task).where(or_(
                Task.ai_conversation_id == conv.id,
                Task.id == conv.parent_task_id,
            ))
        ).scalar_one_or_none()
        if task:
            from datetime import UTC, datetime
            task.last_message_at = datetime.now(UTC)
        db.commit()
        db.refresh(msg)
        return ChatMessageType.from_sql(msg)

    @strawberry.mutation(description="Send a message to a vendor via their notification channel (SMS)")
    def send_sms(self, info: Info, *, vendor_id: str, body: str, task_id: typing.Optional[int] = None) -> ChatMessageType:
        _current_user(info)
        db = _session(info)
        from db.models import ConversationType, Task
        from gql.services.notification_service import NotificationRequest, NotificationService
        from gql.services.vendor_service import get_vendor_by_external_id

        vendor = get_vendor_by_external_id(db, vendor_id)
        if not vendor:
            raise ValueError(f"Vendor {vendor_id} not found")
        if not vendor.phone:
            raise ValueError(f"Vendor {vendor.name} has no phone number")

        # Find or create the vendor conversation for this task
        conv = None
        task = None
        if task_id:
            task = db.query(Task).filter_by(
                id=task_id,
                org_id=resolve_org_id(),
                creator_id=resolve_account_id(),
            ).first()
            if task:
                conv = next(
                    (c for c in task.external_conversations
                     if c.conversation_type == ConversationType.VENDOR),
                    None,
                )
        if not conv:
            conv = chat_service.get_or_create_external_conversation(
                db,
                conversation_type=ConversationType.VENDOR,
                subject=f"SMS with {vendor.name}",
                vendor_id=vendor.id,
                parent_task_id=task.id if task else None,
            )

        # Persist the message
        msg = chat_service.send_message(
            db, conversation_id=conv.id,
            body=body,
            sender_name="You",
            is_ai=False,
        )
        db.commit()
        db.refresh(msg)

        # Deliver to the vendor.
        NotificationService.dispatch(
            db,
            NotificationRequest(
                recipient_user_id=vendor.id,
                conversation_id=conv.id,
                title=f"New message from {vendor.name}'s property manager",
                messages=[body],
                kind="conversation_update",
                task_id=task.id if task else None,
            ),
        )

        return ChatMessageType.from_sql(msg)

    @strawberry.mutation(description="Permanently delete a task and all its messages")
    def delete_task(self, info: Info, uid: int) -> bool:
        _current_user(info)
        db = _session(info)
        result = TaskService.delete_task(db, uid)
        db.commit()
        return result

    @strawberry.mutation(description="Update task mode and/or status")
    def update_task(self, info: Info, input: UpdateTaskInput) -> TaskType:
        _current_user(info)
        db = _session(info)
        task = TaskService.update_task(db, input)
        db.commit()
        db.refresh(task)
        return TaskType.from_sql(task)

    @strawberry.mutation(description="Update the ordered progress steps for a task")
    def update_task_steps(self, info: Info, *, uid: int, steps: strawberry.scalars.JSON) -> TaskType:
        _current_user(info)
        db = _session(info)
        from db.models import Task
        task = db.query(Task).filter_by(
            id=uid,
            org_id=resolve_org_id(),
            creator_id=resolve_account_id(),
        ).first()
        if not task:
            raise ValueError(f"Task {uid} not found")
        typed_steps = [TaskProgressStep.model_validate(s) for s in (steps or [])]
        task.steps = dump_task_steps(typed_steps)
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(task, "steps")
        task.updated_at = datetime.now(UTC)
        db.commit()
        db.refresh(task)
        return TaskType.from_sql(task)

    @strawberry.mutation(description="Mark a task as seen in the tasks view")
    def mark_task_seen(self, info: Info, uid: int) -> TaskType:
        _current_user(info)
        db = _session(info)
        task = TaskService.mark_task_seen(db, uid=uid)
        db.commit()
        db.refresh(task)
        return TaskType.from_sql(task)

    @strawberry.mutation(description="Archive (soft-delete) a conversation")
    def delete_conversation(self, info: Info, uid: str) -> bool:
        _current_user(info)
        db = _session(info)
        from db.models import Conversation
        conv = db.query(Conversation).filter_by(
            external_id=uid,
            org_id=resolve_org_id(),
            creator_id=resolve_account_id(),
        ).first()
        if not conv:
            raise ValueError(f"Conversation {uid} not found")
        conv.is_archived = True
        db.commit()
        return True

    @strawberry.mutation(description="Manually create a property with optional units")
    def create_property(self, info: Info, input: CreatePropertyInput) -> HouseType:
        _current_user(info)
        prop, units = PropertyService.create_property(
            _session(info),
            address=input.address,
            property_type=input.property_type,
            name=input.name,
            city=input.city,
            state=input.state,
            postal_code=input.postal_code,
            unit_labels=input.unit_labels,
        )
        return HouseType.from_new(prop, units)

    @strawberry.mutation(description="Update a property's name, address, or type")
    def update_property(self, info: Info, input: UpdatePropertyInput) -> HouseType:
        _current_user(info)
        today = date.today()
        prop = PropertyService.update_property(_session(info), input)
        return HouseType.from_sql(prop, today)

    @strawberry.mutation(description="Update the agent context for any entity (property, unit, tenant, vendor)")
    def update_entity_context(self, info: Info, *, entity_type: str, entity_id: str, context: str) -> bool:
        _current_user(info)
        db = _session(info)
        _MODEL_MAP = {
            "property": "Property",
            "unit": "Unit",
            "tenant": "Tenant",
            "vendor": "User",
        }
        model_name = _MODEL_MAP.get(entity_type)
        if not model_name:
            raise ValueError(f"Unknown entity type: {entity_type}")
        import db.models as models
        model_cls = getattr(models, model_name)
        if hasattr(model_cls, 'external_id'):
            filters = {"external_id": entity_id, "org_id": resolve_org_id()}
            if entity_type == "vendor":
                filters["user_type"] = "vendor"
            entity = db.query(model_cls).filter_by(**filters).first()
        else:
            entity = db.query(model_cls).filter_by(id=entity_id, org_id=resolve_org_id()).first()
        if not entity:
            raise ValueError(f"{entity_type} {entity_id} not found")
        entity.context = context or None
        db.commit()
        return True

    @strawberry.mutation(description="Save private (per-account) notes for an entity")
    def save_entity_note(self, info: Info, *, entity_type: str, entity_id: str, content: str) -> bool:
        _current_user(info)
        if entity_type == "document":
            raise ValueError("Private notes are not supported for documents — use shared context instead")
        db = _session(info)
        from datetime import UTC, datetime

        from db.models import EntityNote
        creator_id = resolve_account_id()
        note = db.query(EntityNote).filter_by(
            creator_id=creator_id, entity_type=entity_type, entity_id=entity_id,
        ).first()
        if content.strip():
            if note:
                note.content = content.strip()
                note.updated_at = datetime.now(UTC)
            else:
                db.add(EntityNote(
                    creator_id=creator_id,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    content=content.strip(),
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                ))
        elif note:
            db.delete(note)
        db.commit()
        return True

    @strawberry.mutation(description="Create a routine")
    def create_routine(
        self, info: Info, *, name: str, prompt: str, schedule: str, repeat: typing.Optional[int] = None,
    ) -> RoutineType:
        _current_user(info)
        from datetime import UTC, datetime

        from db.enums import RoutineState
        from db.models import Routine
        from gql.services.number_allocator import NumberAllocator
        from handlers.routines import human_schedule, next_run, parse_schedule

        db = _session(info)
        cron_expr = parse_schedule(schedule)
        org_id = resolve_org_id()
        st = Routine(
            id=NumberAllocator.allocate_next(db, entity_type="routine", org_id=org_id),
            org_id=org_id,
            creator_id=resolve_account_id(),
            name=name, prompt=prompt,
            schedule=cron_expr,
            schedule_display=human_schedule(cron_expr),
            enabled=True, state=RoutineState.SCHEDULED,
            repeat=repeat,
            next_run_at=next_run(cron_expr),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.add(st)
        db.commit()
        return RoutineType.from_sql(st)

    @strawberry.mutation(description="Update a routine")
    def update_routine(
        self, info: Info, uid: int, *,
        name: typing.Optional[str] = None,
        prompt: typing.Optional[str] = None,
        schedule: typing.Optional[str] = None,
        enabled: typing.Optional[bool] = None,
    ) -> RoutineType:
        _current_user(info)
        from datetime import UTC, datetime

        from db.enums import RoutineState
        from db.models import Routine
        from handlers.routines import human_schedule, next_run, parse_schedule

        db = _session(info)
        st = db.query(Routine).filter_by(
            id=uid,
            org_id=resolve_org_id(),
            creator_id=resolve_account_id(),
        ).first()
        if not st:
            raise ValueError("Routine not found")
        if name is not None:
            st.name = name
        if prompt is not None:
            st.prompt = prompt
        if schedule is not None:
            st.schedule = parse_schedule(schedule)
            st.schedule_display = human_schedule(st.schedule)
            st.next_run_at = next_run(st.schedule)
        if enabled is not None:
            if enabled and not st.simulated_at:
                raise ValueError("Cannot enable a routine that hasn't been simulated yet. Run a simulation first.")
            st.enabled = enabled
            st.state = RoutineState.SCHEDULED if enabled else RoutineState.PAUSED
        st.updated_at = datetime.now(UTC)
        db.commit()
        return RoutineType.from_sql(st)

    @strawberry.mutation(description="Delete a routine")
    def delete_routine(self, info: Info, uid: int) -> bool:
        _current_user(info)
        from db.models import Routine
        db = _session(info)
        st = db.query(Routine).filter_by(
            id=uid,
            org_id=resolve_org_id(),
            creator_id=resolve_account_id(),
        ).first()
        if not st:
            raise ValueError("Routine not found")
        db.delete(st)
        db.commit()
        return True

    @strawberry.mutation(description="Run a routine immediately and return its output")
    async def run_routine(self, info: Info, uid: int) -> RoutineType:
        _current_user(info)
        from db.models import Routine
        from handlers.routines import execute_routine
        db = _session(info)
        st = db.query(Routine).filter_by(
            id=uid,
            org_id=resolve_org_id(),
            creator_id=resolve_account_id(),
        ).first()
        if not st:
            raise ValueError("Routine not found")
        from datetime import UTC, datetime
        try:
            output = await execute_routine(st)
            st.last_status = "ok"
            st.last_output = output[:5000] if output else ""
        except Exception as exc:
            st.last_status = "error"
            st.last_output = str(exc)[:2000]
        st.last_run_at = datetime.now(UTC)
        st.updated_at = datetime.now(UTC)
        db.commit()
        return RoutineType.from_sql(st)

    @strawberry.mutation(description="Simulate a routine — dry run that returns what the agent would do")
    async def simulate_routine(self, info: Info, uid: int) -> str:
        """Run the routine prompt through the agent but prefix with [SIMULATION] so the
        agent creates suggestions instead of taking direct action."""
        _current_user(info)
        from db.models import Routine
        from handlers.routines import execute_routine
        db = _session(info)
        st = db.query(Routine).filter_by(
            id=uid,
            org_id=resolve_org_id(),
            creator_id=resolve_account_id(),
        ).first()
        if not st:
            raise ValueError("Routine not found")

        sim_prompt = (
            "[SIMULATION — do NOT take direct action. Instead of creating entities "
            "or sending messages, describe what you WOULD do and create suggestions "
            "for each action.]\n\n" + st.prompt
        )
        try:
            output = await execute_routine(st, prompt_override=sim_prompt)
            from datetime import UTC, datetime
            st.simulated_at = datetime.now(UTC)
            db.commit()
            return output or "(no output)"
        except Exception as exc:
            return f"Simulation error: {exc}"

    @strawberry.mutation(description="Delete a property and all its units/leases (cascade)")
    def delete_property(self, info: Info, uid: str) -> bool:
        _current_user(info)
        return PropertyService.delete_property(_session(info), uid)

    @strawberry.mutation(description="Set confirmed_at on a document")
    def confirm_document(self, info: Info, document_id: str) -> bool:
        _current_user(info)
        return DocumentService.confirm_document(_session(info), document_id)

    @strawberry.mutation(description="Attach a new lease to an existing tenant on an existing unit")
    def add_lease_for_tenant(self, info: Info, input: AddLeaseForTenantInput) -> TenantType:
        _current_user(info)
        return TenantType.from_new(*TenantService.add_lease_for_tenant(_session(info), input))

    @strawberry.mutation(description="Create a tenant and attach a lease to an existing unit")
    def create_tenant_with_lease(self, info: Info, input: CreateTenantWithLeaseInput) -> TenantType:
        _current_user(info)
        return TenantType.from_new(*TenantService.create_tenant_with_lease(_session(info), input))

    @strawberry.mutation(description="Assign a vendor to a task")
    def assign_vendor_to_task(self, info: Info, *, task_id: int, vendor_id: str) -> TaskType:
        _current_user(info)
        db = _session(info)
        from sqlalchemy import select as sa_select

        from db.models import ConversationType, Task as TaskModel
        from gql.services.vendor_service import get_vendor_by_external_id
        task = db.execute(
            sa_select(TaskModel).where(
                TaskModel.id == task_id,
                TaskModel.org_id == resolve_org_id(),
                TaskModel.creator_id == resolve_account_id(),
            )
        ).scalar_one_or_none()
        if not task:
            raise ValueError(f"Task {task_id} not found")
        vendor = get_vendor_by_external_id(db, vendor_id)
        if not vendor:
            raise ValueError(f"Vendor {vendor_id} not found")
        chat_service.get_or_create_external_conversation(
            db,
            conversation_type=ConversationType.VENDOR,
            subject=task.title,
            property_id=task.property_id,
            unit_id=task.unit_id,
            vendor_id=vendor.id,
            parent_task_id=task.id,
        )
        task = TaskService.assign_vendor_to_task(db, task_id=task_id, vendor_id=vendor.id)
        db.commit()
        db.refresh(task)
        return TaskType.from_sql(task)

    @strawberry.mutation(description="Create a new vendor contact")
    def create_vendor(self, info: Info, input: CreateVendorInput) -> VendorType:
        _current_user(info)
        return VendorType.from_sql(VendorService.create_vendor(_session(info), input))

    @strawberry.mutation(description="Update an existing vendor contact")
    def update_vendor(self, info: Info, input: UpdateVendorInput) -> VendorType:
        _current_user(info)
        return VendorType.from_sql(VendorService.update_vendor(_session(info), input))

    @strawberry.mutation(description="Delete a vendor contact")
    def delete_vendor(self, info: Info, uid: str) -> bool:
        _current_user(info)
        return VendorService.delete_vendor(_session(info), uid)

    @strawberry.mutation(description="Act on a suggestion (accept, reject, approve draft, etc.)")
    def act_on_suggestion(
        self,
        info: Info,
        *, uid: int,
        action: str,
        edited_body: typing.Optional[str] = None,
    ) -> SuggestionType:
        _current_user(info)
        db = _session(info)
        from gql.services.task_suggestions import SuggestionExecutor
        executor = SuggestionExecutor.for_suggestion(db, uid)
        suggestion, _task = executor.execute(uid, action, edited_body=edited_body)
        db.commit()
        return SuggestionType.from_sql(suggestion)

    @strawberry.mutation(description="Spawn a Task from an existing conversation, linking lineage")
    def spawn_task(self, info: Info, input: SpawnTaskInput) -> TaskType:
        _current_user(info)
        sess = _session(info)
        from db.models import Conversation as ConvModel
        parent_conv = sess.query(ConvModel).filter_by(
            external_id=input.parent_conversation_id,
            org_id=resolve_org_id(),
            creator_id=resolve_account_id(),
        ).first()
        if not parent_conv:
            raise ValueError(f"Conversation {input.parent_conversation_id} not found")
        task = _spawn_task(
            sess,
            parent_conversation_id=parent_conv.id,
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

    @strawberry.mutation(description=(
        "One-shot agent-drafted reply for a TenantCloud conversation viewed "
        "in the chrome extension. Pure read on rentmate data — no DB writes."
    ))
    async def suggest_reply(self, info: Info, *, input: SuggestReplyInput) -> SuggestReplyResult:
        from gql.services.extension_service import draft_reply
        _current_user(info)
        result = await draft_reply(
            _session(info),
            conversation_history=[
                {"sender": turn.sender, "text": turn.text}
                for turn in (input.conversation_history or [])
            ],
            header_title=input.header_title,
            header_description=input.header_description,
            tenant_id=input.tenant_id,
            property_id=input.property_id,
        )
        matched = result.get("matched_tenant")
        return SuggestReplyResult(
            suggestion=result["suggestion"],
            matched_tenant=TenantSearchResult.from_dict(matched) if matched else None,
        )


# ---------------------------
# Schema
# ---------------------------


class RentmateSchema(strawberry.Schema):
    def process_errors(self, errors, execution_context=None) -> None:
        for error in errors:
            if (error.extensions or {}).get("code") == _UNAUTHENTICATED_CODE:
                _auth_logger.info("GraphQL unauthenticated request: %s", error.message)
                continue
            super().process_errors([error], execution_context)


schema = RentmateSchema(query=Query, mutation=Mutation, config=StrawberryConfig(auto_camel_case=True))
