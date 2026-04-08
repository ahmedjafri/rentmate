# tests/test_task_features.py
"""
Comprehensive tests for the unified Task model, GraphQL queries/mutations,
and DocumentTag/DocumentTask models.

Covers:
- tasks query (no filter, category filter, status filter, comma-separated status, source filter)
- tasks query derived fields: tenant_name, unit_label, ai_triage_suggestion, vendor_assigned
- paymentStatus exposed on TenantType via tenants query
- createTask mutation
- updateTaskStatus mutation
- updateTask mutation (mode + status)
- sendMessage mutation
- addDocumentTag mutation
- confirmDocument mutation
- DocumentTask model (create, unique constraint)
- DocumentTag model (create)
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from db.models import (
    Conversation,
    Document,
    DocumentTag,
    DocumentTask,
    Lease,
    Message,
    ParticipantType,
    Property,
    Task,
    Tenant,
    Unit,
)
from gql.schema import schema

DEFAULT_ACCOUNT_ID = "00000000-0000-0000-0000-000000000001"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_USER = {"id": "test-user-id", "email": "admin@test.com"}


def _gql_context(db):
    return {"db_session": db, "user": FAKE_USER}


def _mk_property(db, name="Prop", address="1 Main St"):
    prop = Property(name=name, address_line1=address, city="Seattle", state="WA")
    db.add(prop)
    db.flush()
    return prop


def _mk_unit(db, prop, label="101"):
    unit = Unit(property_id=prop.id, label=label)
    db.add(unit)
    db.flush()
    return unit


def _mk_tenant(db, first="Alice", last="Renter", email=None):
    t = Tenant(first_name=first, last_name=last, email=email)
    db.add(t)
    db.flush()
    return t


def _mk_lease(db, prop, unit, tenant, payment_status="current"):
    lease = Lease(
        tenant_id=tenant.id,
        unit_id=unit.id,
        property_id=prop.id,
        start_date=date.today(),
        end_date=date.today() + timedelta(days=365),
        rent_amount=1500.0,
        payment_status=payment_status,
    )
    db.add(lease)
    db.flush()
    return lease


def _mk_task(
    db,
    subject="Fix something",
    task_status="active",
    category="maintenance",
    source="manual",
    urgency=None,
    priority=None,
    task_mode=None,
    confidential=False,
    lease=None,
    unit=None,
    prop=None,
):
    task = Task(
        account_id=DEFAULT_ACCOUNT_ID,
        title=subject,
        task_status=task_status,
        category=category,
        source=source,
        urgency=urgency,
        priority=priority,
        task_mode=task_mode,
        confidential=confidential,
        lease_id=lease.id if lease else None,
        unit_id=unit.id if unit else None,
        property_id=prop.id if prop else None,
    )
    db.add(task)
    db.flush()
    conv = Conversation(
        subject=subject,
        lease_id=lease.id if lease else None,
        unit_id=unit.id if unit else None,
        property_id=prop.id if prop else None,
    )
    db.add(conv)
    db.flush()
    task.ai_conversation_id = conv.id
    ext_conv = Conversation(
        subject=subject,
        lease_id=lease.id if lease else None,
        unit_id=unit.id if unit else None,
        property_id=prop.id if prop else None,
    )
    db.add(ext_conv)
    db.flush()
    task.external_conversation_id = ext_conv.id
    return task


def _mk_document(db, filename="lease.pdf"):
    doc = Document(filename=filename, document_type="lease", status="pending")
    db.add(doc)
    db.flush()
    return doc


def _add_message(
    db,
    conv_or_task,
    body="hello",
    sender_type=ParticipantType.ACCOUNT_USER,
    message_type="message",
    sender_name="Manager",
    is_ai=False,
):
    # Accept either a Conversation or a Task (use its AI conversation)
    if isinstance(conv_or_task, Task):
        conv_id = conv_or_task.ai_conversation.id
    else:
        conv_id = conv_or_task.id
    msg = Message(
        conversation_id=conv_id,
        sender_type=sender_type,
        body=body,
        message_type=message_type,
        sender_name=sender_name,
        is_ai=is_ai,
        is_system=False,
    )
    db.add(msg)
    db.flush()
    return msg


# ---------------------------------------------------------------------------
# tasks query — basic fetching and filtering
# ---------------------------------------------------------------------------

class TestTasksQuery:

    def test_tasks_returns_empty_when_none(self, db):
        result = schema.execute_sync(
            "{ tasks { uid title } }",
            context_value=_gql_context(db),
        )
        assert result.errors is None
        assert result.data["tasks"] == []

    def test_tasks_returns_only_tasks_not_plain_conversations(self, db):
        # Plain conversation (no linked task) — should not appear in tasks query
        plain_conv = Conversation(subject="Chat")
        db.add(plain_conv)
        db.flush()

        task = _mk_task(db, subject="Leaky faucet")

        result = schema.execute_sync(
            "{ tasks { uid title } }",
            context_value=_gql_context(db),
        )
        assert result.errors is None
        uids = [t["uid"] for t in result.data["tasks"]]
        assert task.id in uids
        assert plain_conv.id not in uids

    def test_tasks_filter_by_category(self, db):
        _mk_task(db, subject="Rent overdue", category="rent")
        maint = _mk_task(db, subject="Broken pipe", category="maintenance")

        result = schema.execute_sync(
            '{ tasks(category: "maintenance") { uid title category } }',
            context_value=_gql_context(db),
        )
        assert result.errors is None
        tasks = result.data["tasks"]
        assert len(tasks) == 1
        assert tasks[0]["uid"] == maint.id
        assert tasks[0]["category"] == "maintenance"

    def test_tasks_filter_by_single_status(self, db):
        active = _mk_task(db, subject="Active task", task_status="active")
        _mk_task(db, subject="Resolved task", task_status="resolved")

        result = schema.execute_sync(
            '{ tasks(status: "active") { uid taskStatus } }',
            context_value=_gql_context(db),
        )
        assert result.errors is None
        tasks = result.data["tasks"]
        assert len(tasks) == 1
        assert tasks[0]["uid"] == active.id

    def test_tasks_filter_by_comma_separated_statuses(self, db):
        t1 = _mk_task(db, subject="Task A", task_status="active")
        t2 = _mk_task(db, subject="Task B", task_status="suggested")
        _mk_task(db, subject="Task C", task_status="resolved")

        result = schema.execute_sync(
            '{ tasks(status: "active,suggested") { uid taskStatus } }',
            context_value=_gql_context(db),
        )
        assert result.errors is None
        uids = {t["uid"] for t in result.data["tasks"]}
        assert t1.id in uids
        assert t2.id in uids
        assert len(uids) == 2

    def test_tasks_filter_by_source(self, db):
        _mk_task(db, subject="AI suggestion", source="ai_suggestion")
        manual = _mk_task(db, subject="Manual task", source="manual")

        result = schema.execute_sync(
            '{ tasks(source: "manual") { uid source } }',
            context_value=_gql_context(db),
        )
        assert result.errors is None
        tasks = result.data["tasks"]
        assert len(tasks) == 1
        assert tasks[0]["uid"] == manual.id
        assert tasks[0]["source"] == "manual"

    def test_tasks_returns_all_task_fields(self, db):
        task = _mk_task(
            db,
            subject="Urgent repair",
            task_status="active",
            category="maintenance",
            source="tenant_report",
            urgency="high",
            priority="urgent",
            task_mode="autonomous",
            confidential=True,
        )

        result = schema.execute_sync(
            """{ tasks {
                uid title taskStatus taskMode source
                category urgency priority confidential
                createdAt
            } }""",
            context_value=_gql_context(db),
        )
        assert result.errors is None
        t = result.data["tasks"][0]
        assert t["uid"] == task.id
        assert t["title"] == "Urgent repair"
        assert t["taskStatus"] == "active"
        assert t["taskMode"] == "autonomous"
        assert t["source"] == "tenant_report"
        assert t["category"] == "maintenance"
        assert t["urgency"] == "high"
        assert t["priority"] == "urgent"
        assert t["confidential"] is True
        assert t["createdAt"] != ""

    def test_tasks_includes_messages(self, db):
        task = _mk_task(db, subject="Task with messages")
        _add_message(db, task, body="First note", sender_name="Manager")
        _add_message(db, task, body="Second note", sender_name="Manager")

        result = schema.execute_sync(
            "{ tasks { uid messages { uid body senderName } } }",
            context_value=_gql_context(db),
        )
        assert result.errors is None
        task_data = result.data["tasks"][0]
        assert len(task_data["messages"]) == 2
        bodies = [m["body"] for m in task_data["messages"]]
        assert "First note" in bodies
        assert "Second note" in bodies

    def test_tasks_includes_messages_after_gql_create_and_add(self, db):
        """Full GQL round-trip: create task, add context message, re-query tasks."""
        # 1. Create task via mutation
        create_result = schema.execute_sync(
            """mutation CreateTask($input: CreateTaskInput!) {
                createTask(input: $input) { uid }
            }""",
            context_value=_gql_context(db),
            variable_values={"input": {"title": "Gutter cleaning", "source": "ai_suggestion"}},
        )
        assert create_result.errors is None
        task_uid = create_result.data["createTask"]["uid"]

        # 2. Get the task's AI conversation ID
        task_obj = db.execute(select(Task).where(Task.id == task_uid)).scalar_one()
        ai_convo_id = task_obj.ai_conversation_id

        # 3. Add context message via mutation
        msg_result = schema.execute_sync(
            """mutation SendMsg($input: SendMessageInput!) {
                sendMessage(input: $input) { uid body messageType }
            }""",
            context_value=_gql_context(db),
            variable_values={"input": {
                "conversationId": ai_convo_id,
                "body": "Gutters need cleaning before winter.",
                "messageType": "context",
                "senderName": "RentMate",
                "isAi": True,
            }},
        )
        assert msg_result.errors is None
        assert msg_result.data["sendMessage"]["messageType"] == "context"

        # 3. Re-query via tasks query — message must be present
        query_result = schema.execute_sync(
            "{ tasks { uid messages { uid body messageType senderName isAi } } }",
            context_value=_gql_context(db),
        )
        assert query_result.errors is None
        task_data = next(t for t in query_result.data["tasks"] if t["uid"] == task_uid)
        assert len(task_data["messages"]) >= 1
        ctx = [m for m in task_data["messages"] if m["messageType"] == "context"]
        assert len(ctx) == 1
        assert "Gutters need cleaning" in ctx[0]["body"]
        assert ctx[0]["isAi"] is True

    def test_tasks_derives_tenant_name_from_lease(self, db):
        prop = _mk_property(db)
        unit = _mk_unit(db, prop, "202")
        tenant = _mk_tenant(db, first="John", last="Smith")
        lease = _mk_lease(db, prop, unit, tenant)
        task = _mk_task(db, subject="Rent issue", lease=lease)

        result = schema.execute_sync(
            "{ tasks { uid tenantName } }",
            context_value=_gql_context(db),
        )
        assert result.errors is None
        t = result.data["tasks"][0]
        assert t["uid"] == task.id
        assert t["tenantName"] == "John Smith"

    def test_tasks_derives_unit_label_from_unit_fk(self, db):
        prop = _mk_property(db)
        unit = _mk_unit(db, prop, "303")
        task = _mk_task(db, subject="Unit task", unit=unit)

        result = schema.execute_sync(
            "{ tasks { uid unitLabel } }",
            context_value=_gql_context(db),
        )
        assert result.errors is None
        assert result.data["tasks"][0]["unitLabel"] == "303"

    def test_tasks_derives_unit_label_from_lease_fallback(self, db):
        prop = _mk_property(db)
        unit = _mk_unit(db, prop, "404")
        tenant = _mk_tenant(db)
        lease = _mk_lease(db, prop, unit, tenant)
        # No direct unit_id on the task — should fall back to lease.unit
        task = _mk_task(db, subject="Lease fallback task", lease=lease, unit=None)

        result = schema.execute_sync(
            "{ tasks { uid unitLabel } }",
            context_value=_gql_context(db),
        )
        assert result.errors is None
        assert result.data["tasks"][0]["unitLabel"] == "404"

    def test_tasks_ai_triage_suggestion_from_context_message(self, db):
        task = _mk_task(db, subject="AI triaged task")
        _add_message(db, task, body="Regular message", message_type="message")
        _add_message(db, task, body="AI context text here", message_type="context")

        result = schema.execute_sync(
            "{ tasks { uid aiTriageSuggestion } }",
            context_value=_gql_context(db),
        )
        assert result.errors is None
        assert result.data["tasks"][0]["aiTriageSuggestion"] == "AI context text here"

    def test_tasks_ai_triage_suggestion_none_when_no_context_message(self, db):
        task = _mk_task(db, subject="No context task")
        _add_message(db, task, body="Just a message", message_type="message")

        result = schema.execute_sync(
            "{ tasks { uid aiTriageSuggestion } }",
            context_value=_gql_context(db),
        )
        assert result.errors is None
        assert result.data["tasks"][0]["aiTriageSuggestion"] is None

    def test_tasks_vendor_assigned_from_external_contact_message(self, db):
        task = _mk_task(db, subject="Vendor task")
        msg = Message(
            conversation_id=task.ai_conversation.id,
            sender_type=ParticipantType.EXTERNAL_CONTACT,
            body="I'll fix it",
            sender_name="Bob's Plumbing",
            is_system=False,
        )
        db.add(msg)
        db.flush()

        result = schema.execute_sync(
            "{ tasks { uid vendorAssigned } }",
            context_value=_gql_context(db),
        )
        assert result.errors is None
        assert result.data["tasks"][0]["vendorAssigned"] == "Bob's Plumbing"

    def test_tasks_unauthenticated_raises_error(self, db):
        result = schema.execute_sync(
            "{ tasks { uid } }",
            context_value={"db_session": db, "user": None},
        )
        assert result.errors is not None
        assert len(result.errors) > 0


# ---------------------------------------------------------------------------
# paymentStatus on tenants query
# ---------------------------------------------------------------------------

class TestTenantsPaymentStatus:

    def _find_tenant(self, result_tenants, uid):
        """Helper: find a specific tenant by uid among all returned tenants."""
        return next((t for t in result_tenants if t["uid"] == uid), None)

    def test_tenants_query_exposes_payment_status_current(self, db):
        prop = _mk_property(db)
        unit = _mk_unit(db, prop)
        tenant = _mk_tenant(db, first="Eve", last="Current")
        _mk_lease(db, prop, unit, tenant, payment_status="current")

        result = schema.execute_sync(
            "{ tenants { uid name paymentStatus } }",
            context_value=_gql_context(db),
        )
        assert result.errors is None
        found = self._find_tenant(result.data["tenants"], tenant.id)
        assert found is not None
        assert found["paymentStatus"] == "current"

    def test_tenants_query_exposes_payment_status_overdue(self, db):
        prop = _mk_property(db)
        unit = _mk_unit(db, prop)
        tenant = _mk_tenant(db, first="Frank", last="Overdue")
        _mk_lease(db, prop, unit, tenant, payment_status="overdue")

        result = schema.execute_sync(
            "{ tenants { uid name paymentStatus } }",
            context_value=_gql_context(db),
        )
        assert result.errors is None
        found = self._find_tenant(result.data["tenants"], tenant.id)
        assert found is not None
        assert found["paymentStatus"] == "overdue"

    def test_tenants_query_exposes_payment_status_late(self, db):
        prop = _mk_property(db)
        unit = _mk_unit(db, prop)
        tenant = _mk_tenant(db, first="Grace", last="Late")
        _mk_lease(db, prop, unit, tenant, payment_status="late")

        result = schema.execute_sync(
            "{ tenants { uid name paymentStatus } }",
            context_value=_gql_context(db),
        )
        assert result.errors is None
        found = self._find_tenant(result.data["tenants"], tenant.id)
        assert found is not None
        assert found["paymentStatus"] == "late"


# ---------------------------------------------------------------------------
# createTask mutation
# ---------------------------------------------------------------------------

class TestCreateTaskMutation:

    CREATE_TASK_MUTATION = """
    mutation CreateTask($input: CreateTaskInput!) {
        createTask(input: $input) {
            uid title taskStatus taskMode source
            category urgency priority confidential
        }
    }
    """

    def test_create_task_minimal(self, db):
        result = schema.execute_sync(
            self.CREATE_TASK_MUTATION,
            context_value=_gql_context(db),
            variable_values={"input": {"title": "Fix roof", "source": "manual"}},
        )
        assert result.errors is None
        task = result.data["createTask"]
        assert task["uid"] is not None
        assert task["title"] == "Fix roof"
        assert task["taskStatus"] == "active"
        assert task["source"] == "manual"
        assert task["confidential"] is False

    def test_create_task_with_all_fields(self, db):
        result = schema.execute_sync(
            self.CREATE_TASK_MUTATION,
            context_value=_gql_context(db),
            variable_values={
                "input": {
                    "title": "Inspect unit",
                    "source": "ai_suggestion",
                    "taskStatus": "suggested",
                    "category": "maintenance",
                    "urgency": "high",
                    "priority": "urgent",
                    "taskMode": "autonomous",
                    "confidential": True,
                }
            },
        )
        assert result.errors is None
        task = result.data["createTask"]
        assert task["title"] == "Inspect unit"
        assert task["taskStatus"] == "suggested"
        assert task["category"] == "maintenance"
        assert task["urgency"] == "high"
        assert task["priority"] == "urgent"
        assert task["taskMode"] == "autonomous"
        assert task["confidential"] is True

    def test_create_task_persists_to_db(self, db):
        result = schema.execute_sync(
            self.CREATE_TASK_MUTATION,
            context_value=_gql_context(db),
            variable_values={"input": {"title": "Check boiler", "source": "tenant_report"}},
        )
        assert result.errors is None
        uid = result.data["createTask"]["uid"]

        db.expire_all()
        from sqlalchemy import select
        task = db.execute(select(Task).where(Task.id == uid)).scalar_one_or_none()
        assert task is not None
        assert task.title == "Check boiler"
        assert task.source == "tenant_report"

    def test_create_task_sets_external_conversation_id(self, db):
        result = schema.execute_sync(
            """
            mutation CreateTask($input: CreateTaskInput!) {
                createTask(input: $input) {
                    uid externalConversationId
                }
            }
            """,
            context_value=_gql_context(db),
            variable_values={"input": {"title": "Pipe leak", "source": "manual"}},
        )
        assert result.errors is None
        task = result.data["createTask"]
        assert task["externalConversationId"] is not None

        # Verify the DB task has distinct ai and external conversations
        from sqlalchemy import select
        db.expire_all()
        db_task = db.execute(select(Task).where(Task.id == task["uid"])).scalar_one()
        assert db_task.ai_conversation_id is not None
        assert db_task.external_conversation_id is not None
        assert db_task.ai_conversation_id != db_task.external_conversation_id

    def test_create_task_unauthenticated_fails(self, db):
        result = schema.execute_sync(
            self.CREATE_TASK_MUTATION,
            context_value={"db_session": db, "user": None},
            variable_values={"input": {"title": "Unauthorized", "source": "manual"}},
        )
        assert result.errors is not None


# ---------------------------------------------------------------------------
# updateTaskStatus mutation
# ---------------------------------------------------------------------------

class TestUpdateTaskStatusMutation:

    UPDATE_STATUS_MUTATION = """
    mutation UpdateStatus($uid: String!, $status: String!) {
        updateTaskStatus(uid: $uid, status: $status) {
            uid taskStatus
        }
    }
    """

    def test_update_task_status_active_to_resolved(self, db):
        task = _mk_task(db, task_status="active")

        result = schema.execute_sync(
            self.UPDATE_STATUS_MUTATION,
            context_value=_gql_context(db),
            variable_values={"uid": task.id, "status": "resolved"},
        )
        assert result.errors is None
        assert result.data["updateTaskStatus"]["uid"] == task.id
        assert result.data["updateTaskStatus"]["taskStatus"] == "resolved"

    def test_update_task_status_suggested_to_active(self, db):
        task = _mk_task(db, task_status="suggested")

        result = schema.execute_sync(
            self.UPDATE_STATUS_MUTATION,
            context_value=_gql_context(db),
            variable_values={"uid": task.id, "status": "active"},
        )
        assert result.errors is None
        assert result.data["updateTaskStatus"]["taskStatus"] == "active"

    def test_update_task_status_persists_to_db(self, db):
        task = _mk_task(db, task_status="active")

        schema.execute_sync(
            self.UPDATE_STATUS_MUTATION,
            context_value=_gql_context(db),
            variable_values={"uid": task.id, "status": "paused"},
        )

        db.expire_all()
        from sqlalchemy import select
        fetched = db.execute(select(Task).where(Task.id == task.id)).scalar_one()
        assert fetched.task_status == "paused"

    def test_update_task_status_not_found_raises_error(self, db):
        result = schema.execute_sync(
            self.UPDATE_STATUS_MUTATION,
            context_value=_gql_context(db),
            variable_values={"uid": "nonexistent-uid", "status": "resolved"},
        )
        assert result.errors is not None

    def test_update_task_status_on_non_task_conversation_fails(self, db):
        # A regular conversation (no linked task) should NOT be found by updateTaskStatus
        conv = Conversation(subject="Not a task")
        db.add(conv)
        db.flush()

        result = schema.execute_sync(
            self.UPDATE_STATUS_MUTATION,
            context_value=_gql_context(db),
            variable_values={"uid": conv.id, "status": "resolved"},
        )
        assert result.errors is not None


# ---------------------------------------------------------------------------
# updateTask mutation (mode + status)
# ---------------------------------------------------------------------------

class TestUpdateTaskMutation:

    UPDATE_TASK_MUTATION = """
    mutation UpdateTask($input: UpdateTaskInput!) {
        updateTask(input: $input) {
            uid taskMode taskStatus
        }
    }
    """

    def test_update_task_mode_only(self, db):
        task = _mk_task(db, task_mode="manual", task_status="active")

        result = schema.execute_sync(
            self.UPDATE_TASK_MUTATION,
            context_value=_gql_context(db),
            variable_values={"input": {"uid": task.id, "taskMode": "autonomous"}},
        )
        assert result.errors is None
        updated = result.data["updateTask"]
        assert updated["taskMode"] == "autonomous"
        assert updated["taskStatus"] == "active"  # unchanged

    def test_update_task_status_only(self, db):
        task = _mk_task(db, task_mode="manual", task_status="active")

        result = schema.execute_sync(
            self.UPDATE_TASK_MUTATION,
            context_value=_gql_context(db),
            variable_values={"input": {"uid": task.id, "taskStatus": "paused"}},
        )
        assert result.errors is None
        updated = result.data["updateTask"]
        assert updated["taskStatus"] == "paused"
        assert updated["taskMode"] == "manual"  # unchanged

    def test_update_task_mode_and_status_together(self, db):
        task = _mk_task(db, task_mode="manual", task_status="active")

        result = schema.execute_sync(
            self.UPDATE_TASK_MUTATION,
            context_value=_gql_context(db),
            variable_values={
                "input": {
                    "uid": task.id,
                    "taskMode": "waiting_approval",
                    "taskStatus": "paused",
                }
            },
        )
        assert result.errors is None
        updated = result.data["updateTask"]
        assert updated["taskMode"] == "waiting_approval"
        assert updated["taskStatus"] == "paused"

    def test_update_task_not_found_raises_error(self, db):
        result = schema.execute_sync(
            self.UPDATE_TASK_MUTATION,
            context_value=_gql_context(db),
            variable_values={"input": {"uid": "no-such-id", "taskMode": "autonomous"}},
        )
        assert result.errors is not None

    def test_update_task_persists_to_db(self, db):
        task = _mk_task(db, task_mode="manual")

        schema.execute_sync(
            self.UPDATE_TASK_MUTATION,
            context_value=_gql_context(db),
            variable_values={"input": {"uid": task.id, "taskMode": "autonomous", "taskStatus": "resolved"}},
        )

        db.expire_all()
        from sqlalchemy import select
        fetched = db.execute(select(Task).where(Task.id == task.id)).scalar_one()
        assert fetched.task_mode == "autonomous"
        assert fetched.task_status == "resolved"


# ---------------------------------------------------------------------------
# sendMessage mutation
# ---------------------------------------------------------------------------

class TestSendMessageMutation:

    SEND_MSG_MUTATION = """
    mutation SendMsg($input: SendMessageInput!) {
        sendMessage(input: $input) {
            uid body messageType senderName isAi isSystem sentAt
        }
    }
    """

    def test_send_message_basic(self, db):
        task = _mk_task(db)

        result = schema.execute_sync(
            self.SEND_MSG_MUTATION,
            context_value=_gql_context(db),
            variable_values={
                "input": {
                    "conversationId": task.ai_conversation_id,
                    "body": "Looking into this now.",
                    "senderName": "Manager",
                }
            },
        )
        assert result.errors is None
        msg = result.data["sendMessage"]
        assert msg["uid"] is not None
        assert msg["body"] == "Looking into this now."
        assert msg["senderName"] == "Manager"
        assert msg["isAi"] is False
        assert msg["isSystem"] is False
        assert msg["sentAt"] != ""

    def test_send_message_ai_flag(self, db):
        task = _mk_task(db)

        result = schema.execute_sync(
            self.SEND_MSG_MUTATION,
            context_value=_gql_context(db),
            variable_values={
                "input": {
                    "conversationId": task.ai_conversation_id,
                    "body": "I've scheduled a contractor.",
                    "senderName": "RentMate AI",
                    "isAi": True,
                    "messageType": "internal",
                }
            },
        )
        assert result.errors is None
        msg = result.data["sendMessage"]
        assert msg["isAi"] is True
        assert msg["messageType"] == "internal"
        assert msg["senderName"] == "RentMate AI"

    def test_send_message_updates_last_message_at(self, db):
        task = _mk_task(db)
        assert task.last_message_at is None

        schema.execute_sync(
            self.SEND_MSG_MUTATION,
            context_value=_gql_context(db),
            variable_values={
                "input": {
                    "conversationId": task.ai_conversation_id,
                    "body": "Updating the task.",
                }
            },
        )

        db.expire_all()
        from sqlalchemy import select
        fetched = db.execute(select(Task).where(Task.id == task.id)).scalar_one()
        assert fetched.last_message_at is not None

    def test_send_message_persists_to_db(self, db):
        task = _mk_task(db)

        result = schema.execute_sync(
            self.SEND_MSG_MUTATION,
            context_value=_gql_context(db),
            variable_values={
                "input": {
                    "conversationId": task.ai_conversation_id,
                    "body": "Persisted message",
                    "senderName": "Test User",
                }
            },
        )
        assert result.errors is None
        msg_uid = result.data["sendMessage"]["uid"]

        db.expire_all()
        from sqlalchemy import select
        msg = db.execute(select(Message).where(Message.id == msg_uid)).scalar_one_or_none()
        assert msg is not None
        assert msg.body == "Persisted message"
        # Message is stored on the task's AI conversation
        fetched_task = db.execute(select(Task).where(Task.id == task.id)).scalar_one()
        assert msg.conversation_id == fetched_task.ai_conversation_id

    def test_send_message_to_orphan_conversation(self, db):
        """Sending a message to a conversation not linked to any task should still succeed."""
        conv = Conversation(subject="Orphan chat")
        db.add(conv)
        db.flush()

        result = schema.execute_sync(
            self.SEND_MSG_MUTATION,
            context_value=_gql_context(db),
            variable_values={
                "input": {
                    "conversationId": conv.id,
                    "body": "This should still work (creates message row)",
                }
            },
        )
        assert result.errors is None

    def test_send_multiple_messages_shows_in_tasks_query(self, db):
        task = _mk_task(db, subject="Multi-message task")

        for body in ["First", "Second", "Third"]:
            schema.execute_sync(
                self.SEND_MSG_MUTATION,
                context_value=_gql_context(db),
                variable_values={"input": {"conversationId": task.ai_conversation_id, "body": body}},
            )

        # Expire the session identity map so the subsequent query fetches fresh
        # rows from the DB (each mutation calls sess.commit() which can leave
        # the session's relationship cache stale when tests share a connection).
        db.expire_all()

        result = schema.execute_sync(
            "{ tasks { uid messages { body } } }",
            context_value=_gql_context(db),
        )
        assert result.errors is None
        task_data = next(t for t in result.data["tasks"] if t["uid"] == task.id)
        msgs = task_data["messages"]
        assert len(msgs) == 3
        assert [m["body"] for m in msgs] == ["First", "Second", "Third"]


# ---------------------------------------------------------------------------
# addDocumentTag mutation
# ---------------------------------------------------------------------------

class TestAddDocumentTagMutation:

    ADD_TAG_MUTATION = """
    mutation AddTag($input: AddDocumentTagInput!) {
        addDocumentTag(input: $input) {
            uid documentId tagType propertyId unitId tenantId createdAt
        }
    }
    """

    def test_add_document_tag_property(self, db):
        doc = _mk_document(db)
        prop = _mk_property(db)

        result = schema.execute_sync(
            self.ADD_TAG_MUTATION,
            context_value=_gql_context(db),
            variable_values={
                "input": {
                    "documentId": doc.id,
                    "tagType": "property",
                    "propertyId": prop.id,
                }
            },
        )
        assert result.errors is None
        tag = result.data["addDocumentTag"]
        assert tag["uid"] is not None
        assert tag["documentId"] == doc.id
        assert tag["tagType"] == "property"
        assert tag["propertyId"] == prop.id
        assert tag["unitId"] is None
        assert tag["tenantId"] is None
        assert tag["createdAt"] != ""

    def test_add_document_tag_unit(self, db):
        doc = _mk_document(db)
        prop = _mk_property(db)
        unit = _mk_unit(db, prop, "501")

        result = schema.execute_sync(
            self.ADD_TAG_MUTATION,
            context_value=_gql_context(db),
            variable_values={
                "input": {
                    "documentId": doc.id,
                    "tagType": "unit",
                    "unitId": unit.id,
                }
            },
        )
        assert result.errors is None
        tag = result.data["addDocumentTag"]
        assert tag["tagType"] == "unit"
        assert tag["unitId"] == unit.id

    def test_add_document_tag_tenant(self, db):
        doc = _mk_document(db)
        tenant = _mk_tenant(db)

        result = schema.execute_sync(
            self.ADD_TAG_MUTATION,
            context_value=_gql_context(db),
            variable_values={
                "input": {
                    "documentId": doc.id,
                    "tagType": "tenant",
                    "tenantId": tenant.id,
                }
            },
        )
        assert result.errors is None
        tag = result.data["addDocumentTag"]
        assert tag["tagType"] == "tenant"
        assert tag["tenantId"] == tenant.id

    def test_add_document_tag_persists_to_db(self, db):
        doc = _mk_document(db)
        prop = _mk_property(db)

        result = schema.execute_sync(
            self.ADD_TAG_MUTATION,
            context_value=_gql_context(db),
            variable_values={
                "input": {
                    "documentId": doc.id,
                    "tagType": "property",
                    "propertyId": prop.id,
                }
            },
        )
        assert result.errors is None
        tag_uid = result.data["addDocumentTag"]["uid"]

        db.expire_all()
        from sqlalchemy import select
        tag = db.execute(select(DocumentTag).where(DocumentTag.id == tag_uid)).scalar_one_or_none()
        assert tag is not None
        assert tag.document_id == doc.id
        assert tag.tag_type == "property"
        assert tag.property_id == prop.id

    def test_add_document_tag_unauthenticated_fails(self, db):
        doc = _mk_document(db)
        result = schema.execute_sync(
            self.ADD_TAG_MUTATION,
            context_value={"db_session": db, "user": None},
            variable_values={
                "input": {"documentId": doc.id, "tagType": "property"}
            },
        )
        assert result.errors is not None


# ---------------------------------------------------------------------------
# confirmDocument mutation
# ---------------------------------------------------------------------------

class TestConfirmDocumentMutation:

    CONFIRM_DOC_MUTATION = """
    mutation ConfirmDoc($documentId: String!) {
        confirmDocument(documentId: $documentId)
    }
    """

    def test_confirm_document_returns_true(self, db):
        doc = _mk_document(db)

        result = schema.execute_sync(
            self.CONFIRM_DOC_MUTATION,
            context_value=_gql_context(db),
            variable_values={"documentId": doc.id},
        )
        assert result.errors is None
        assert result.data["confirmDocument"] is True

    def test_confirm_document_sets_confirmed_at(self, db):
        doc = _mk_document(db)
        assert doc.confirmed_at is None

        schema.execute_sync(
            self.CONFIRM_DOC_MUTATION,
            context_value=_gql_context(db),
            variable_values={"documentId": doc.id},
        )

        db.expire_all()
        from sqlalchemy import select
        reloaded = db.execute(select(Document).where(Document.id == doc.id)).scalar_one()
        assert reloaded.confirmed_at is not None

    def test_confirm_document_idempotent(self, db):
        doc = _mk_document(db)

        for _ in range(2):
            result = schema.execute_sync(
                self.CONFIRM_DOC_MUTATION,
                context_value=_gql_context(db),
                variable_values={"documentId": doc.id},
            )
            assert result.errors is None
            assert result.data["confirmDocument"] is True

    def test_confirm_document_not_found_raises_error(self, db):
        result = schema.execute_sync(
            self.CONFIRM_DOC_MUTATION,
            context_value=_gql_context(db),
            variable_values={"documentId": "nonexistent-doc-id"},
        )
        assert result.errors is not None

    def test_confirm_document_unauthenticated_fails(self, db):
        doc = _mk_document(db)
        result = schema.execute_sync(
            self.CONFIRM_DOC_MUTATION,
            context_value={"db_session": db, "user": None},
            variable_values={"documentId": doc.id},
        )
        assert result.errors is not None


# ---------------------------------------------------------------------------
# DocumentTask model
# ---------------------------------------------------------------------------

class TestDocumentTaskModel:

    def test_create_document_task(self, db):
        doc = _mk_document(db)
        task = _mk_task(db, subject="Doc task")

        dt = DocumentTask(document_id=doc.id, task_id=task.id)
        db.add(dt)
        db.flush()

        assert dt.id is not None
        assert dt.document_id == doc.id
        assert dt.task_id == task.id
        assert dt.created_at is not None

    def test_document_task_unique_constraint(self, db):
        doc = _mk_document(db)
        task = _mk_task(db, subject="Unique constraint task")

        dt1 = DocumentTask(document_id=doc.id, task_id=task.id)
        db.add(dt1)
        db.flush()

        dt2 = DocumentTask(document_id=doc.id, task_id=task.id)
        db.add(dt2)
        with pytest.raises(IntegrityError):
            db.flush()

    def test_document_task_multiple_tasks_per_document(self, db):
        doc = _mk_document(db)
        task1 = _mk_task(db, subject="Task 1")
        task2 = _mk_task(db, subject="Task 2")

        db.add(DocumentTask(document_id=doc.id, task_id=task1.id))
        db.add(DocumentTask(document_id=doc.id, task_id=task2.id))
        db.flush()

        from sqlalchemy import select
        results = db.execute(
            select(DocumentTask).where(DocumentTask.document_id == doc.id)
        ).scalars().all()
        assert len(results) == 2

    def test_document_task_multiple_documents_per_task(self, db):
        doc1 = _mk_document(db, filename="doc1.pdf")
        doc2 = _mk_document(db, filename="doc2.pdf")
        task = _mk_task(db, subject="Multi-doc task")

        db.add(DocumentTask(document_id=doc1.id, task_id=task.id))
        db.add(DocumentTask(document_id=doc2.id, task_id=task.id))
        db.flush()

        from sqlalchemy import select
        results = db.execute(
            select(DocumentTask).where(DocumentTask.task_id == task.id)
        ).scalars().all()
        assert len(results) == 2


# ---------------------------------------------------------------------------
# DocumentTag model
# ---------------------------------------------------------------------------

class TestDocumentTagModel:

    def test_create_document_tag_for_property(self, db):
        doc = _mk_document(db)
        prop = _mk_property(db)

        tag = DocumentTag(
            document_id=doc.id,
            tag_type="property",
            property_id=prop.id,
        )
        db.add(tag)
        db.flush()

        assert tag.id is not None
        assert tag.document_id == doc.id
        assert tag.tag_type == "property"
        assert tag.property_id == prop.id
        assert tag.unit_id is None
        assert tag.tenant_id is None

    def test_create_document_tag_for_unit(self, db):
        doc = _mk_document(db)
        prop = _mk_property(db)
        unit = _mk_unit(db, prop, "12B")

        tag = DocumentTag(
            document_id=doc.id,
            tag_type="unit",
            unit_id=unit.id,
        )
        db.add(tag)
        db.flush()

        assert tag.id is not None
        assert tag.tag_type == "unit"
        assert tag.unit_id == unit.id

    def test_create_document_tag_for_tenant(self, db):
        doc = _mk_document(db)
        tenant = _mk_tenant(db, first="Henry", last="Tag")

        tag = DocumentTag(
            document_id=doc.id,
            tag_type="tenant",
            tenant_id=tenant.id,
        )
        db.add(tag)
        db.flush()

        assert tag.id is not None
        assert tag.tag_type == "tenant"
        assert tag.tenant_id == tenant.id

    def test_document_tag_multiple_tags_per_document(self, db):
        doc = _mk_document(db)
        prop = _mk_property(db)
        tenant = _mk_tenant(db)

        db.add(DocumentTag(document_id=doc.id, tag_type="property", property_id=prop.id))
        db.add(DocumentTag(document_id=doc.id, tag_type="tenant", tenant_id=tenant.id))
        db.flush()

        from sqlalchemy import select
        tags = db.execute(
            select(DocumentTag).where(DocumentTag.document_id == doc.id)
        ).scalars().all()
        assert len(tags) == 2


# ---------------------------------------------------------------------------
# _ensure_schema() — startup schema management (see test_startup.py for full coverage)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Task-number monotonic sequence (never reuse after deletion)
# ---------------------------------------------------------------------------

class TestTaskNumberNeverReused:
    """Task numbers must monotonically increase even when tasks are deleted."""

    def test_task_number_not_reused_after_deletion(self, db):
        from gql.services.task_service import TaskService
        from gql.types import CreateTaskInput

        inp = CreateTaskInput(title="Task A", source="manual")
        task_a = TaskService.create_task(db, inp)
        assert task_a.task_number == 1

        inp2 = CreateTaskInput(title="Task B", source="manual")
        task_b = TaskService.create_task(db, inp2)
        assert task_b.task_number == 2

        # Delete task B
        TaskService.delete_task(db, task_b.id)

        # New task must get 3, not reuse 2
        inp3 = CreateTaskInput(title="Task C", source="manual")
        task_c = TaskService.create_task(db, inp3)
        assert task_c.task_number == 3

    def test_task_number_survives_all_tasks_deleted(self, db):
        from gql.services.task_service import TaskService
        from gql.types import CreateTaskInput

        t1 = TaskService.create_task(db, CreateTaskInput(title="T1", source="manual"))
        assert t1.task_number == 1

        TaskService.delete_task(db, t1.id)

        # All tasks gone — next task must still be 2, not 1
        t2 = TaskService.create_task(db, CreateTaskInput(title="T2", source="manual"))
        assert t2.task_number == 2
