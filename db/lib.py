# db/lib.py

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import List, Optional

from sqlalchemy import func, select as sa_select
from sqlalchemy.orm import Session, joinedload

from .models import (
    Conversation,
    ConversationParticipant,
    ConversationType,
    ExternalContact,
    Lease,
    Message,
    MessageReceipt,
    ParticipantType,
    Property,
    Task,
    Tenant,
    Unit,
)
from .utils import normalize_phone


def _normalize_address(addr: str) -> str:
    """Normalize an address string for fuzzy comparison."""
    if not addr:
        return ""
    s = addr.upper().strip()
    s = re.sub(r',?\s*U\.?S\.?A?\.?\s*$', '', s)           # strip trailing USA
    s = re.sub(r'\b(\d{5})-\d{4}\b', r'\1', s)             # zip+4 → zip5
    abbrevs = {
        'NORTHEAST': 'NE', 'NORTHWEST': 'NW', 'SOUTHEAST': 'SE', 'SOUTHWEST': 'SW',
        'NORTH': 'N', 'SOUTH': 'S', 'EAST': 'E', 'WEST': 'W',
        'DRIVE': 'DR', 'STREET': 'ST', 'AVENUE': 'AVE', 'ROAD': 'RD',
        'BOULEVARD': 'BLVD', 'COURT': 'CT', 'LANE': 'LN', 'PLACE': 'PL',
        'CIRCLE': 'CIR', 'TRAIL': 'TRL', 'PARKWAY': 'PKY',
    }
    for full, short in abbrevs.items():
        s = re.sub(rf'\b{full}\b', short, s)
    s = re.sub(r'[,\.#]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def _address_similarity(a: str, b: str) -> float:
    """Jaccard similarity on word tokens of two normalized addresses."""
    if a == b:
        return 1.0
    a_w = set(a.split())
    b_w = set(b.split())
    if not a_w or not b_w:
        return 0.0
    return len(a_w & b_w) / len(a_w | b_w)


def find_candidate_properties(db: Session, *, address: str, threshold: float = 0.55) -> list:
    """Return existing properties whose address fuzzy-matches the query, scored 0-1."""
    if not address:
        return []
    norm_in = _normalize_address(address)
    candidates = []
    for p in db.query(Property).all():
        parts = [p.address_line1, p.city, p.state, p.postal_code]
        full = " ".join(x for x in parts if x)
        score = _address_similarity(norm_in, _normalize_address(full))
        if score >= threshold:
            candidates.append({
                "id": str(p.id),
                "name": p.name,
                "address": full,
                "property_type": p.property_type or "multi_family",
                "score": round(score, 2),
            })
    candidates.sort(key=lambda x: -x["score"])
    return candidates[:5]


# -------------------------------
# Core helpers
# -------------------------------

def get_or_create_tenant_by_phone(
    db: Session,
    *, phone: str,
    first_name: str = "Unknown",
    last_name: str = "Tenant",
) -> Tenant:
    """
    Resolve a Tenant by phone number, creating one if needed.
    """
    phone_norm = normalize_phone(phone) if phone else None

    tenant = (
        db.query(Tenant)
        .filter(Tenant.phone == phone_norm)
        .one_or_none()
    )
    if tenant:
        return tenant

    tenant = Tenant(
        id=str(uuid.uuid4()),
        first_name=first_name,
        last_name=last_name,
        phone=phone_norm,
        created_at=datetime.now(UTC),
    )
    db.add(tenant)
    db.flush()
    return tenant


def get_or_create_conversation_for_tenant(
    db: Session,
    *, tenant: Tenant,
    subject: Optional[str] = None,
) -> Conversation:
    """
    Find the most recent non-archived 1:1 conversation for this tenant
    (where this tenant is the ONLY active participant), or create a new one.
    """
    convs = (
        db.query(Conversation)
        .options(joinedload(Conversation.participants))
        .join(ConversationParticipant)
        .filter(
            Conversation.is_archived.is_(False),
            ConversationParticipant.tenant_id == tenant.id,
        )
        .order_by(Conversation.updated_at.desc())
        .all()
    )

    for conv in convs:
        active_parts = [p for p in conv.participants if p.is_active]
        if len(active_parts) == 1 and active_parts[0].tenant_id == tenant.id:
            return conv

    now = datetime.now(UTC)
    conv = Conversation(
        id=str(uuid.uuid4()),
        subject=subject or f"Conversation with {tenant.first_name} {tenant.last_name}",
        is_group=False,
        is_archived=False,
        created_at=now,
        updated_at=now,
    )
    db.add(conv)
    db.flush()

    participant = ConversationParticipant(
        id=str(uuid.uuid4()),
        conversation_id=conv.id,
        participant_type=ParticipantType.TENANT,
        tenant_id=tenant.id,
        is_active=True,
        joined_at=now,
    )
    db.add(participant)
    db.flush()

    return conv


def add_message(
    db: Session,
    *, conversation: Conversation,
    sender_type: ParticipantType,
    body: Optional[str] = None,
    body_html: Optional[str] = None,
    meta: Optional[dict] = None,
    attachments: Optional[dict] = None,
    sender_tenant: Optional[Tenant] = None,
    sender_external_contact: Optional[ExternalContact] = None,
    is_system: bool = False,
) -> Message:
    """
    Create a Message in a Conversation, update the conversation's updated_at,
    and create MessageReceipt rows for all active participants.
    """
    now = datetime.now(UTC)

    msg = Message(
        id=str(uuid.uuid4()),
        conversation_id=conversation.id,
        sender_type=sender_type,
        sender_tenant_id=sender_tenant.id if sender_tenant else None,
        sender_external_contact_id=sender_external_contact.id if sender_external_contact else None,
        body=body,
        body_html=body_html,
        attachments=attachments,
        meta=meta,
        is_system=is_system,
        sent_at=now,
    )

    msg.validate_sender()

    db.add(msg)

    conversation.updated_at = now
    db.add(conversation)
    db.flush()

    active_participants: List[ConversationParticipant] = [
        p for p in conversation.participants if p.is_active
    ]
    for p in active_participants:
        receipt = MessageReceipt(
            id=str(uuid.uuid4()),
            message_id=msg.id,
            conversation_participant_id=p.id,
            delivered_at=now,
            read_at=None,
        )
        db.add(receipt)

    db.flush()
    return msg


def list_conversations(
    db: Session,
    *, limit: int = 50,
    offset: int = 0,
) -> List[Conversation]:
    """
    Return a paginated list of conversations, newest first.
    """
    return (
        db.query(Conversation)
        .order_by(Conversation.updated_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


def get_conversation_with_messages(
    db: Session,
    conversation_id: str,
) -> Optional[Conversation]:
    """
    Fetch a conversation plus its messages and participants.
    """
    return (
        db.query(Conversation)
        .options(
            joinedload(Conversation.participants)
            .joinedload(ConversationParticipant.tenant),
            joinedload(Conversation.participants)
            .joinedload(ConversationParticipant.external_contact),
            joinedload(Conversation.messages),
        )
        .filter(Conversation.id == conversation_id)
        .one_or_none()
    )


# -------------------------------
# Inbound channel router
# -------------------------------

def _classify_task_match(body: str, candidates: list) -> str:
    """
    Ask a cheap LLM to classify whether a new message continues an existing task
    or is a new issue.

    Returns a task id string from candidates, or "new".
    """
    import os
    try:
        from litellm import completion as litellm_completion
    except ImportError:
        return "new"

    model = os.getenv("CLASSIFY_MODEL", "claude-haiku-4-5-20251001")
    api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("LITELLM_API_KEY")

    task_lines = "\n".join(
        f'{i+1}. [{c["id"]}] "{c["subject"]}" — last message: "{c["last_snippet"]}"'
        for i, c in enumerate(candidates)
    )
    prompt = (
        f'Given this new message from a tenant:\n"{body}"\n\n'
        f"And these open tasks:\n{task_lines}\n\n"
        f'Reply with only the task ID this message most likely continues, or "new" if it is a distinct issue.'
    )

    try:
        resp = litellm_completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=50,
            api_key=api_key,
        )
        result = resp.choices[0].message.content.strip().strip('"').strip("'")
        candidate_ids = {c["id"] for c in candidates}
        return result if result in candidate_ids else "new"
    except Exception as e:
        print(f"[_classify_task_match] LLM call failed: {e}")
        return "new"


def route_inbound_to_task(
    db: Session,
    *,
    tenant: Tenant,
    body: str,
    channel_type: str,
    sender_meta: dict,
    account_id: str = "default",
) -> tuple:
    """
    Central router for all inbound channels (SMS, email).

    Finds or creates a task for the inbound message, adds the message,
    and returns (conversation, message).
    """
    now = datetime.now(UTC)
    recency_cutoff = now - timedelta(days=30)

    # Find active tasks for this tenant updated within last 30 days
    # Join from Conversation to Task via Task.ai_conversation_id (inverted FK)
    candidates_raw = (
        db.query(Conversation)
        .options(
            joinedload(Conversation.participants),
            joinedload(Conversation.messages),
        )
        .join(ConversationParticipant, ConversationParticipant.conversation_id == Conversation.id)
        .join(Task, Task.ai_conversation_id == Conversation.id)
        .filter(
            Task.task_status == "active",
            Conversation.updated_at >= recency_cutoff,
            ConversationParticipant.tenant_id == tenant.id,
            ConversationParticipant.is_active.is_(True),
        )
        .order_by(Conversation.updated_at.desc())
        .all()
    )

    task_created = False
    conv = None

    if not candidates_raw:
        # No active tasks → create a new one
        task_created = True
    elif len(candidates_raw) == 1:
        conv = candidates_raw[0]
    else:
        # Multiple candidates — ask LLM to pick the right one
        candidates_for_llm = []
        for c in candidates_raw:
            last_msgs = sorted(c.messages, key=lambda m: m.sent_at)
            last_snippet = last_msgs[-1].body[:120] if last_msgs else ""
            candidates_for_llm.append({
                "id": c.id,
                "subject": c.subject or "",
                "last_snippet": last_snippet,
            })
        chosen_id = _classify_task_match(body, candidates_for_llm)
        if chosen_id != "new":
            conv = next((c for c in candidates_raw if c.id == chosen_id), None)
        if conv is None:
            task_created = True

    if task_created or conv is None:
        task = Task(
            id=str(uuid.uuid4()),
            account_id=account_id,
            title=f"Message from {tenant.first_name} {tenant.last_name}",
            task_status="active",
            task_mode="autonomous",
            source=channel_type,
            channel_type=channel_type,
            created_at=now,
            updated_at=now,
        )
        db.add(task)
        db.flush()

        # Assign task_number per account
        max_num = db.execute(
            sa_select(func.coalesce(func.max(Task.task_number), 0))
            .where(Task.account_id == task.account_id)
        ).scalar()
        task.task_number = max_num + 1

        conv = Conversation(
            id=str(uuid.uuid4()),
            subject=f"Message from {tenant.first_name} {tenant.last_name}",
            is_group=False,
            is_archived=False,
            created_at=now,
            updated_at=now,
        )
        db.add(conv)
        db.flush()

        task.ai_conversation_id = conv.id

        participant = ConversationParticipant(
            id=str(uuid.uuid4()),
            conversation_id=conv.id,
            participant_type=ParticipantType.TENANT,
            tenant_id=tenant.id,
            is_active=True,
            joined_at=now,
        )
        db.add(participant)
        db.flush()

        # Reload with relationships so add_message can see participants
        db.refresh(conv)

    msg = add_message(
        db=db,
        conversation=conv,
        sender_type=ParticipantType.TENANT,
        body=body,
        meta=sender_meta,
        sender_tenant=tenant,
    )

    return conv, msg


# -------------------------------
# Quo-specific helper
# -------------------------------

def record_sms_from_quo(
    db: Session,
    *,
    from_number: str,
    to_number: str,
    body: str,
) -> Optional[Message]:
    """
    High-level helper for inbound Quo SMS.

    Resolves tenant via SMS router, routes to a task via route_inbound_to_task,
    and returns (message, conversation) for the caller to drive the agent.
    """
    from backends.wire import sms_router

    resolved = sms_router.resolve(db, from_number, to_number)
    if not resolved:
        print(
            f"Tenant not resolved for numbers, "
            f"from_number={from_number}, to_number={to_number}"
        )
        return None, None

    _account_id, tenant, direction = resolved

    if direction != "inbound":
        # Outbound messages from the admin side — just record as plain message
        conv = get_or_create_conversation_for_tenant(db=db, tenant=tenant)
        msg = add_message(
            db=db,
            conversation=conv,
            sender_type=ParticipantType.ACCOUNT_USER,
            body=body,
            meta={
                "source": "quo",
                "direction": direction,
                "from_number": from_number,
                "to_number": to_number,
            },
            is_system=True,
        )
        db.commit()
        return msg, None

    # Inbound: route to a task
    conv, msg = route_inbound_to_task(
        db,
        tenant=tenant,
        body=body,
        channel_type="sms",
        sender_meta={
            "source": "quo",
            "direction": "inbound",
            "from_number": from_number,
            "to_number": to_number,
        },
    )

    db.commit()

    print(
        f"[Quo] Recorded SMS msg={msg.id} conv={msg.conversation_id} tenant={tenant.id}"
    )

    return msg, conv


def _compute_single_suggestions(db: Session, data: dict) -> list:
    """
    Compare LLM-extracted lease data against existing DB records and return
    a list of actionable suggestions the user can choose to apply or skip.

    Each suggestion is a dict:
      { id, type, label, description, payload }
    """
    suggestions = []

    address = (data.get("property_address") or "").strip()
    unit_label = (data.get("unit_label") or "Main").strip()
    email = (data.get("tenant_email") or "").strip() or None
    first_name = (data.get("tenant_first_name") or "").strip()
    last_name = (data.get("tenant_last_name") or "").strip()
    phone = (data.get("tenant_phone") or "").strip() or None
    start_raw = data.get("lease_start_date")
    end_raw = data.get("lease_end_date")
    rent = data.get("monthly_rent")

    # --- Property ---
    prop = None
    if address:
        prop = (
            db.query(Property)
            .filter(func.lower(Property.address_line1).ilike(f"%{address.lower()}%"))
            .first()
        )
        if not prop:
            suggestions.append({
                "id": "create_property",
                "type": "create",
                "entity": "property",
                "label": f"Add property: {address}",
                "description": f"No property matching \"{address}\" exists. Create it.",
                "payload": {
                    "property_address": address,
                    "property_type": (data.get("property_type") or "multi_family"),
                },
            })
        else:
            # Check for unit
            unit = (
                db.query(Unit)
                .filter(Unit.property_id == prop.id, func.lower(Unit.label) == unit_label.lower())
                .first()
            )
            if not unit:
                suggestions.append({
                    "id": "create_unit",
                    "type": "create",
                    "entity": "unit",
                    "label": f"Add unit \"{unit_label}\" to {prop.address_line1}",
                    "description": f"Unit \"{unit_label}\" not found at this property.",
                    "payload": {"unit_label": unit_label, "property_id": str(prop.id)},
                })

    # --- Tenant ---
    tenant = None
    if email:
        tenant = (
            db.query(Tenant)
            .filter(func.lower(Tenant.email) == email.lower())
            .first()
        )
    if not tenant and first_name and last_name:
        tenant = (
            db.query(Tenant)
            .filter(
                func.lower(Tenant.first_name) == first_name.lower(),
                func.lower(Tenant.last_name) == last_name.lower(),
            )
            .first()
        )

    if not tenant and (first_name or last_name or email or phone):
        suggestions.append({
            "id": "create_tenant",
            "type": "create",
            "entity": "tenant",
            "label": f"Add tenant: {(first_name + ' ' + last_name).strip() or email or phone or 'New tenant'}",
            "description": "No matching tenant found. Create a new tenant record.",
            "payload": {
                "tenant_first_name": first_name or None,
                "tenant_last_name": last_name or None,
                "tenant_email": email,
                "tenant_phone": phone,
            },
        })
    elif tenant:
        # Check if extracted data differs from existing record
        updates = {}
        if email and tenant.email and email.lower() != tenant.email.lower():
            updates["email"] = email
        if phone:
            from .utils import normalize_phone
            normalized = normalize_phone(phone)
            if normalized and tenant.phone and normalized != tenant.phone:
                updates["phone"] = normalized
        if first_name and tenant.first_name and first_name.lower() != tenant.first_name.lower():
            updates["first_name"] = first_name
        if last_name and tenant.last_name and last_name.lower() != tenant.last_name.lower():
            updates["last_name"] = last_name
        if updates:
            suggestions.append({
                "id": "update_tenant",
                "type": "update",
                "entity": "tenant",
                "label": f"Update tenant: {tenant.first_name} {tenant.last_name}",
                "description": f"Document has different values: {', '.join(updates.keys())}",
                "payload": {"tenant_id": str(tenant.id), **updates},
            })

    # --- Lease ---
    if prop and tenant and (start_raw or end_raw or rent):
        # Check if a lease already exists for this tenant+property
        existing_lease = (
            db.query(Lease)
            .filter(Lease.tenant_id == tenant.id, Lease.property_id == prop.id)
            .order_by(Lease.start_date.desc())
            .first()
        )
        if not existing_lease:
            suggestions.append({
                "id": "create_lease",
                "type": "create",
                "entity": "lease",
                "label": f"Create lease for {first_name} {last_name} at {address}",
                "description": f"Start: {start_raw or '?'}, End: {end_raw or '?'}, Rent: ${rent or '?'}/mo",
                "payload": {
                    "lease_start_date": start_raw,
                    "lease_end_date": end_raw,
                    "monthly_rent": rent,
                },
            })
        else:
            updates = {}
            if rent and float(rent) != existing_lease.rent_amount:
                updates["rent_amount"] = float(rent)
            if end_raw:
                try:
                    new_end = date.fromisoformat(end_raw)
                    if new_end != existing_lease.end_date:
                        updates["end_date"] = end_raw
                except ValueError:
                    pass
            if updates:
                suggestions.append({
                    "id": "update_lease",
                    "type": "update",
                    "entity": "lease",
                    "label": f"Update lease for {first_name} {last_name}",
                    "description": f"Document has different values: {', '.join(updates.keys())}",
                    "payload": {"lease_id": str(existing_lease.id), **updates},
                })

    return suggestions


def compute_suggestions(db: Session, data: dict) -> list:
    """
    Handle both old flat-dict format and new {"leases": [...]} format.
    Returns suggestions tagged with lease_index.
    """
    leases_raw = data.get("leases")
    if isinstance(leases_raw, list) and leases_raw:
        leases = leases_raw
    elif leases_raw is None and any(
        k in data for k in ('property_address', 'tenant_first_name', 'lease_start_date', 'monthly_rent')
    ):
        leases = [data]
    else:
        return []

    multi = len(leases) > 1
    result = []
    for i, lease in enumerate(leases):
        sug = _compute_single_suggestions(db, lease)
        for s in sug:
            s['lease_index'] = i
            if multi:
                s['id'] = f"{s['id']}_{i}"
        result.extend(sug)
    return result


def group_suggestions(doc_id: str, *, filename: str, suggestions: list, suggestion_states: dict, db: Optional[Session] = None) -> list:
    """
    Group flat suggestions by lease_index and entity type.
    Returns one set of location/tenant/lease groups per lease entry.
    """
    from collections import defaultdict
    states = suggestion_states or {}

    by_index: dict = defaultdict(list)
    for s in suggestions:
        by_index[s.get('lease_index', 0)].append(s)

    multi = len(by_index) > 1
    groups = []

    for idx, sug_list in sorted(by_index.items()):
        sfx = f"_{idx}" if multi else ""

        loc_key = f"location{sfx}"
        ten_key = f"tenant{sfx}"
        lea_key = f"lease{sfx}"

        location_sug = [s for s in sug_list if s['entity'] in ('property', 'unit')]
        tenant_sug   = [s for s in sug_list if s['entity'] == 'tenant']
        lease_sug    = [s for s in sug_list if s['entity'] == 'lease']

        if location_sug and states.get(loc_key) not in ('accepted', 'rejected'):
            address       = next((s['payload'].get('property_address', '') for s in location_sug if s['payload'].get('property_address')), '')
            unit          = next((s['payload'].get('unit_label', '') for s in location_sug if s['payload'].get('unit_label')), '')
            property_type = next((s['payload'].get('property_type') for s in location_sug if s['payload'].get('property_type')), 'multi_family')
            candidates    = find_candidate_properties(db, address) if db else []
            groups.append({
                'group_id':          f'{doc_id}_{loc_key}',
                'document_id':       doc_id,
                'document_filename': filename,
                'category':          'location',
                'lease_index':       idx,
                'title':             f'New property: {address}' if address else 'New property',
                'description':       f'Unit: {unit}' if unit else 'From document',
                'suggestion_ids':    [s['id'] for s in location_sug],
                'fields':            {'property_address': address, 'unit_label': unit, 'property_type': property_type},
                'state':             states.get(loc_key, 'pending'),
                'candidates':        candidates,
            })

        if tenant_sug and states.get(ten_key) not in ('accepted', 'rejected'):
            sug     = tenant_sug[0]
            payload = sug.get('payload', {})
            name    = f"{payload.get('tenant_first_name', '')} {payload.get('tenant_last_name', '')}".strip() or sug['label']
            groups.append({
                'group_id':          f'{doc_id}_{ten_key}',
                'document_id':       doc_id,
                'document_filename': filename,
                'category':          'tenant',
                'lease_index':       idx,
                'title':             f'Tenant: {name}' if name else 'New tenant',
                'description':       sug['description'],
                'suggestion_ids':    [s['id'] for s in tenant_sug],
                'fields':            payload,
                'state':             states.get(ten_key, 'pending'),
                'candidates':        [],
            })

        if lease_sug and states.get(lea_key) not in ('accepted', 'rejected'):
            sug     = lease_sug[0]
            payload = sug.get('payload', {})
            groups.append({
                'group_id':          f'{doc_id}_{lea_key}',
                'document_id':       doc_id,
                'document_filename': filename,
                'category':          'lease',
                'lease_index':       idx,
                'title':             sug['label'],
                'description':       sug['description'],
                'suggestion_ids':    [s['id'] for s in lease_sug],
                'fields':            payload,
                'state':             states.get(lea_key, 'pending'),
                'candidates':        [],
            })

    return groups


def route_inbound_to_tenant_chat(
    db: Session,
    *,
    tenant: Tenant,
    body: str,
    channel_type: str,
    sender_meta: dict,
) -> tuple:
    """
    Route an inbound message to a persistent tenant conversation (not a task).
    Finds or creates a conversation_type='tenant' conversation for the tenant,
    adds the message, and returns (conversation, message).
    """
    now = datetime.now(UTC)

    # Find existing open tenant conversation for this tenant
    existing = (
        db.query(Conversation)
        .options(joinedload(Conversation.participants))
        .join(ConversationParticipant)
        .filter(
            Conversation.conversation_type == ConversationType.TENANT,
            Conversation.is_archived.is_(False),
            ConversationParticipant.tenant_id == tenant.id,
            ConversationParticipant.is_active.is_(True),
        )
        .order_by(Conversation.updated_at.desc())
        .first()
    )

    if existing is None:
        existing = Conversation(
            id=str(uuid.uuid4()),
            subject=f"Conversation with {tenant.first_name} {tenant.last_name}",
            is_group=False,
            is_archived=False,
            conversation_type=ConversationType.TENANT,
            created_at=now,
            updated_at=now,
        )
        db.add(existing)
        db.flush()

        participant = ConversationParticipant(
            id=str(uuid.uuid4()),
            conversation_id=existing.id,
            participant_type=ParticipantType.TENANT,
            tenant_id=tenant.id,
            is_active=True,
            joined_at=now,
        )
        db.add(participant)
        db.flush()
        db.refresh(existing)

    msg = add_message(
        db=db,
        conversation=existing,
        sender_type=ParticipantType.TENANT,
        body=body,
        meta=sender_meta,
        sender_tenant=tenant,
    )
    return existing, msg


def spawn_task_from_conversation(
    db: Session,
    *,
    parent_conversation_id: str,
    objective: str,
    category: Optional[str] = None,
    urgency: Optional[str] = None,
    priority: Optional[str] = None,
    task_mode: str = "autonomous",
    source: str = "manual",
    account_id: Optional[str] = None,
) -> Task:
    """
    Spawn a Task as a child of an existing conversation.
    Creates a linked AI Conversation for the task thread.
    """
    parent = db.query(Conversation).filter(Conversation.id == parent_conversation_id).one_or_none()
    if not parent:
        raise ValueError(f"Parent conversation {parent_conversation_id} not found")

    # Derive account_id from parent conversation's property/unit if not provided
    if not account_id:
        from sqlalchemy import text as sa_text
        try:
            if parent.property_id:
                res = db.execute(sa_text("SELECT account_id FROM properties WHERE id = :id"), {"id": parent.property_id}).fetchone()
                account_id = res[0] if res and res[0] else None
            if not account_id and parent.unit_id:
                res = db.execute(sa_text("SELECT account_id FROM units WHERE id = :id"), {"id": parent.unit_id}).fetchone()
                account_id = res[0] if res and res[0] else None
        except Exception:
            pass
        account_id = account_id or "00000000-0000-0000-0000-000000000001"

    now = datetime.now(UTC)

    task = Task(
        id=str(uuid.uuid4()),
        account_id=account_id,
        title=objective,
        task_status="active",
        task_mode=task_mode,
        source=source,
        category=category,
        urgency=urgency,
        priority=priority,
        created_at=now,
        updated_at=now,
    )
    db.add(task)
    db.flush()

    # Assign task_number per account
    max_num = db.execute(
        sa_select(func.coalesce(func.max(Task.task_number), 0))
        .where(Task.account_id == task.account_id)
    ).scalar()
    task.task_number = max_num + 1

    convo = Conversation(
        id=str(uuid.uuid4()),
        subject=objective,
        is_group=False,
        is_archived=False,
        conversation_type=ConversationType.TASK_AI,
        parent_conversation_id=parent_conversation_id,
        created_at=now,
        updated_at=now,
    )
    db.add(convo)
    db.flush()

    task.ai_conversation_id = convo.id
    task.parent_conversation_id = parent_conversation_id
    task.external_conversation_id = parent_conversation_id
    return task


def get_or_create_user_ai_conversation(
    db: Session,
    *,
    account_id: str,
    user_id: str,
    session_key: Optional[str] = None,
) -> Conversation:
    """
    Get or create a persistent user_ai conversation for the given user.
    If session_key is provided, looks up an existing conversation by that key in subject.
    """
    now = datetime.now(UTC)

    if session_key:
        # Try to find by session_key in subject or a recent one
        existing = (
            db.query(Conversation)
            .filter(
                Conversation.conversation_type == ConversationType.USER_AI,
                Conversation.is_archived.is_(False),
                Conversation.subject == session_key,
            )
            .order_by(Conversation.updated_at.desc())
            .first()
        )
        if existing:
            return existing

    # Create a new user_ai conversation
    conv = Conversation(
        id=str(uuid.uuid4()),
        subject=session_key or "Chat with RentMate",
        is_group=False,
        is_archived=False,
        conversation_type=ConversationType.USER_AI,
        created_at=now,
        updated_at=now,
    )
    db.add(conv)
    db.flush()
    return conv


def apply_document_extraction(
    db: Session,
    *, data: dict,
    apply_only: Optional[List[str]] = None,
    property_id_override: Optional[str] = None,
) -> dict:
    """
    Upsert Property / Unit / Tenant / Lease from LLM-extracted lease data.

    If apply_only is provided (list of suggestion IDs), only the specified
    operations are performed. Pass None to apply everything (legacy behaviour).

    Returns IDs of found/created records plus a list of what was created/updated.
    """
    def _should(suggestion_id: str) -> bool:
        return apply_only is None or suggestion_id in apply_only

    created = []

    # --- Property ---
    address = (data.get("property_address") or "").strip()
    prop = None
    if property_id_override:
        prop = db.query(Property).filter(Property.id == property_id_override).first()
    elif address:
        # Try exact/fuzzy match using normalized address
        norm_input = _normalize_address(address)
        for candidate in db.query(Property).all():
            parts = [candidate.address_line1, candidate.city, candidate.state, candidate.postal_code]
            full = " ".join(x for x in parts if x)
            if _normalize_address(full) == norm_input:
                prop = candidate
                break
        if not prop:
            # Fall back to ilike
            prop = (
                db.query(Property)
                .filter(func.lower(Property.address_line1).ilike(f"%{address.lower()[:30]}%"))
                .first()
            )
        if not prop and _should("create_property"):
            prop = Property(
                id=str(uuid.uuid4()),
                address_line1=address,
                property_type=data.get("property_type") or "multi_family",
                source="document",
                created_at=datetime.now(UTC),
            )
            db.add(prop)
            db.flush()
            created.append("property")

    # --- Unit ---
    unit = None
    if prop:
        unit_label = (data.get("unit_label") or "Main").strip()
        unit = (
            db.query(Unit)
            .filter(Unit.property_id == prop.id, func.lower(Unit.label) == unit_label.lower())
            .first()
        )
        # For single-family: always ensure the one unit exists (even without an explicit suggestion)
        is_single_family = (prop.property_type or "multi_family") == "single_family"
        if not unit and (is_single_family or _should("create_unit")):
            unit = Unit(
                id=str(uuid.uuid4()),
                property_id=prop.id,
                label=unit_label,
                created_at=datetime.now(UTC),
            )
            db.add(unit)
            db.flush()
            created.append("unit")

    # --- Tenant ---
    tenant = None
    email = (data.get("tenant_email") or "").strip() or None
    first_name = (data.get("tenant_first_name") or "").strip()
    last_name = (data.get("tenant_last_name") or "").strip()
    phone = (data.get("tenant_phone") or "").strip() or None

    if email:
        tenant = (
            db.query(Tenant)
            .filter(func.lower(Tenant.email) == email.lower())
            .first()
        )
    if not tenant and first_name and last_name:
        tenant = (
            db.query(Tenant)
            .filter(
                func.lower(Tenant.first_name) == first_name.lower(),
                func.lower(Tenant.last_name) == last_name.lower(),
            )
            .first()
        )
    if not tenant and _should("create_tenant"):
        tenant = Tenant(
            id=str(uuid.uuid4()),
            first_name=first_name or "Unknown",
            last_name=last_name or "Tenant",
            email=email,
            phone=normalize_phone(phone) if phone else None,
            created_at=datetime.now(UTC),
        )
        db.add(tenant)
        db.flush()
        created.append("tenant")
    elif tenant and _should("update_tenant"):
        changed = False
        if email and email.lower() != (tenant.email or "").lower():
            tenant.email = email
            changed = True
        if phone:
            normalized = normalize_phone(phone)
            if normalized and normalized != tenant.phone:
                tenant.phone = normalized
                changed = True
        if first_name and first_name.lower() != (tenant.first_name or "").lower():
            tenant.first_name = first_name
            changed = True
        if last_name and last_name.lower() != (tenant.last_name or "").lower():
            tenant.last_name = last_name
            changed = True
        if changed:
            db.flush()
            created.append("tenant_updated")

    # --- Lease ---
    lease = None
    if prop and unit and tenant:
        start_raw = data.get("lease_start_date")
        end_raw = data.get("lease_end_date")
        rent = data.get("monthly_rent") or 0.0

        existing_lease = (
            db.query(Lease)
            .filter(Lease.tenant_id == tenant.id, Lease.property_id == prop.id)
            .order_by(Lease.start_date.desc())
            .first()
        )

        if not existing_lease and _should("create_lease"):
            try:
                start_date = date.fromisoformat(start_raw) if start_raw else date.today()
            except ValueError:
                start_date = date.today()
            try:
                end_date = date.fromisoformat(end_raw) if end_raw else date.today()
            except ValueError:
                end_date = date.today()

            lease = Lease(
                id=str(uuid.uuid4()),
                tenant_id=tenant.id,
                unit_id=unit.id,
                property_id=prop.id,
                start_date=start_date,
                end_date=end_date,
                rent_amount=float(rent),
                created_at=datetime.now(UTC),
            )
            db.add(lease)
            db.flush()
            created.append("lease")
        elif existing_lease and _should("update_lease"):
            changed = False
            if rent and float(rent) != existing_lease.rent_amount:
                existing_lease.rent_amount = float(rent)
                changed = True
            if end_raw:
                try:
                    new_end = date.fromisoformat(end_raw)
                    if new_end != existing_lease.end_date:
                        existing_lease.end_date = new_end
                        changed = True
                except ValueError:
                    pass
            if changed:
                db.flush()
                created.append("lease_updated")
            lease = existing_lease

    # --- Save extracted context to entities ---
    def _append_context(entity, new_text):
        if not new_text or not new_text.strip():
            return
        existing = entity.context or ""
        entity.context = (existing + "\n" + new_text.strip()).strip() if existing else new_text.strip()

    if prop and data.get("property_context"):
        _append_context(prop, data["property_context"])
    if unit and data.get("unit_context"):
        _append_context(unit, data["unit_context"])
    if tenant and data.get("tenant_context"):
        _append_context(tenant, data["tenant_context"])
    # Lease doesn't have a context field — store on tenant notes
    if tenant and data.get("lease_context"):
        existing_notes = tenant.notes or ""
        lease_ctx = data["lease_context"].strip()
        tenant.notes = (existing_notes + "\n" + lease_ctx).strip() if existing_notes else lease_ctx

    db.commit()

    return {
        "property_id": str(prop.id) if prop else None,
        "unit_id": str(unit.id) if unit else None,
        "tenant_id": str(tenant.id) if tenant else None,
        "lease_id": str(lease.id) if lease else None,
        "created": created,
    }
