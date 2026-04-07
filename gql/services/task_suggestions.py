"""Generation and execution helpers for task-related suggestions.

Coordinates multiple service calls (task creation, conversation wiring,
message sending) for the suggestion approval workflow.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from db.enums import AgentSource, SuggestionSource
from db.models import (
    Conversation,
    ConversationType,
    Message,
    MessageType,
    Suggestion,
    Task,
)
from gql.services import chat_service, settings_service, suggestion_service
from gql.services.task_service import TaskService
from gql.types import CreateTaskInput


class SuggestionExecutor:
    """Base class for generating and executing task-related suggestions.

    Subclasses implement ``generate()`` and ``execute()``.  Shared orchestration
    steps are available as protected helpers on the base class.
    """

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def for_suggestion(db: Session, suggestion_id: str) -> "SuggestionExecutor":
        """Return the right executor subclass for an existing suggestion.

        Used by handlers that need to execute an action on a suggestion
        without knowing which type it is.
        """
        suggestion = db.execute(
            select(Suggestion).where(Suggestion.id == suggestion_id)
        ).scalar_one_or_none()
        if not suggestion:
            raise ValueError(f"Suggestion {suggestion_id} not found")
        payload = suggestion.action_payload or {}
        action_type = payload.get("action")
        if action_type == "close_task":
            cls = CloseTaskSuggestionExecutor
        elif action_type == "set_mode":
            cls = SetModeSuggestionExecutor
        elif action_type == "attach_vendor":
            cls = AttachVendorSuggestionExecutor
        elif action_type == "attach_entity":
            cls = AttachEntitySuggestionExecutor
        elif action_type == "message_person":
            cls = MessagePersonSuggestionExecutor
        elif action_type == "update_steps":
            cls = UpdateStepsSuggestionExecutor
        elif suggestion.task_id:
            cls = ReplyInTaskSuggestionExecutor
        else:
            cls = CreateTaskSuggestionExecutor
        executor = cls.__new__(cls)
        executor.db = db
        return executor

    def generate(self) -> Suggestion | None:
        """Create a suggestion (with LLM-generated content if applicable).

        Returns the created Suggestion, or None if generation fails.
        """
        raise NotImplementedError

    def execute(
        self,
        suggestion_id: str,
        action: str,
        edited_body: str | None = None,
    ) -> tuple[Suggestion, Task | None]:
        """Act on a suggestion and return the updated Suggestion and optional Task."""
        raise NotImplementedError

    # ─── shared helpers for subclasses ────────────────────────────────────

    def _fetch_suggestion(self, suggestion_id: str) -> Suggestion:
        suggestion = self.db.execute(
            select(Suggestion).where(Suggestion.id == suggestion_id)
        ).scalar_one_or_none()
        if not suggestion:
            raise ValueError(f"Suggestion {suggestion_id} not found")
        return suggestion

    def _create_task_from_suggestion(self, suggestion: Suggestion) -> Task:
        """Create a new Task and transfer the AI conversation from the suggestion."""
        task = TaskService.create_task(self.db, CreateTaskInput(
            title=suggestion.title or "",
            source=suggestion.source or "automation",
            task_status="active",
            task_mode="manual",
            category=suggestion.category,
            urgency=suggestion.urgency,
            priority="routine",
            property_id=suggestion.property_id,
            unit_id=suggestion.unit_id,
        ))

        # Capture before clearing — needed for approval message update
        ai_convo_id = suggestion.ai_conversation_id

        # Reassign the AI conversation from suggestion to task
        if ai_convo_id:
            ai_convo = self.db.get(Conversation, ai_convo_id)
            if ai_convo:
                ai_convo.conversation_type = ConversationType.TASK_AI
            task.ai_conversation_id = ai_convo_id
            suggestion.ai_conversation_id = None

        # Mark suggestion/approval messages as approved
        if ai_convo_id:
            approval_msgs = self.db.execute(
                select(Message).where(
                    Message.conversation_id == ai_convo_id,
                    Message.message_type.in_([MessageType.APPROVAL, MessageType.SUGGESTION]),
                )
            ).scalars().all()
            for m in approval_msgs:
                m.approval_status = "approved"

        return task

    def _wire_vendor_conversation(self, task: Task, suggestion: Suggestion, vendor_id: str) -> None:
        """Create or find the vendor conversation and link it to the task."""
        ext_convo = chat_service.get_or_create_external_conversation(
            self.db,
            conversation_type=ConversationType.VENDOR,
            subject=suggestion.title or "",
            property_id=suggestion.property_id,
            unit_id=suggestion.unit_id,
            vendor_id=vendor_id,
        )
        task.external_conversation_id = ext_convo.id
        TaskService.assign_vendor_to_task(self.db, task_id=task.id, vendor_id=vendor_id)

    def _send_draft_message(self, task: Task, draft: str) -> None:
        """Send a draft message to the task's external conversation."""
        if task.external_conversation_id:
            chat_service.send_autonomous_message(
                self.db, task.external_conversation_id, draft, task_id=task.id,
            )

    def _resolve_suggestion(
        self, suggestion_id: str, action: str, task: Task | None,
    ) -> Suggestion:
        """Mark the suggestion as accepted/dismissed via the service layer."""
        result = suggestion_service.act_on_suggestion(
            self.db, suggestion_id, action,
            task_id=task.id if task else None,
        )
        from llm.tracing import log_trace
        log_trace(
            "suggestion_executed", "executor",
            f"Suggestion {action}: {result.title or suggestion_id}",
            task_id=task.id if task else None,
            suggestion_id=suggestion_id,
            detail={"action": action, "status": result.status},
        )
        return result


