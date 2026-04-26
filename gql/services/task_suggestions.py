"""Generation and execution helpers for task-related suggestions.

Coordinates multiple service calls (task creation, conversation wiring,
message sending) for the suggestion approval workflow.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from backends.local_auth import resolve_account_id, resolve_org_id
from db.enums import AgentSource, SuggestionSource, TaskMode, TaskPriority, TaskSource, TaskStatus
from db.models import (
    Conversation,
    ConversationParticipant,
    ConversationType,
    Message,
    MessageType,
    ParticipantType,
    Suggestion,
    Task,
)
from gql.services import chat_service, settings_service, suggestion_service
from gql.services.task_service import TaskProgressStep, TaskService, dump_task_steps
from gql.services.vendor_service import get_vendor_by_external_id, get_vendor_by_id
from gql.types import CreateTaskInput
from llm.tools._common import _placeholder_message_block_error


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
            select(Suggestion).where(
                Suggestion.id == suggestion_id,
                Suggestion.org_id == resolve_org_id(),
                Suggestion.creator_id == resolve_account_id(),
            )
        ).scalar_one_or_none()
        if not suggestion:
            raise ValueError(f"Suggestion {suggestion_id} not found")
        payload = suggestion.action_payload or {}
        action_type = payload.get("action")
        if action_type == "close_task":
            cls = CloseTaskSuggestionExecutor
        elif action_type == "message_person":
            cls = MessagePersonSuggestionExecutor
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
            select(Suggestion).where(
                Suggestion.id == suggestion_id,
                Suggestion.org_id == resolve_org_id(),
                Suggestion.creator_id == resolve_account_id(),
            )
        ).scalar_one_or_none()
        if not suggestion:
            raise ValueError(f"Suggestion {suggestion_id} not found")
        return suggestion

    def _create_task_from_suggestion(self, suggestion: Suggestion) -> Task:
        """Create a new Task and transfer the AI conversation from the suggestion."""
        payload = suggestion.action_payload or {}
        task = TaskService.create_task(self.db, CreateTaskInput(
            title=suggestion.title or "",
            source=TaskSource(suggestion.source or "automation"),
            task_status=TaskStatus.ACTIVE,
            task_mode=TaskMode.MANUAL,
            category=suggestion.category,
            urgency=suggestion.urgency,
            priority=TaskPriority.ROUTINE,
            property_id=suggestion.property_id,
            unit_id=suggestion.unit_id,
            goal=(payload.get("goal") or suggestion.title or "").strip(),
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

    def _task_conversation_with_entity(
        self,
        task: Task,
        *,
        conversation_type: ConversationType,
        participant_type: ParticipantType,
        entity_user_id: int,
    ) -> Conversation | None:
        """Find the task's existing coordination thread with a specific entity.

        Returns the Conversation if this task already has one matching
        (conversation_type, participant with the given user_id + participant_type),
        otherwise None. Used to decide whether to reuse or create a new thread
        when the agent reaches out to the same tenant/vendor more than once.
        """
        for convo in task.external_conversations:
            if convo.conversation_type != conversation_type:
                continue
            participant = self.db.execute(
                select(ConversationParticipant).where(
                    ConversationParticipant.conversation_id == convo.id,
                    ConversationParticipant.user_id == entity_user_id,
                    ConversationParticipant.participant_type == participant_type,
                )
            ).scalar_one_or_none()
            if participant:
                return convo
        return None

    def _wire_vendor_conversation(self, task: Task, suggestion: Suggestion, vendor_id: str) -> Conversation:
        """Create or reuse the vendor's task-scoped conversation.

        Reuses an existing conversation on this task *with this specific vendor*;
        otherwise creates a new one. Multiple vendors on the same task each get
        their own thread.
        """
        vendor = get_vendor_by_external_id(self.db, str(vendor_id))
        if not vendor:
            raise ValueError(f"Vendor {vendor_id} not found")
        existing = self._task_conversation_with_entity(
            task,
            conversation_type=ConversationType.VENDOR,
            participant_type=ParticipantType.EXTERNAL_CONTACT,
            entity_user_id=vendor.id,
        )
        if existing is None:
            existing = chat_service.get_or_create_external_conversation(
                self.db,
                conversation_type=ConversationType.VENDOR,
                subject=suggestion.title or "",
                property_id=suggestion.property_id,
                unit_id=suggestion.unit_id,
                vendor_id=vendor.id,
                parent_task_id=task.id,
            )
        TaskService.assign_vendor_to_task(self.db, task_id=task.id, vendor_id=vendor.id)
        return existing

    def _send_draft_message(self, task: Task, draft: str) -> None:
        """Send a draft message to the task's most recent external conversation."""
        placeholder_error = _placeholder_message_block_error(draft)
        if placeholder_error:
            raise ValueError(placeholder_error)
        ext_convo = task.latest_external_conversation
        if ext_convo:
            chat_service.send_autonomous_message(
                self.db, conversation_id=ext_convo.id, body=draft, task_id=task.id,
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

        if action in ("send_and_create_task", "edit_message"):
            payload = suggestion.action_payload or {}

            task = self._create_task_from_suggestion(suggestion)

            # Apply progress steps if the agent included them
            steps = [TaskProgressStep.model_validate(s) for s in (payload.get("steps") or [])]
            if steps:
                from datetime import UTC, datetime

                task.steps = dump_task_steps(steps)
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(task, "steps")
                task.updated_at = datetime.now(UTC)

            vendor_id = payload.get("vendor_id")
            if vendor_id:
                self._wire_vendor_conversation(task, suggestion, vendor_id)

            suggestion.task_id = task.id

            draft = edited_body or payload.get("draft_message")
            if draft:
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
                    vendor = get_vendor_by_id(self.db, vid)
                    if vendor:
                        action_payload["vendor_id"] = str(vendor.external_id)
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

        if action in ("send_and_create_task", "edit_message"):
            payload = suggestion.action_payload or {}

            # This suggestion is linked to an existing task
            if suggestion.task_id:
                task = self.db.execute(
                    select(Task).where(Task.id == suggestion.task_id)
                ).scalar_one_or_none()

            if task:
                draft = edited_body or payload.get("draft_message")
                if draft:
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

        if action in ("message_person_send", "edit_message") and not suggestion.task_id:
            # Standalone (no-task) message suggestion: the tool intentionally
            # did NOT create the conversation at draft time. Materialise it
            # now, drop the draft into it, and let ``_resolve_suggestion``
            # mark the suggestion accepted below. Nothing to do for task.
            payload = suggestion.action_payload or {}
            entity_id = payload.get("entity_id")
            entity_type = payload.get("entity_type")
            draft = edited_body or payload.get("draft_message")
            if entity_id and entity_type and draft:
                self._send_standalone_message(
                    suggestion=suggestion,
                    entity_id=str(entity_id),
                    entity_type=str(entity_type),
                    draft=str(draft),
                )

        if action in ("message_person_send", "edit_message") and suggestion.task_id:
            payload = suggestion.action_payload or {}
            entity_id = payload.get("entity_id")
            entity_type = payload.get("entity_type")
            draft = edited_body or payload.get("draft_message")

            task = self.db.execute(
                select(Task).where(Task.id == suggestion.task_id)
            ).scalar_one_or_none()

            if task and entity_id and draft:
                target_convo: Conversation | None = None
                # Wire a task-scoped conversation per (task, entity). Reuses if
                # this task already has a thread with this specific entity;
                # otherwise creates a new one — so reaching out to vendor B
                # while vendor A already has a thread produces a distinct
                # conversation rather than piggybacking on vendor A's.
                if entity_type == "vendor":
                    target_convo = self._wire_vendor_conversation(task, suggestion, entity_id)
                elif entity_type == "tenant":
                    from db.models import Tenant as TenantModel
                    from gql.services.tenant_service import TenantService

                    t_obj = self.db.execute(
                        select(TenantModel).where(TenantModel.external_id == str(entity_id))
                    ).scalar_one_or_none()
                    if not t_obj:
                        raise ValueError(f"Tenant {entity_id} not found")
                    existing = self._task_conversation_with_entity(
                        task,
                        conversation_type=ConversationType.TENANT,
                        participant_type=ParticipantType.TENANT,
                        entity_user_id=t_obj.user_id,
                    )
                    if existing is None:
                        target_convo = chat_service.get_or_create_external_conversation(
                            self.db,
                            conversation_type=ConversationType.TENANT,
                            subject=suggestion.title or "",
                            property_id=suggestion.property_id,
                            unit_id=suggestion.unit_id,
                            tenant_id=t_obj.id,
                            parent_task_id=task.id,
                        )
                        if not task.parent_conversation_id:
                            task.parent_conversation_id = target_convo.id
                    else:
                        target_convo = existing
                    TenantService.ensure_portal_token(self.db, t_obj)

                # Find the conversation to send to
                conv_id = target_convo.id if target_convo is not None else self._resolve_conversation_for_entity(task, entity_type, entity_id)
                if conv_id:
                    chat_service.send_autonomous_message(
                        self.db, conversation_id=conv_id, body=draft, task_id=task.id,
                    )
                    # Notify the recipient out-of-band (SMS) with a login-less
                    # link back to the conversation.
                    self._notify_recipient(
                        task=task,
                        entity_type=entity_type,
                        entity_id=entity_id,
                        conversation_id=conv_id,
                        draft=draft,
                    )

        suggestion = self._resolve_suggestion(suggestion_id, action, task)
        return suggestion, task

    def _send_standalone_message(
        self,
        *,
        suggestion: Suggestion,
        entity_id: str,
        entity_type: str,
        draft: str,
    ) -> None:
        """Materialise a standalone (no-task) conversation at approval time.

        The no-task ``message_person`` tool path intentionally defers
        conversation creation until the manager approves the draft so a
        dismissed/edited suggestion leaves no orphaned Conversation rows
        behind. This helper creates the conversation, records the draft,
        and writes the resolved conversation_id back onto the suggestion's
        ``action_payload`` for traceability.
        """
        from db.models import Tenant as TenantModel

        conv_type = (
            ConversationType.TENANT if entity_type == "tenant"
            else ConversationType.VENDOR
        )
        if entity_type == "tenant":
            tenant = self.db.execute(
                select(TenantModel).where(TenantModel.external_id == str(entity_id))
            ).scalar_one_or_none()
            if not tenant:
                raise ValueError(f"Tenant {entity_id} not found")
            participant_kwargs = {"tenant_id": tenant.id}
        else:
            vendor = get_vendor_by_external_id(self.db, str(entity_id))
            if not vendor:
                raise ValueError(f"Vendor {entity_id} not found")
            participant_kwargs = {"vendor_id": vendor.id}

        convo = chat_service.get_or_create_external_conversation(
            self.db,
            conversation_type=conv_type,
            subject=suggestion.title or "",
            property_id=suggestion.property_id,
            unit_id=suggestion.unit_id,
            **participant_kwargs,
        )
        chat_service.send_autonomous_message(
            self.db, conversation_id=convo.id, body=draft,
        )
        # Record the materialised conversation on the suggestion so the UI
        # can link it after approval.
        updated_payload = dict(suggestion.action_payload or {})
        updated_payload["conversation_id"] = str(convo.id)
        suggestion.action_payload = updated_payload
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(suggestion, "action_payload")

    def _resolve_conversation_for_entity(
        self, task: Task, entity_type: str, entity_id: str | None,
    ) -> int | None:
        """Find the task-scoped conversation ID for a specific entity."""
        if entity_type == "vendor":
            vendor = get_vendor_by_external_id(self.db, str(entity_id)) if entity_id else None
            if not vendor:
                return None
            convo = self._task_conversation_with_entity(
                task,
                conversation_type=ConversationType.VENDOR,
                participant_type=ParticipantType.EXTERNAL_CONTACT,
                entity_user_id=vendor.id,
            )
            return convo.id if convo else None
        elif entity_type == "tenant":
            from db.models import Tenant as TenantModel
            t_obj = self.db.execute(
                select(TenantModel).where(TenantModel.external_id == str(entity_id))
            ).scalar_one_or_none() if entity_id else None
            if not t_obj:
                return None
            convo = self._task_conversation_with_entity(
                task,
                conversation_type=ConversationType.TENANT,
                participant_type=ParticipantType.TENANT,
                entity_user_id=t_obj.user_id,
            )
            return convo.id if convo else None
        return None

    def _notify_recipient(
        self,
        *,
        task: Task,
        entity_type: str,
        entity_id: str,
        conversation_id: int,
        draft: str,
    ) -> None:
        """Dispatch a notification (SMS today) with a login-less link to the thread."""
        from gql.services.notification_service import NotificationRequest, NotificationService

        recipient_user_id = self._recipient_user_id(entity_type, entity_id)
        if recipient_user_id is None:
            return
        blurb = f"RentMate update: {task.title}" if task.title else "RentMate update"
        NotificationService.dispatch(
            self.db,
            NotificationRequest(
                recipient_user_id=recipient_user_id,
                conversation_id=conversation_id,
                title=blurb,
                messages=[draft],
                kind="conversation_update",
                task_id=task.id,
            ),
        )

    def _recipient_user_id(self, entity_type: str, entity_id: str) -> int | None:
        """Resolve a tenant or vendor external id to its underlying User.id."""
        if entity_type == "vendor":
            vendor = get_vendor_by_external_id(self.db, str(entity_id))
            return vendor.id if vendor else None
        if entity_type == "tenant":
            from db.models import Tenant as TenantModel
            tenant = self.db.execute(
                select(TenantModel).where(TenantModel.external_id == str(entity_id))
            ).scalar_one_or_none()
            return tenant.user_id if tenant else None
        return None
