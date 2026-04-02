# db/dsl_runner.py
"""
Property-Flow DSL interpreter.

Parses a YAML script, queries the declared scope resource, evaluates filters
and conditions per record, renders templates, and creates tasks.

Does NOT commit — callers are responsible for committing or rolling back.
"""

import logging
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from db.enums import TaskCategory, TaskSource, Urgency

import yaml
from sqlalchemy.orm import Session

from .models import (
    Task,
    Conversation,
    ConversationParticipant,
    ConversationType,
    Lease,
    Message,
    MessageType,
    ParticipantType,
    Property,
    Tenant,
    Unit,
)

logger = logging.getLogger("rentmate.dsl")

_OPEN_STATUSES = {"suggested", "active", "paused", "dismissed"}

# ─── computed fields ──────────────────────────────────────────────────────────

def _active_leases(unit: Unit) -> List[Lease]:
    today = date.today()
    return [l for l in unit.leases if l.end_date >= today]


def _computed(obj: Any, name: str) -> Any:
    """Return computed / virtual field values for DSL field references."""
    today = date.today()
    if name == "active_lease_count" and isinstance(obj, Unit):
        return len(_active_leases(obj))
    if name == "days_vacant" and isinstance(obj, Unit):
        if _active_leases(obj):
            return 0
        past = sorted(obj.leases, key=lambda l: l.end_date, reverse=True)
        return (today - past[0].end_date).days if past else 0
    if name == "days_until_end" and isinstance(obj, Lease):
        return (obj.end_date - today).days if obj.end_date else 0
    if name == "unit_count" and isinstance(obj, Property):
        return len(obj.units)
    if name == "last_lease" and isinstance(obj, Unit):
        past = sorted(obj.leases, key=lambda l: l.end_date, reverse=True)
        return past[0] if past else None
    return None


# ─── field resolution ─────────────────────────────────────────────────────────

def _get_field(obj: Any, path: str) -> Any:
    """
    Resolve a dot-path field from an ORM object, handling computed fields.
    e.g. "tenant.first_name", "unit.property.address_line1", "active_lease_count"
    """
    if obj is None or not path:
        return None

    dot = path.find(".")
    if dot == -1:
        name, rest = path, None
    else:
        name, rest = path[:dot], path[dot + 1:]

    # Try computed fields first
    computed = _computed(obj, name)
    if computed is not None or (name in ("active_lease_count", "days_vacant",
                                          "days_until_end", "unit_count", "last_lease")
                                 and isinstance(obj, (Unit, Lease, Property))):
        val = computed
    else:
        val = getattr(obj, name, None)

    return _get_field(val, rest) if rest else val


# ─── context ──────────────────────────────────────────────────────────────────