class CreateTaskSuggestionExecutor(SuggestionExecutor):
    """Generate and execute suggestions triggered by automation rules.

    Optionally generates an LLM-drafted vendor outreach message when a
    vendor is assigned and autonomy is "suggest".
    """

    def __init__(
        self,
        db: Session,
        *,
        title: str,
        ai_context: str,
        category: str | None,
        urgency: str | None,
        source: SuggestionSource,
        autonomy: str,
        property_id: str | None = None,
        unit_id: str | None = None,
        vendor_id: str | None = None,
        vendor_name: str | None = None,
    ):
        super().__init__(db)
        self.title = title
        self.ai_context = ai_context
        self.category = category
        self.urgency = urgency
        self.source = source
        self.autonomy = autonomy
        self.property_id = property_id
        self.unit_id = unit_id
        self.vendor_id = vendor_id
        self.vendor_name = vendor_name

    def generate(self) -> Suggestion:
        from llm.vendor_outreach import generate_vendor_outreach

        action_payload: dict = {}
        if self.vendor_id:
            action_payload["vendor_id"] = self.vendor_id
            action_payload["vendor_name"] = self.vendor_name

        # Generate vendor draft if in suggest mode
        has_vendor_draft = False
        if self.vendor_id and self.autonomy == "suggest":
            try:
                draft = generate_vendor_outreach(
                    task_title=self.title,
                    task_body=self.ai_context,
                    category=self.category,
                    vendor_name=self.vendor_name,
                )
                if draft:
                    action_payload["draft_message"] = draft
                    has_vendor_draft = True
            except Exception:
                pass

        options = settings_service.build_suggestion_options(
            self.autonomy, has_vendor_draft=has_vendor_draft,
        )

        suggestion = suggestion_service.create_suggestion(
            self.db,
            title=self.title,
            ai_context=self.ai_context,
            category=self.category,
            urgency=self.urgency,
            source=self.source,
            options=options,
            action_payload=action_payload or None,
            property_id=self.property_id,
            unit_id=self.unit_id,
        )

        if has_vendor_draft:
            chat_service.send_message(
                self.db,
                conversation_id=suggestion.ai_conversation_id,
                body="Here's a suggested message you can send to the vendor:",
                message_type=MessageType.SUGGESTION,
                sender_name="RentMate",
                is_ai=True,
                draft_reply=action_payload["draft_message"],
                related_task_ids={"suggestion_id": suggestion.id},
            )

        return suggestion

    def execute(
        self,
        suggestion_id: str,
        action: str,
        edited_body: str | None = None,
    ) -> tuple[Suggestion, Task | None]:
        """Accept: create a task from the suggestion and wire conversations.
        Reject: dismiss the suggestion.
        """
        suggestion = self._fetch_suggestion(suggestion_id)
        task = None

        if action in ("accept_task", "approve_draft"):
            payload = suggestion.action_payload or {}

            task = self._create_task_from_suggestion(suggestion)

            # Apply progress steps if the agent included them
            steps = payload.get("steps")
            if steps:
                task.steps = steps
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(task, "steps")

            vendor_id = payload.get("vendor_id")
            if vendor_id:
                self._wire_vendor_conversation(task, suggestion, vendor_id)

            suggestion.task_id = task.id

            draft = edited_body or payload.get("draft_message")
            if action == "approve_draft" and draft:
                self._send_draft_message(task, draft)

        suggestion = self._resolve_suggestion(suggestion_id, action, task)
        return suggestion, task


