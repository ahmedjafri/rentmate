"""Database helpers for inbound email ingestion.

Kept separate from the already-large db/lib.py so the email pipeline has a
clean home.  The key design constraints:

- All functions accept an open Session and flush but never commit — the caller
  (process_inbound_email in handlers/chat.py) owns the transaction boundary.
- MIRRORED_CHAT conversations are read-only mirrors; we never write AI replies
  into them.  Replies from the agent live in the Task's TASK_AI conversation.
- Thread identity is keyed on the RFC email thread anchor stored in
  Conversation.extra["email_thread_id"].  All replies in the same thread map
  to the same Conversation row.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from db.models import (
    Conversation,
    ConversationType,
    Message,
    MessageType,
    ParticipantType,
)
from db.models.rental import Tenant
from db.models.account import User
from handlers.email_parser import ParsedEmail
from integrations.local_auth import resolve_account_id, resolve_org_id


# ─── Sender resolution ────────────────────────────────────────────────────────

def resolve_email_sender(
    db: Session,
    *,
    email: str,
    display_name: str,
) -> Tuple[Optional[object], str]:
    """Map the sender's email address to a known RentMate entity.

    Checks tenants first (most common case for inbound email), then account
    users and vendors.  Returns a (entity, label) pair where label is one of
    "tenant", "account_user", "external".  Returns (None, "external") when
    the address doesn't match anyone in the DB — we still store the email,
    we just don't spawn an autonomous task for unknown senders.
    """
    if not email:
        return None, "external"

    # Tenants are stored via their linked User row which carries the email.
    tenant = (
        db.query(Tenant)
        .join(User, Tenant.user_id == User.id)
        .filter(User.email == email)
        .first()
    )
    if tenant:
        return tenant, "tenant"

    # Check account users (property managers) and vendors.
    user = db.query(User).filter(User.email == email).first()
    if user:
        label = "account_user" if getattr(user, "user_type", None) != "vendor" else "vendor"
        return user, label

    return None, "external"


# ─── Conversation threading ───────────────────────────────────────────────────

def find_email_conversation_by_thread(
    db: Session,
    *,
    thread_id: str,
) -> Optional[Conversation]:
    """Look up the existing MIRRORED_CHAT conversation for an email thread.

    All emails that share the same thread root (computed by
    email_parser._extract_thread_id) should append their messages to the
    same Conversation row so the whole chain is visible in one place.
    We search by Conversation.extra['email_thread_id'] rather than subject
    because subjects can change mid-thread (e.g. "Re: Broken heater in 4B").
    Uses the ix_conversations_email_thread_id index (created in migration
    e1f2a3b4c5d6) so this is O(log n) even with millions of conversations.
    """
    return (
        db.query(Conversation)
        .filter(
            Conversation.org_id == resolve_org_id(),
            Conversation.conversation_type == ConversationType.MIRRORED_CHAT,
            text("extra->>'email_thread_id' = :tid").bindparams(tid=str(thread_id)),
            text("extra->>'source' = 'email'"),
        )
        .first()
    )


def create_email_mirrored_conversation(
    db: Session,
    *,
    parsed: ParsedEmail,
    tenant: Optional[Tenant] = None,
    property_id: Optional[str] = None,
) -> Conversation:
    """Create a new MIRRORED_CHAT conversation for a brand-new email thread.

    This is called the first time we see a particular email thread — all
    subsequent replies in the same thread will append to this conversation
    via find_email_conversation_by_thread.

    The extra JSON carries enough metadata for the agent to understand
    the email origin without having to decode Message.meta on every read.
    """
    now = datetime.now(UTC)
    subject = parsed.subject[:255] if parsed.subject else "Inbound email"

    # Resolve property/unit/lease from the tenant's most recent active lease.
    resolved_property_id = property_id
    resolved_unit_id = None
    resolved_lease_id = None
    tenant_display_name = None
    if tenant:
        from db.models.rental import Lease
        lease = (
            db.query(Lease)
            .filter_by(tenant_id=tenant.id)
            .order_by(Lease.created_at.desc())
            .first()
        )
        if lease:
            resolved_property_id = resolved_property_id or lease.property_id
            resolved_unit_id = lease.unit_id
            resolved_lease_id = lease.id
        # Build a display name from the linked User row if available.
        tenant_user = getattr(tenant, "user", None)
        if tenant_user:
            first = getattr(tenant_user, "first_name", None) or ""
            last = getattr(tenant_user, "last_name", None) or ""
            tenant_display_name = f"{first} {last}".strip() or None

    extra: dict = {
        "source": "email",
        # Stable key that links all replies in this thread to this conversation.
        "email_thread_id": parsed.thread_id,
        "email_from": parsed.from_email,
        # Prevents accidental writes — the send-message path checks this flag
        # and raises MirrorConversationReadOnly if True.
        "read_only": True,
    }
    # Tenant identity snapshot — gives the agent immediate context without
    # needing an extra DB query on every read.
    if tenant:
        extra["tenant_id"] = tenant.id
        if tenant_display_name:
            extra["tenant_name"] = tenant_display_name

    conv = Conversation(
        org_id=resolve_org_id(),
        creator_id=resolve_account_id(),
        subject=subject,
        property_id=resolved_property_id,
        unit_id=resolved_unit_id,
        conversation_type=ConversationType.MIRRORED_CHAT,
        is_group=False,
        is_archived=False,
        created_at=now,
        updated_at=now,
        extra=extra,
    )
    db.add(conv)
    db.flush()
    return conv


# ─── Deduplication ────────────────────────────────────────────────────────────

def is_email_message_duplicate(
    db: Session,
    *,
    message_id: str,
) -> bool:
    """Return True if we already stored this exact email.

    Postmark retries webhook delivery on network errors, so we need to be
    idempotent.  The RFC Message-ID header uniquely identifies each email
    across the entire mail system and is stored in Message.meta['email_message_id'].
    """
    if not message_id:
        return False
    # Use PostgreSQL's ->> operator (text extraction) via a raw SQL expression.
    # This hits the partial expression index on meta->>'email_message_id' created
    # in migration a1b2c3d4e5f6_email_inbound_indexes so the lookup is O(log n).
    # We can't use SQLAlchemy's .astext here because Message.meta is Column(JSON)
    # not Column(JSONB) — .astext is only available on the JSONB dialect type.
    return (
        db.query(Message)
        .filter(text("meta->>'email_message_id' = :mid").bindparams(mid=message_id))
        .first()
    ) is not None


# ─── Message persistence ──────────────────────────────────────────────────────

def append_email_message(
    db: Session,
    *,
    conversation: Conversation,
    parsed: ParsedEmail,
    sender_type: ParticipantType,
    sender_name: str,
) -> Message:
    """Add one email as a Message row inside a MIRRORED_CHAT conversation.

    We store the full email metadata in Message.meta so the agent has
    access to threading context (subject, cc list, in-reply-to) without
    having to parse the body.  The email's actual send timestamp is used
    as sent_at so messages sort in the correct chronological order even
    if the webhook fires late.
    """
    meta = {
        "source": "email",
        # Dedup key — matched by is_email_message_duplicate on re-delivery.
        "email_message_id": parsed.message_id,
        "in_reply_to": parsed.in_reply_to,
        "subject": parsed.subject,
        "from_email": parsed.from_email,
        "to_emails": parsed.to_emails,
        "cc_emails": parsed.cc_emails,
    }

    # Convert attachment list to the format Message.attachments expects.
    # We store name + content_type for display; content_base64 is excluded
    # from the main meta to keep message rows lean.  Large attachments
    # should be stored via the document pipeline instead.
    attachments = [
        {"name": a["name"], "content_type": a["content_type"], "size_bytes": a["size_bytes"]}
        for a in (parsed.attachments or [])
    ] or None

    msg = Message(
        org_id=resolve_org_id(),
        conversation_id=conversation.id,
        sender_type=sender_type,
        sender_id=None,  # no ConversationParticipant row for mirrored external senders
        body=parsed.body_text or None,
        body_html=parsed.body_html or None,
        attachments=attachments,
        meta=meta,
        is_system=False,
        message_type=MessageType.MESSAGE,
        sender_name=sender_name or parsed.from_email,
        is_ai=False,
        sent_at=parsed.received_at,
    )
    db.add(msg)

    conversation.updated_at = parsed.received_at
    flag_modified(conversation, "extra")
    db.flush()
    return msg


# ─── Top-level orchestrator ───────────────────────────────────────────────────

def ingest_email(
    db: Session,
    *,
    parsed: ParsedEmail,
    auto_spawn_task: bool = True,
) -> Tuple[Optional[Conversation], Optional[Message], bool]:
    """Store one inbound email and optionally spawn a Task for the agent.

    This is the single entry point for the email pipeline.  Returns
    (conversation, message, task_was_spawned).  Returns (None, None, False)
    when the email is a duplicate and was safely skipped.

    The caller (process_inbound_email in handlers/chat.py) owns the DB
    transaction — we flush but do not commit.

    Flow:
    1. Dedup check — skip silently if we already have this Message-ID.
    2. Resolve the sender to a Tenant, User, or unknown external.
    3. Find or create the MIRRORED_CHAT conversation for this email thread.
    4. Append the email body as a Message row.
    5. Optionally spawn a Task so the agent can classify and handle the email
       autonomously (maintenance, payment question, lease query, etc.).
    """
    # ── 1. Dedup ──────────────────────────────────────────────────────────────
    if is_email_message_duplicate(db, message_id=parsed.message_id):
        print(f"[email-ingest] Duplicate message_id={parsed.message_id!r} — skipping")
        return None, None, False

    # ── 2. Resolve sender ─────────────────────────────────────────────────────
    entity, entity_type = resolve_email_sender(
        db, email=parsed.from_email, display_name=parsed.from_name
    )
    # Map entity type to the ParticipantType enum the Message model expects.
    sender_type_map = {
        "tenant":       ParticipantType.TENANT,
        "account_user": ParticipantType.ACCOUNT_USER,
        "vendor":       ParticipantType.EXTERNAL_CONTACT,
        "external":     ParticipantType.EXTERNAL_CONTACT,
    }
    sender_type = sender_type_map.get(entity_type, ParticipantType.EXTERNAL_CONTACT)
    sender_name = parsed.from_name or parsed.from_email

    # Only tenants (and known external contacts in the future) trigger
    # autonomous task creation.  Unknown senders are stored for context only.
    tenant = entity if entity_type == "tenant" else None

    print(
        f"[email-ingest] from={parsed.from_email!r} "
        f"subject={parsed.subject!r} "
        f"entity_type={entity_type}"
    )

    # ── 3. Find or create the thread conversation ─────────────────────────────
    try:
        conv = find_email_conversation_by_thread(db, thread_id=parsed.thread_id)
        if conv is None:
            conv = create_email_mirrored_conversation(db, parsed=parsed, tenant=tenant)
    except Exception:
        # On a race-condition unique violation the conversation already exists —
        # retry the lookup once before giving up.
        import traceback
        traceback.print_exc()
        conv = find_email_conversation_by_thread(db, thread_id=parsed.thread_id)
        if conv is None:
            raise

    # ── 4. Append the email as a message ──────────────────────────────────────
    msg = append_email_message(
        db,
        conversation=conv,
        parsed=parsed,
        sender_type=sender_type,
        sender_name=sender_name,
    )

    # ── 5. Optionally spawn a Task ────────────────────────────────────────────
    task_spawned = False
    if auto_spawn_task and tenant is not None:
        # Only spawn one task per thread — if the conversation already has a
        # parent_task_id the agent is already handling this thread.
        if conv.parent_task_id is None:
            from db.lib import spawn_task_from_conversation
            from db.enums import TaskSource
            from db.models.rental import Lease
            task = spawn_task_from_conversation(
                db,
                parent_conversation_id=str(conv.id),
                objective=(
                    f"Handle email from {sender_name} <{parsed.from_email}>: "
                    f"{parsed.subject or '(no subject)'}"
                ),
                # Let the agent decide the final category after reading the email.
                # Passing None here avoids locking it into the wrong bucket before
                # the agent has had a chance to classify it.
                category=None,
                source=TaskSource.TENANT_REPORT,
            )

            # Attach the tenant's property/unit/lease to the task so the agent
            # context builder can load property details, unit info, and lease
            # terms when classifying and handling this email.
            lease = (
                db.query(Lease)
                .filter_by(tenant_id=tenant.id)
                .order_by(Lease.created_at.desc())
                .first()
            )
            if lease:
                task.property_id = lease.property_id
                task.unit_id = lease.unit_id
                task.lease_id = lease.id
                db.flush()

            task_spawned = True
            print(
                f"[email-ingest] Spawned task id={task.id} "
                f"property={task.property_id} unit={task.unit_id} "
                f"for thread {parsed.thread_id!r}"
            )

    db.flush()
    return conv, msg, task_spawned