def _build_ctx(resource: str, record: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build the template/condition evaluation context for a single record.

    Top-level keys:
      - resource name (e.g. "unit")  → the ORM object, for dot-path templates
      - computed field shortcuts      → e.g. days_vacant, days_until_end
      - direct column values          → e.g. address_line1, label, payment_status
      - today                         → ISO date string
      - params                        → per-check param dict
    """
    today = date.today()
    ctx: Dict[str, Any] = {"today": today, "params": params}

    # Resource object (for {{unit.label}}, {{lease.tenant.first_name}}, etc.)
    ctx[resource] = record

    # Expose direct column values at top level (for shorthand like {{address_line1}})
    for k, v in record.__dict__.items():
        if not k.startswith("_"):
            ctx.setdefault(k, v)

    # Computed shortcuts
    if isinstance(record, Unit):
        active = _active_leases(record)
        past = sorted(record.leases, key=lambda l: l.end_date, reverse=True)
        ctx["active_lease_count"] = len(active)
        ctx["days_vacant"] = 0 if active else ((today - past[0].end_date).days if past else 0)
        ctx["last_lease"] = past[0] if past else None
    elif isinstance(record, Lease):
        ctx["days_until_end"] = (record.end_date - today).days if record.end_date else 0
    elif isinstance(record, Property):
        ctx["unit_count"] = len(record.units)

    return ctx


# ─── template rendering ───────────────────────────────────────────────────────

def _resolve_expr(ctx: Dict[str, Any], expr: str) -> Any:
    """Resolve a dot-path expression like 'unit.label' or 'params.warn_days'."""
    parts = expr.split(".")
    obj: Any = ctx
    for part in parts:
        if obj is None:
            return None
        if isinstance(obj, dict):
            obj = obj.get(part)
        else:
            obj = _get_field(obj, part)
    return obj


def _render(template: str, ctx: Dict[str, Any]) -> str:
    """Substitute {{expr}} placeholders in a template string."""
    def replace(m: re.Match) -> str:
        expr = m.group(1).strip()
        # today + N arithmetic: {{today + params.warn_days}} or {{today + 30}}
        add_m = re.match(r"today\s*\+\s*(.+)", expr)
        if add_m:
            raw = _resolve_expr(ctx, add_m.group(1).strip())
            try:
                result = ctx["today"] + timedelta(days=int(raw))
                return str(result)
            except (TypeError, ValueError):
                return ""
        val = _resolve_expr(ctx, expr)
        return str(val) if val is not None else ""

    return re.sub(r"\{\{(.+?)\}\}", replace, str(template))


# ─── operators ────────────────────────────────────────────────────────────────

def _apply_op(left: Any, operator: str, right: Any) -> bool:
    try:
        if operator == "equals":     return left == right
        if operator == "not_equals": return left != right
        if operator == "gt":         return left is not None and left > right
        if operator == "lt":         return left is not None and left < right
        if operator == "gte":        return left is not None and left >= right
        if operator == "lte":        return left is not None and left <= right
        if operator == "in":
            haystack = right if isinstance(right, list) else [right]
            return left in haystack
        if operator == "exists":
            if isinstance(left, list): return len(left) > 0
            return left is not None and left != ""
        if operator == "not_exists":
            if isinstance(left, list): return len(left) == 0
            return left is None or left == ""
        if operator == "contains":
            if isinstance(left, str):  return str(right) in left
            if isinstance(left, list): return right in left
    except TypeError:
        pass
    return False


# ─── value coercion ───────────────────────────────────────────────────────────

def _coerce(value: Any, ctx: Dict[str, Any]) -> Any:
    """Render template strings and coerce to int/float/date where possible."""
    if isinstance(value, list):
        return [_coerce(v, ctx) for v in value]
    if not isinstance(value, str):
        return value
    rendered = _render(value, ctx)
    try:
        return int(rendered)
    except (ValueError, TypeError):
        pass
    try:
        return datetime.strptime(rendered, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        pass
    return rendered


# ─── condition evaluation ─────────────────────────────────────────────────────

def _eval_cond(cond: Dict[str, Any], record: Any, ctx: Dict[str, Any]) -> bool:
    if "any_of" in cond:
        return any(_eval_cond(c, record, ctx) for c in cond["any_of"])

    field   = cond.get("field", "")
    op      = cond.get("operator", "exists")
    raw_val = cond.get("value")

    left  = _get_field(record, field) if field else None
    # also check ctx for computed shortcuts (active_lease_count, days_vacant, etc.)
    if left is None and field in ctx:
        left = ctx[field]

    right = _coerce(raw_val, ctx) if raw_val is not None else None
    return _apply_op(left, op, right)


def _eval_conditions(conditions: List[Any], record: Any, ctx: Dict[str, Any]) -> bool:
    return all(_eval_cond(c, record, ctx) for c in conditions)


# ─── scope filter ─────────────────────────────────────────────────────────────

def _eval_filter(record: Any, f: Dict[str, Any], ctx: Dict[str, Any]) -> bool:
    """
    Evaluate one scope filter.  Supports both standard and shorthand formats:

    Standard:   {field: end_date, operator: gte, value: "{{today}}"}
    Shorthand:  {exists: units}          — relation is non-empty
                {not_exists: units}      — relation is empty
    """
    # Shorthand: {exists: <attr>}
    for shorthand_op in ("exists", "not_exists"):
        if shorthand_op in f and "field" not in f:
            attr = f[shorthand_op]
            # Try exact name, then plural/singular variant
            val = getattr(record, attr, None)
            if val is None:
                val = getattr(record, attr + "s", None)      # unit → units
            if val is None:
                val = getattr(record, attr.rstrip("s"), None) # units → unit
            return _apply_op(val, shorthand_op, None)

    return _eval_cond(f, record, ctx)


# ─── urgency expression ───────────────────────────────────────────────────────

_COND_RE  = re.compile(r"(\w[\w.]*)\s*(<=|>=|<|>|==|!=)\s*(.+)")
_OP_MAP   = {"<=": "lte", ">=": "gte", "<": "lt", ">": "gt", "==": "equals", "!=": "not_equals"}
_LEVELS   = {u.value for u in Urgency}


def _eval_urgency(urgency: Any, ctx: Dict[str, Any]) -> str:
    """
    Evaluate an urgency expression.  Handles both:
      - Static value:         "high"
      - Conditional (literal block |):
            high   if days_until_end <= 30
            medium otherwise
      - Conditional (folded block > - newlines collapsed to spaces):
            "high if days_until_end <= 30 medium otherwise"
    """
    if not isinstance(urgency, str):
        return str(urgency)
    urgency = urgency.strip()
    if " if " not in urgency:
        return urgency  # static

    # Tokenise — works whether separated by newlines or spaces
    tokens = re.split(r"\s+", urgency)

    clauses: List[tuple] = []
    i = 0
    while i < len(tokens):
        word = tokens[i].lower()
        if word in _LEVELS:
            if i + 1 < len(tokens) and tokens[i + 1].lower() == "if":
                # Collect condition tokens until the next level keyword
                j = i + 2
                while j < len(tokens) and tokens[j].lower() not in _LEVELS:
                    j += 1
                condition = " ".join(tokens[i + 2 : j])
                clauses.append(("if", tokens[i], condition))
                i = j
            else:
                # Default clause: "low otherwise" or bare "low"
                clauses.append(("default", tokens[i], ""))
                i += 1
                if i < len(tokens) and tokens[i].lower() == "otherwise":
                    i += 1
        elif word == "otherwise":
            i += 1  # orphan keyword, skip
        else:
            i += 1

    for kind, level, condition in clauses:
        if kind == "default":
            return level
        cm = _COND_RE.match(condition.strip())
        if cm:
            field_expr, sym, val_str = cm.groups()
            left  = _resolve_expr(ctx, field_expr.strip())
            right = _coerce(val_str.strip(), ctx)
            if _apply_op(left, _OP_MAP[sym], right):
                return level

    return "medium"


# ─── llm gate ────────────────────────────────────────────────────────────────

def _last_resolved_date(db: Session, category: str,
                        property_id: Optional[str],
                        unit_id: Optional[str]) -> Optional[date]:
    """Find the most recent resolved_at date for a given category + property/unit."""
    q = (
        db.query(Task.resolved_at)
        .filter(
            Task.category == category,
            Task.task_status == "resolved",
            Task.resolved_at.isnot(None),
        )
    )
    if property_id:
        q = q.filter(Task.property_id == property_id)
    if unit_id:
        q = q.filter(Task.unit_id == unit_id)
    row = q.order_by(Task.resolved_at.desc()).first()
    if row and row[0]:
        return row[0].date() if isinstance(row[0], datetime) else row[0]
    return None


def _eval_llm_gate(gate: dict, ctx: Dict[str, Any], *,
                   db: Session, category: str,
                   property_id: Optional[str],
                   unit_id: Optional[str]) -> bool:
    """Ask the LLM whether an action should proceed.

    The gate dict must contain a ``prompt`` key (template string).
    Automatically appends task history context (last completed date).
    Returns True if the LLM answers yes, False otherwise.
    Falls back to True on any error so tasks are never silently dropped.
    """
    import os
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        logger.debug("llm_gate: no LLM_API_KEY, allowing by default")
        return True

    prompt = _render(gate.get("prompt", ""), ctx)
    if not prompt.strip():
        return True

    # Enrich with task history
    last_done = _last_resolved_date(db, category, property_id, unit_id)
    if last_done:
        days_ago = (date.today() - last_done).days
        prompt += (
            f"\nThis was last completed on {last_done.isoformat()} "
            f"({days_ago} day(s) ago)."
        )
    else:
        prompt += "\nThere is no record of this ever being completed at this property."

    try:
        import litellm

        resp = litellm.completion(
            model=os.getenv("LLM_MODEL", "openai/gpt-4o-mini"),
            api_key=api_key,
            api_base=os.getenv("LLM_BASE_URL") or None,
            messages=[
                {"role": "system", "content": (
                    "You are a property management assistant. "
                    "Answer the following yes/no question. "
                    "Respond with ONLY a JSON object: {\"answer\": true} or {\"answer\": false}."
                )},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            response_format={"type": "json_object"},
            max_tokens=20,
        )
        import json
        raw = resp.choices[0].message.content or "{}"
        result = json.loads(raw)
        answer = bool(result.get("answer", True))
        logger.info("llm_gate: %s -> %s", prompt[:120], answer)
        return answer
    except Exception as exc:
        logger.warning("llm_gate: LLM call failed (%s), allowing by default", exc)
        return True


# ─── task helpers ─────────────────────────────────────────────────────────────

def _task_exists(db: Session, subject: str,
                 property_id: Optional[str], unit_id: Optional[str]) -> bool:
    q = (
        db.query(Task)
        .filter(
            Task.source == TaskSource.AI_SUGGESTION,
            Task.task_status.in_(_OPEN_STATUSES),
            Task.title == subject,
        )
    )
    if property_id:
        q = q.filter(Task.property_id == property_id)
    if unit_id:
        q = q.filter(Task.unit_id == unit_id)
    return q.first() is not None


def _get_account_id(db: Session, property_id: Optional[str], unit_id: Optional[str]) -> str:
    from sqlalchemy import text
    try:
        if property_id:
            res = db.execute(text("SELECT account_id FROM properties WHERE id = :id"), {"id": property_id}).fetchone()
            if res and res[0]:
                return res[0]
        if unit_id:
            res = db.execute(text("SELECT account_id FROM units WHERE id = :id"), {"id": unit_id}).fetchone()
            if res and res[0]:
                return res[0]
        res = db.execute(text("SELECT id FROM hosted_accounts LIMIT 1")).fetchone()
        if res and res[0]:
            return res[0]
    except Exception:
        pass
    return "00000000-0000-0000-0000-000000000001"

def _do_create_task(db: Session, subject: str, body: str, category: str,
                    urgency: str, property_id: Optional[str],
                    unit_id: Optional[str],
                    tenant_name: Optional[str] = None,
                    property_address: Optional[str] = None,
                    require_vendor_type: Optional[str] = None,
                    preferred_vendor_id: Optional[str] = None) -> None:
    extra = {}
    if require_vendor_type:
        extra["require_vendor_type"] = require_vendor_type
    if preferred_vendor_id:
        from db.models import ExternalContact as _EC
        vendor = db.query(_EC).filter_by(id=preferred_vendor_id).first()
        if vendor:
            extra["assigned_vendor_id"] = preferred_vendor_id
            extra["assigned_vendor_name"] = vendor.name
    task = Task(
        id=str(uuid.uuid4()),
        account_id=_get_account_id(db, property_id, unit_id),
        title=subject,
        task_status="suggested",
        task_mode="waiting_approval",
        source=TaskSource.AI_SUGGESTION,
        category=category,
        urgency=urgency,
        priority="routine",
        confidential=False,
        property_id=property_id,
        unit_id=unit_id,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(task)
    db.flush()

    # Assign task_number per account
    from sqlalchemy import select as sa_select, func as sa_func
    max_num = db.execute(
        sa_select(sa_func.coalesce(sa_func.max(Task.task_number), 0))
        .where(Task.account_id == task.account_id)
    ).scalar()
    task.task_number = max_num + 1

    # Create the internal AI conversation thread
    ai_convo = Conversation(
        id=str(uuid.uuid4()),
        subject=subject,
        property_id=property_id,
        unit_id=unit_id,
        conversation_type=ConversationType.TASK_AI,
        is_group=False,
        is_archived=False,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(ai_convo)
    db.flush()
    task.ai_conversation_id = ai_convo.id

    # Create the external conversation thread (vendor if vendor required, else tenant)
    ext_type = ConversationType.VENDOR if require_vendor_type else ConversationType.TENANT
    ext_convo = Conversation(
        id=str(uuid.uuid4()),
        subject=subject,
        property_id=property_id,
        unit_id=unit_id,
        conversation_type=ext_type,
        is_group=False,
        is_archived=False,
        extra=extra or None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(ext_convo)
    db.flush()
    task.external_conversation_id = ext_convo.id

    # Add preferred vendor as a participant on the external conversation
    if preferred_vendor_id and require_vendor_type:
        db.add(ConversationParticipant(
            id=str(uuid.uuid4()),
            conversation_id=ext_convo.id,
            participant_type=ParticipantType.EXTERNAL_CONTACT,
            external_contact_id=preferred_vendor_id,
            is_active=True,
        ))
        db.flush()

    db.add(Message(
        id=str(uuid.uuid4()),
        conversation_id=ai_convo.id,
        sender_type=ParticipantType.ACCOUNT_USER,
        body=body,
        message_type=MessageType.CONTEXT,
        sender_name="RentMate",
        is_ai=True,
        sent_at=datetime.now(UTC),
    ))
    db.flush()

    # Generate a draft suggested action for waiting_approval tasks
    from llm.suggest import generate_task_suggestion
    draft = generate_task_suggestion(
        subject=subject,
        context_body=body,
        category=category,
        tenant_name=tenant_name,
        property_address=property_address,
    )
    if draft:
        db.add(Message(
            id=str(uuid.uuid4()),
            conversation_id=ai_convo.id,
            sender_type=ParticipantType.ACCOUNT_USER,
            body="Here's a suggested message you can send:",
            message_type=MessageType.SUGGESTION,
            sender_name="RentMate",
            is_ai=True,
            draft_reply=draft,
            sent_at=datetime.now(UTC),
        ))
        db.flush()


def _extract_ids(resource: str, record: Any) -> Tuple[Optional[str], Optional[str]]:
    if resource == "property":
        return record.id, None
    if resource == "unit":
        return getattr(record, "property_id", None), record.id
    if resource == "lease":
        return getattr(record, "property_id", None), getattr(record, "unit_id", None)
    if resource == "tenant":
        leases = getattr(record, "leases", [])
        prop_id = leases[0].property_id if leases else None
        return prop_id, None
    return None, None


# ─── resource query ───────────────────────────────────────────────────────────

_RESOURCE_MAP = {
    "property": Property,
    "unit":     Unit,
    "lease":    Lease,
    "tenant":   Tenant,
}


def _query_resource(db: Session, resource: str) -> List[Any]:
    model = _RESOURCE_MAP.get(resource)
    if not model:
        raise ValueError(f"Unknown scope resource: {resource!r}. "
                         f"Valid values: {list(_RESOURCE_MAP)}")
    return db.query(model).all()


# ─── public entry point ───────────────────────────────────────────────────────

def run_script(
    db: Session,
    script_yaml: str,
    params: Optional[Dict[str, Any]] = None,
    dry_run: bool = False,
) -> int:
    """
    Execute a Property-Flow YAML script against the database.

    Iterates over the declared scope resource, applies filters and conditions,
    renders templates, and calls _do_create_task for each matching record that
    doesn't already have an open task with the same subject.

    When dry_run=True, skips the deduplication check so simulation always
    shows the full set of tasks the script would generate.

    Returns the number of new tasks created.
    Does NOT commit — caller is responsible.
    """
    try:
        script = yaml.safe_load(script_yaml)
    except Exception as exc:
        logger.error("DSL parse error: %s", exc)
        return 0

    if not isinstance(script, dict):
        logger.error("DSL script must be a YAML mapping, got %s", type(script).__name__)
        return 0

    scope        = script.get("scope", {})
    resource     = scope.get("resource", "")
    scope_filters = scope.get("filters", []) or []
    conditions   = script.get("conditions", []) or []
    actions      = script.get("actions", []) or []
    params       = params or {}

    if not resource:
        logger.error("DSL script is missing scope.resource")
        return 0

    try:
        records = _query_resource(db, resource)
    except ValueError as exc:
        logger.error("DSL: %s", exc)
        return 0

    logger.info("DSL: resource=%r  records=%d  filters=%d  conditions=%d",
                resource, len(records), len(scope_filters), len(conditions))

    count = 0
    filtered_out = 0
    condition_out = 0
    deduped = 0

    for record in records:
        ctx = _build_ctx(resource, record, params)

        # Scope filters (all must pass)
        filter_results = []
        for f in scope_filters:
            result = _eval_filter(record, f, ctx)
            filter_results.append(result)
            if not result:
                break
        if not all(filter_results):
            filtered_out += 1
            logger.debug("DSL: record %r failed filter %r", getattr(record, "id", record), f)
            continue

        # Per-record conditions (all must pass)
        if not _eval_conditions(conditions, record, ctx):
            condition_out += 1
            continue

        # Execute actions
        for action in actions:
            if action.get("type") != "create_task":
                continue

            category            = action.get("category", "compliance")
            property_id, unit_id = _extract_ids(resource, record)

            # Optional LLM gate — skip action if the LLM says no
            llm_gate = action.get("llm_gate")
            if llm_gate and not dry_run and not _eval_llm_gate(
                llm_gate, ctx, db=db, category=category,
                property_id=property_id, unit_id=unit_id,
            ):
                logger.debug("DSL: llm_gate rejected action for record %r",
                             getattr(record, "id", record))
                continue

            subject             = _render(action.get("subject", "Untitled task"), ctx)
            body                = _render(action.get("body", ""), ctx)
            urgency             = _eval_urgency(action.get("urgency", "medium"), ctx)
            require_vendor_type = action.get("require_vendor_type") or None

            if not dry_run and _task_exists(db, subject, property_id, unit_id):
                deduped += 1
                logger.debug("DSL: task already exists, skipping: %r", subject)
                continue

            # Extract tenant name and property address from ORM record for suggestion generation
            t_name: Optional[str] = None
            p_addr: Optional[str] = None
            if resource == "lease":
                if getattr(record, "tenant", None):
                    t_name = f"{record.tenant.first_name} {record.tenant.last_name}".strip()
                if getattr(record, "property", None):
                    p_addr = record.property.address_line1
            elif resource == "unit":
                if getattr(record, "property", None):
                    p_addr = record.property.address_line1
            elif resource == "property":
                p_addr = getattr(record, "address_line1", None)
            elif resource == "tenant":
                t_name = f"{record.first_name} {record.last_name}".strip()

            preferred_vendor_id = (params or {}).get("preferred_vendor_id") or None
            _do_create_task(db, subject, body, category, urgency, property_id, unit_id,
                            tenant_name=t_name, property_address=p_addr,
                            require_vendor_type=require_vendor_type,
                            preferred_vendor_id=preferred_vendor_id)
            count += 1
            logger.debug("DSL created task: %r (urgency=%s)", subject, urgency)

    logger.info("DSL: created=%d  filtered_out=%d  condition_out=%d  deduped=%d",
                count, filtered_out, condition_out, deduped)
    if count:
        logger.info("DSL script created %d new task(s).", count)
    return count