class ReplyInTaskSuggestionExecutor(SuggestionExecutor):
    """Generate and execute reply suggestions for vendor/external messages.

    Calls the LLM to draft a response to a vendor message on an existing
    task, then creates a suggestion with approval options.
    """

    def __init__(
        self,
        db: Session,
        *,
        task: Task,
        last_msg: Message,
        vendor_name: str,
        autonomy: str,
    ):
        super().__init__(db)
        self.task = task
        self.last_msg = last_msg
        self.vendor_name = vendor_name
        self.autonomy = autonomy

    def generate(self) -> Suggestion | None:
        from llm.suggest import generate_task_suggestion

        draft = generate_task_suggestion(
            subject=self.task.title,
            context_body=f'{self.vendor_name} replied: "{self.last_msg.body}"',
            category=self.task.category or "maintenance",
        )
        if not draft:
            return None

        options = settings_service.build_suggestion_options(
            self.autonomy, has_vendor_draft=True,
        )
        action_payload: dict = {"draft_message": draft}
        if self.task.ai_conversation_id:
            ai_convo = self.db.get(Conversation, self.task.ai_conversation_id)
            if ai_convo:
                vid = (ai_convo.extra or {}).get("assigned_vendor_id")
                if vid:
                    action_payload["vendor_id"] = vid
                    action_payload["vendor_name"] = self.vendor_name

        suggestion = suggestion_service.create_suggestion(
            self.db,
            title=f"Reply to {self.vendor_name}: {self.task.title}",
            ai_context=(
                f'{self.vendor_name} replied: "{self.last_msg.body}"'
                f"\n\nSuggested response:\n{draft}"
            ),
            category=self.task.category,
            urgency=self.task.urgency,
            source=AgentSource(),
            options=options,
            action_payload=action_payload,
            property_id=self.task.property_id,
            unit_id=self.task.unit_id,
        )
        suggestion.task_id = self.task.id

        chat_service.send_message(
            self.db, conversation_id=suggestion.ai_conversation_id,
            body=f"{self.vendor_name} replied. Here's a suggested response:",
            message_type=MessageType.SUGGESTION,
            sender_name="RentMate",
            is_ai=True,
            draft_reply=draft,
            related_task_ids={"suggestion_id": suggestion.id},
        )

        return suggestion

    def execute(
        self,
        suggestion_id: str,
        action: str,
        edited_body: str | None = None,
    ) -> tuple[Suggestion, Task | None]:
        """Accept: use the existing linked task and optionally send the draft.
        Reject: dismiss the suggestion.
        """
        suggestion = self._fetch_suggestion(suggestion_id)
        task = None

        if action in ("accept_task", "approve_draft"):
            payload = suggestion.action_payload or {}

            # This suggestion is linked to an existing task
            if suggestion.task_id:
                task = self.db.execute(
                    select(Task).where(Task.id == suggestion.task_id)
                ).scalar_one_or_none()

            if task:
                draft = edited_body or payload.get("draft_message")
                if action == "approve_draft" and draft:
                    self._send_draft_message(task, draft)

        suggestion = self._resolve_suggestion(suggestion_id, action, task)
        return suggestion, task


class CloseTaskSuggestionExecutor(SuggestionExecutor):
    """Execute a suggestion to close an existing task."""

    def execute(
        self,
        suggestion_id: str,
        action: str,
        edited_body: str | None = None,
    ) -> tuple[Suggestion, Task | None]:
        suggestion = self._fetch_suggestion(suggestion_id)
        task = None

        if action == "close_task" and suggestion.task_id:
            task = self.db.execute(
                select(Task).where(Task.id == suggestion.task_id)
            ).scalar_one_or_none()
            if task:
                task.task_status = "dismissed"

        suggestion = self._resolve_suggestion(suggestion_id, action, task)
        return suggestion, task


class SetModeSuggestionExecutor(SuggestionExecutor):
    """Execute a suggestion to change a task's operating mode."""

    def execute(
        self,
        suggestion_id: str,
        action: str,
        edited_body: str | None = None,
    ) -> tuple[Suggestion, Task | None]:
        suggestion = self._fetch_suggestion(suggestion_id)
        task = None

        if action == "set_mode" and suggestion.task_id:
            payload = suggestion.action_payload or {}
            new_mode = payload.get("mode")
            task = self.db.execute(
                select(Task).where(Task.id == suggestion.task_id)
            ).scalar_one_or_none()
            if task and new_mode:
                task.task_mode = new_mode

        suggestion = self._resolve_suggestion(suggestion_id, action, task)
        return suggestion, task


