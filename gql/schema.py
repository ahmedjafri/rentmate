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
    UserType, HouseType, TenantType, LeaseType, TaskType,
    TaskChatMessageType, DocumentTagType, ConversationSummaryType, SpawnTaskInput,
    CreateTaskInput, AddDocumentTagInput, AddTaskMessageInput, UpdateTaskInput,
    CreatePropertyInput, UpdatePropertyInput, CreateTenantWithLeaseInput, AddLeaseForTenantInput,
    VendorType, CreateVendorInput, UpdateVendorInput, VENDOR_TYPES,
)
from .services.task_service import TaskService
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
        today = date.today()
        return [HouseType.from_sql(p, today) for p in fetch_properties(_session(info))]

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
    def conversation_messages(self, info, uid: str) -> typing.List[TaskChatMessageType]:
        _current_user(info)
        return [TaskChatMessageType.from_sql(m) for m in fetch_messages(_session(info), uid)]

    @strawberry.field(description="Returns all vendors")
    def vendors(self, info) -> typing.List[VendorType]:
        _current_user(info)
        return [VendorType.from_sql(v) for v in fetch_vendors(_session(info))]

    @strawberry.field(description="Returns the list of valid vendor type labels")
    def vendor_types(self, info) -> typing.List[str]:
        _current_user(info)
        return VENDOR_TYPES

    @strawberry.field(description="Returns conversations by type (tenant/vendor/user_ai/task)")
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
        return TaskType.from_sql(TaskService.create_task(_session(info), input))

    @strawberry.mutation(description="Transition task_status (e.g. suggested→active, active→resolved)")
    def update_task_status(self, info, uid: str, status: str) -> TaskType:
        _current_user(info)
        return TaskType.from_sql(TaskService.update_task_status(_session(info), uid, status))

    @strawberry.mutation(description="Tag a document to a property, unit, or tenant")
    def add_document_tag(self, info, input: AddDocumentTagInput) -> DocumentTagType:
        _current_user(info)
        return DocumentTagType.from_sql(DocumentService.add_document_tag(_session(info), input))

    @strawberry.mutation(description="Add a manager message to a task's chat thread")
    def add_task_message(self, info, input: AddTaskMessageInput) -> TaskChatMessageType:
        _current_user(info)
        return TaskChatMessageType.from_sql(TaskService.add_task_message(_session(info), input))

    @strawberry.mutation(description="Permanently delete a task and all its messages")
    def delete_task(self, info, uid: str) -> bool:
        _current_user(info)
        return TaskService.delete_task(_session(info), uid)

    @strawberry.mutation(description="Update task mode and/or status")
    def update_task(self, info, input: UpdateTaskInput) -> TaskType:
        _current_user(info)
        return TaskType.from_sql(TaskService.update_task(_session(info), input))

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
        return TaskType.from_sql(TaskService.assign_vendor_to_task(_session(info), task_id, vendor_id))

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