class AttachVendorSuggestionExecutor(SuggestionExecutor):
    """Execute a suggestion to attach a vendor conversation to a task.

    Handles two accept actions:
    - ``attach_vendor`` — wire the vendor conversation without sending a message
    - ``attach_vendor_send`` — wire the conversation and send the draft message
    """

    def execute(
        self,
        suggestion_id: str,
        action: str,
        edited_body: str | None = None,
    ) -> tuple[Suggestion, Task | None]:
        suggestion = self._fetch_suggestion(suggestion_id)
        task = None

        if action in ("attach_vendor", "attach_vendor_send") and suggestion.task_id:
            payload = suggestion.action_payload or {}
            vendor_id = payload.get("vendor_id")

            task = self.db.execute(
                select(Task).where(Task.id == suggestion.task_id)
            ).scalar_one_or_none()

            if task and vendor_id:
                self._wire_vendor_conversation(task, suggestion, vendor_id)

                if action == "attach_vendor_send":
                    draft = edited_body or payload.get("draft_message")
                    if draft:
                        self._send_draft_message(task, draft)

        suggestion = self._resolve_suggestion(suggestion_id, action, task)
        return suggestion, task


class UpdateStepsSuggestionExecutor(SuggestionExecutor):
    """Execute a suggestion to update a task's progress steps."""

    def execute(
        self,
        suggestion_id: str,
        action: str,
        edited_body: str | None = None,
    ) -> tuple[Suggestion, Task | None]:
        suggestion = self._fetch_suggestion(suggestion_id)
        task = None

        if action == "update_steps" and suggestion.task_id:
            payload = suggestion.action_payload or {}
            steps = payload.get("steps")

            task = self.db.execute(
                select(Task).where(Task.id == suggestion.task_id)
            ).scalar_one_or_none()

            if task and steps is not None:
                task.steps = steps
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(task, "steps")

        suggestion = self._resolve_suggestion(suggestion_id, action, task)
        return suggestion, task


class AttachEntitySuggestionExecutor(SuggestionExecutor):
    """Execute a suggestion to attach an entity (vendor, tenant, property, unit) to a task."""

    def execute(
        self,
        suggestion_id: str,
        action: str,
        edited_body: str | None = None,
    ) -> tuple[Suggestion, Task | None]:
        suggestion = self._fetch_suggestion(suggestion_id)
        task = None

        if action == "attach_entity" and suggestion.task_id:
            payload = suggestion.action_payload or {}
            entity_id = payload.get("entity_id")
            entity_type = payload.get("entity_type")

            task = self.db.execute(
                select(Task).where(Task.id == suggestion.task_id)
            ).scalar_one_or_none()

            if task and entity_id:
                if entity_type == "vendor":
                    self._wire_vendor_conversation(task, suggestion, entity_id)
                elif entity_type == "tenant":
                    self._wire_tenant_conversation(task, suggestion, entity_id)
                elif entity_type == "property":
                    task.property_id = entity_id
                elif entity_type == "unit":
                    from db.models import Unit
                    unit = self.db.query(Unit).filter_by(id=entity_id).first()
                    task.unit_id = entity_id
                    if unit and unit.property_id:
                        task.property_id = unit.property_id

        suggestion = self._resolve_suggestion(suggestion_id, action, task)
        return suggestion, task

    def _wire_tenant_conversation(self, task: Task, suggestion: Suggestion, tenant_id: str) -> None:
        """Create or find the tenant conversation and link it to the task."""
        ext_convo = chat_service.get_or_create_external_conversation(
            self.db,
            conversation_type=ConversationType.TENANT,
            subject=suggestion.title or "",
            property_id=suggestion.property_id,
            unit_id=suggestion.unit_id,
            tenant_id=tenant_id,
        )
        if not task.parent_conversation_id:
            task.parent_conversation_id = ext_convo.id
        elif not task.external_conversation_id:
            task.external_conversation_id = ext_convo.id
        # Ensure tenant has a portal token for the portal link
        from db.models import Tenant
        from gql.services.tenant_service import TenantService
        tenant = self.db.get(Tenant, tenant_id)
        if tenant:
            TenantService.ensure_portal_token(self.db, tenant)


class MessagePersonSuggestionExecutor(SuggestionExecutor):
    """Execute a suggestion to send a message to an external person (tenant or vendor)."""

    def execute(
        self,
        suggestion_id: str,
        action: str,
        edited_body: str | None = None,
    ) -> tuple[Suggestion, Task | None]:
        suggestion = self._fetch_suggestion(suggestion_id)
        task = None

        if action == "message_person_send" and suggestion.task_id:
            payload = suggestion.action_payload or {}
            entity_id = payload.get("entity_id")
            entity_type = payload.get("entity_type")
            entity_phone = payload.get("entity_phone")
            draft = edited_body or payload.get("draft_message")

            task = self.db.execute(
                select(Task).where(Task.id == suggestion.task_id)
            ).scalar_one_or_none()

            if task and entity_id and draft:
                # Wire conversation if not already linked
                if entity_type == "vendor":
                    if not task.external_conversation_id:
                        self._wire_vendor_conversation(task, suggestion, entity_id)
                elif entity_type == "tenant":
                    has_tenant_conv = False
                    if task.parent_conversation_id:
                        from db.models import Conversation as Conv
                        pc = self.db.get(Conv, task.parent_conversation_id)
                        if pc and getattr(pc, "conversation_type", None) == ConversationType.TENANT:
                            has_tenant_conv = True
                    if not has_tenant_conv:
                        ext_convo = chat_service.get_or_create_external_conversation(
                            self.db,
                            conversation_type=ConversationType.TENANT,
                            subject=suggestion.title or "",
                            property_id=suggestion.property_id,
                            unit_id=suggestion.unit_id,
                            tenant_id=entity_id,
                        )
                        if not task.parent_conversation_id:
                            task.parent_conversation_id = ext_convo.id
                        elif not task.external_conversation_id:
                            task.external_conversation_id = ext_convo.id
                    # Ensure tenant has a portal token
                    from db.models import Tenant as TenantModel
                    from gql.services.tenant_service import TenantService
                    t_obj = self.db.get(TenantModel, entity_id)
                    if t_obj:
                        TenantService.ensure_portal_token(self.db, t_obj)

                # Find the conversation to send to
                conv_id = self._resolve_conversation_for_entity(task, entity_type)
                if conv_id:
                    chat_service.send_autonomous_message(
                        self.db, conv_id, draft, task_id=task.id,
                    )
                    if entity_phone:
                        # For tenants, include a portal link in the SMS
                        sms_body = draft
                        if entity_type == "tenant":
                            sms_body = self._append_tenant_portal_link(entity_id, draft)
                        self._dispatch_sms(entity_phone, sms_body)

        suggestion = self._resolve_suggestion(suggestion_id, action, task)
        return suggestion, task

    def _resolve_conversation_for_entity(self, task: Task, entity_type: str) -> str | None:
        """Find the conversation ID for the given entity type on a task."""
        if entity_type == "vendor":
            return task.external_conversation_id
        elif entity_type == "tenant":
            if task.parent_conversation_id:
                from db.models import Conversation as Conv
                pc = self.db.get(Conv, task.parent_conversation_id)
                if pc and getattr(pc, "conversation_type", None) == ConversationType.TENANT:
                    return task.parent_conversation_id
            return task.external_conversation_id
        return None

    def _append_tenant_portal_link(self, tenant_id: str, draft: str) -> str:
        """Ensure tenant has a portal token and append the link to the SMS body."""
        try:
            from db.models import Tenant
            from gql.services.tenant_service import TenantService
            tenant = self.db.get(Tenant, tenant_id)
            if tenant:
                TenantService.ensure_portal_token(self.db, tenant)
                self.db.flush()
                portal_url = TenantService.get_portal_url(tenant)
                if portal_url:
                    return f"{draft}\n\nReply here: {portal_url}"
        except Exception:
            pass
        return draft

    def _dispatch_sms(self, to_phone: str, body: str) -> None:
        """Dispatch an SMS via Quo (best-effort, non-blocking)."""
        try:
            from handlers.chat import _get_quo_api_key, _get_quo_from_number, send_sms_reply
            api_key = _get_quo_api_key()
            from_num = _get_quo_from_number()
            if api_key:
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                    loop.create_task(send_sms_reply(from_num, to_phone, body, api_key))
                except RuntimeError:
                    pass
        except Exception:
            pass
