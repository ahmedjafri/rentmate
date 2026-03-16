import asyncio
import hashlib
import json
import logging
import os
from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from handlers.deps import SessionLocal, extract_json, get_db, require_user
from handlers.default_automations import _DEFAULT_AUTOMATION_CONFIG, _CHECK_META

router = APIRouter()
_logger = logging.getLogger("rentmate.audit")

# ─── in-memory run log (last 10 runs per check, reset on restart) ─────────────

_MAX_RUNS = 10
_run_log: Dict[str, deque] = {}


def _record_run(key: str, tasks_created: int, error: str | None = None) -> None:
    if key not in _run_log:
        _run_log[key] = deque(maxlen=_MAX_RUNS)
    _run_log[key].appendleft({
        "ran_at": datetime.utcnow().isoformat(),
        "tasks_created": tasks_created,
        "outcome": "error" if error else "ok",
        "error": error,
    })

# ─── config helpers ───────────────────────────────────────────────────────────

def _make_revision_id(cfg: Dict[str, Any]) -> str:
    from datetime import datetime as _dt
    raw = json.dumps(cfg, sort_keys=True) + _dt.utcnow().isoformat()
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _merge_automation_config(stored: Dict[str, Any]) -> Dict[str, Any]:
    """Deep-merge stored config with defaults (per-check level)."""
    stored_checks = stored.get("checks", {})
    merged_checks: Dict[str, Any] = {}
    for key, defaults in _DEFAULT_AUTOMATION_CONFIG["checks"].items():
        merged_checks[key] = {**defaults, **stored_checks.get(key, {})}
    for key, vals in stored_checks.items():
        if key not in merged_checks:
            merged_checks[key] = vals
    result: Dict[str, Any] = {"checks": merged_checks}
    if "custom_meta" in stored:
        result["custom_meta"] = stored["custom_meta"]
    return result


def _load_automation_config() -> Dict[str, Any]:
    """Return the latest revision's config, or defaults if no revisions exist."""
    from db.models import AutomationRevision
    db = SessionLocal.session_factory()
    try:
        row = db.query(AutomationRevision).order_by(AutomationRevision.created_at.desc()).first()
        if row:
            return _merge_automation_config(row.config)
    except Exception:
        pass  # table may not exist yet on first run before restart
    finally:
        db.close()
    return _merge_automation_config({})


def _save_automation_config(
    cfg: Dict[str, Any],
    message: str = "Update automation config",
    versioned: bool = True,
) -> str:
    """Persist a config. If versioned=False, update the latest revision in-place
    (no new history entry). Returns the revision id."""
    from db.models import AutomationRevision
    db = SessionLocal.session_factory()
    try:
        latest = db.query(AutomationRevision).order_by(AutomationRevision.created_at.desc()).first()
        if not versioned and latest:
            from sqlalchemy.orm.attributes import flag_modified
            latest.config = cfg
            flag_modified(latest, "config")
            db.commit()
            return latest.id
        parent_id = latest.id if latest else None
        rev = AutomationRevision(
            id=_make_revision_id(cfg),
            config=cfg,
            message=message,
            parent_id=parent_id,
        )
        db.add(rev)
        db.commit()
        return rev.id
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _get_automation_history() -> List[Dict[str, Any]]:
    from db.models import AutomationRevision
    db = SessionLocal.session_factory()
    try:
        rows = db.query(AutomationRevision).order_by(AutomationRevision.created_at.desc()).all()
        return [
            {"sha": r.id, "message": r.message,
             "date": r.created_at.isoformat(), "parent": r.parent_id}
            for r in rows
        ]
    finally:
        db.close()


def _revert_automation_config(sha: str) -> Dict[str, Any]:
    """Create a new revision with the content of an older one (non-destructive)."""
    from db.models import AutomationRevision
    db = SessionLocal.session_factory()
    try:
        target = db.query(AutomationRevision).filter_by(id=sha).one_or_none()
        if not target:
            raise ValueError(f"Revision {sha} not found")
        _save_automation_config(target.config, message=f"Revert to {sha[:8]}")
        return _merge_automation_config(target.config)
    finally:
        db.close()


def _build_automations_response() -> Dict[str, Any]:
    """Return the full automations list (metadata + current config) for API responses."""
    cfg = _load_automation_config()
    checks = cfg.get("checks", {})
    custom_meta = cfg.get("custom_meta", {})
    automations = []
    for key, meta in _CHECK_META.items():
        check_cfg = checks.get(key, _DEFAULT_AUTOMATION_CONFIG["checks"].get(key, {}))
        automations.append({"key": key, **meta, **check_cfg, "custom": False})
    for key, meta in custom_meta.items():
        check_cfg = checks.get(key, {"enabled": False, "interval_hours": 1})
        automations.append({
            "key": key, "has_params": False, "params": [],
            **meta, **check_cfg,
            "custom": True,
            "simulation_run": meta.get("simulation_run", False),
        })
    return {"automations": automations}


def _add_custom_automation(
    label: str, description: str, interval_hours: int, script: Optional[str] = None
) -> None:
    import re
    base_key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "automation"
    cfg = _load_automation_config()
    custom_meta = dict(cfg.get("custom_meta", {}))
    all_keys = set(_CHECK_META.keys()) | set(custom_meta.keys())
    key, n = base_key, 2
    while key in all_keys:
        key, n = f"{base_key}_{n}", n + 1
    meta: Dict[str, Any] = {
        "label": label,
        "description": description,
        "hint": "No configurable parameters — toggle on/off only.",
        "simulation_run": False,
    }
    if script:
        meta["script"] = script
    custom_meta[key] = meta
    checks = dict(cfg.get("checks", {}))
    checks[key] = {"enabled": False, "interval_hours": interval_hours}
    new_cfg = {**cfg, "checks": checks, "custom_meta": custom_meta}
    _save_automation_config(new_cfg, f"Add automation: {label}")


def _mark_simulated(key: str) -> None:
    """Mark a custom automation as having been simulated (unversioned update)."""
    cfg = _load_automation_config()
    custom_meta = dict(cfg.get("custom_meta", {}))
    if key not in custom_meta:
        return
    custom_meta[key] = {**custom_meta[key], "simulation_run": True}
    new_cfg = {**cfg, "custom_meta": custom_meta}
    _save_automation_config(new_cfg, versioned=False)


_NAMED_INTERVALS: Dict[str, int] = {
    "hourly":     1,
    "daily":      24,
    "weekly":     168,
    "bi-weekly":  336,
    "biweekly":   336,
    "monthly":    720,
}


def _resolve_interval_hours(schedule: dict) -> Optional[int]:
    """Return interval_hours from a DSL schedule block, accepting names or numbers."""
    named = schedule.get("interval")
    if named:
        resolved = _NAMED_INTERVALS.get(str(named).lower().strip())
        if resolved:
            return resolved
    raw = schedule.get("interval_hours")
    if raw and isinstance(raw, (int, float)) and raw > 0:
        return int(raw)
    return None


def _update_custom_script(key: str, script: str) -> None:
    """Update the Property-Flow script for a custom automation (versioned save).

    Syncs schedule.interval / schedule.interval_hours to checks[key].interval_hours
    so the scheduler picks it up without a separate save.
    """
    import yaml as _yaml
    cfg = _load_automation_config()
    custom_meta = dict(cfg.get("custom_meta", {}))
    if key not in custom_meta:
        raise ValueError(f"Custom automation '{key}' not found")
    custom_meta[key] = {**custom_meta[key], "script": script}
    checks = dict(cfg.get("checks", {}))
    try:
        parsed = _yaml.safe_load(script) or {}
        interval = _resolve_interval_hours(parsed.get("schedule", {}))
        if interval:
            checks[key] = {**checks.get(key, {}), "interval_hours": interval}
    except Exception:
        pass
    new_cfg = {**cfg, "checks": checks, "custom_meta": custom_meta}
    _save_automation_config(new_cfg, f"Update script for {custom_meta[key].get('label', key)}")


def _delete_custom_automation(key: str) -> None:
    """Remove a custom automation by key (versioned save)."""
    cfg = _load_automation_config()
    custom_meta = dict(cfg.get("custom_meta", {}))
    if key not in custom_meta:
        raise ValueError(f"Custom automation '{key}' not found")
    label = custom_meta[key].get("label", key)
    del custom_meta[key]
    checks = {k: v for k, v in cfg.get("checks", {}).items() if k != key}
    new_cfg = {**cfg, "checks": checks, "custom_meta": custom_meta}
    _save_automation_config(new_cfg, f"Delete automation: {label}")


def seed_automations() -> None:
    """Seed the default automation config if no revisions exist yet."""
    try:
        from db.models import AutomationRevision
        db = SessionLocal.session_factory()
        try:
            exists = db.query(AutomationRevision).first() is not None
        finally:
            db.close()
        if not exists:
            default_cfg = {"checks": {k: dict(v) for k, v in _DEFAULT_AUTOMATION_CONFIG["checks"].items()}}
            _save_automation_config(default_cfg, "Initialize automations")
            logging.getLogger("rentmate").info("Seeded default automation config")
    except Exception as e:
        logging.getLogger("rentmate").warning("Could not seed automations: %s", e)


async def audit_loop():
    """Background loop: run each enabled check on its own interval (polls every 60 s)."""
    from db.audit import run_data_audit
    from handlers.settings import get_autonomy_settings
    _POLL_SECONDS = 60
    last_run: Dict[str, float] = {}

    while True:
        cfg = {**_load_automation_config(), "autonomy": get_autonomy_settings()}
        checks = cfg.get("checks", {})
        now = asyncio.get_event_loop().time()

        for check_key, check_cfg in checks.items():
            if not check_cfg.get("enabled", True):
                continue
            interval_secs = int(check_cfg.get("interval_hours", 1)) * 3600
            if now < last_run.get(check_key, 0) + interval_secs:
                continue

            _logger.info("Running check: %s", check_key)
            db = SessionLocal.session_factory()
            try:
                n = run_data_audit(db, config=cfg, check_name=check_key)
                if n:
                    db.commit()
                _logger.info("Check %s complete — %d new task(s).", check_key, n)
                _record_run(check_key, n)
            except Exception as exc:
                db.rollback()
                _logger.exception("Error in check %s: %s", check_key, exc)
                _record_run(check_key, 0, error=str(exc))
            finally:
                db.close()

            last_run[check_key] = now

        await asyncio.sleep(_POLL_SECONDS)


# ─── pydantic models ──────────────────────────────────────────────────────────

class AutomationCheckBody(BaseModel):
    enabled: bool = True
    interval_hours: int = 1
    warn_days: Optional[int] = None
    min_vacancy_days: Optional[int] = None


class AutomationConfigBody(BaseModel):
    checks: Dict[str, AutomationCheckBody] = Field(default_factory=dict)
    message: Optional[str] = None
    versioned: bool = True


class InterpretNLRequest(BaseModel):
    check: str
    description: str


class NewAutomationBody(BaseModel):
    label: str
    description: str = ""
    interval_hours: int = 1
    script: Optional[str] = None


class GenerateScriptBody(BaseModel):
    label: str = ""
    description: str


class UpdateScriptBody(BaseModel):
    key: str
    script: str


class CreateSimulatedTaskBody(BaseModel):
    subject: str
    category: str
    urgency: str
    body: str = ""
    property_id: Optional[str] = None
    unit_id: Optional[str] = None


# ─── routes ───────────────────────────────────────────────────────────────────

@router.get("/automations")
async def get_automations(request: Request):
    await require_user(request)
    return _build_automations_response()


@router.post("/automations")
async def save_automations(body: AutomationConfigBody, request: Request):
    await require_user(request)
    # Load current config to preserve custom_meta and existing check state
    current_cfg = _load_automation_config()
    new_checks = {k: v.model_dump(exclude_none=True) for k, v in body.checks.items()}
    merged_checks = {**current_cfg.get("checks", {}), **new_checks}
    new_cfg: Dict[str, Any] = {"checks": merged_checks}
    if "custom_meta" in current_cfg:
        new_cfg["custom_meta"] = current_cfg["custom_meta"]
    _save_automation_config(new_cfg, message=body.message or "Update automation config", versioned=body.versioned)
    return _build_automations_response()


@router.get("/automations/history")
async def get_automation_history(request: Request):
    await require_user(request)
    return {"history": _get_automation_history()}


@router.get("/automations/runs")
async def get_automation_runs(request: Request):
    await require_user(request)
    return {"runs": {k: list(v) for k, v in _run_log.items()}}


@router.post("/automations/revert")
async def revert_automation(request: Request):
    await require_user(request)
    body = await request.json()
    sha = body.get("sha")
    if not sha:
        raise HTTPException(status_code=400, detail="sha required")
    try:
        _revert_automation_config(sha)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _build_automations_response()


@router.post("/automations/simulate")
async def simulate_automations(request: Request):
    await require_user(request)
    from db.audit import run_data_audit
    from db.models import Conversation

    check_name: Optional[str] = None
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            body = await request.json()
            check_name = body.get("check") or None
        except Exception:
            pass

    from handlers.settings import get_autonomy_settings
    cfg = {**_load_automation_config(), "autonomy": get_autonomy_settings()}
    _logger.info("simulate: check_name=%r  custom_meta_keys=%r",
                 check_name, list(cfg.get("custom_meta", {}).keys()))
    if check_name:
        meta = cfg.get("custom_meta", {}).get(check_name, {})
        script = meta.get("script")
        _logger.info("simulate: key=%r  has_script=%s  script_len=%s",
                     check_name, script is not None, len(script) if script else 0)

    # Use a dedicated session so we can close it fully before _mark_simulated
    # opens its own — SQLite allows only one writer at a time.
    db = SessionLocal.session_factory()
    preview = []
    try:
        existing_ids = {
            row.id for row in
            db.query(Conversation.id).filter(Conversation.is_task == True).all()
        }
        savepoint = db.begin_nested()
        try:
            run_data_audit(db, config=cfg, check_name=check_name, dry_run=True)
            # Query for tasks flushed within the savepoint (more reliable than identity_map)
            q = db.query(Conversation).filter(Conversation.is_task == True)
            if existing_ids:
                q = q.filter(Conversation.id.notin_(existing_ids))
            new_tasks = q.all()
            _logger.info("simulate: new_tasks=%d", len(new_tasks))
            for t in new_tasks:
                ctx = next((m for m in t.messages if m.message_type == "context"), None)
                preview.append({
                    "subject": t.subject,
                    "category": t.category,
                    "urgency": t.urgency,
                    "source": t.source,
                    "property_id": t.property_id,
                    "unit_id": t.unit_id,
                    "description": ctx.body if ctx else "",
                })
        finally:
            savepoint.rollback()
        db.commit()   # close the outer transaction cleanly (nothing to write)
    finally:
        db.close()    # release the connection before _mark_simulated acquires one

    # Mark custom automation as simulated (enables the toggle)
    if check_name and check_name in _load_automation_config().get("custom_meta", {}):
        _mark_simulated(check_name)

    return {"tasks": preview, "count": len(preview)}


@router.post("/automations/simulate/create-task")
async def create_simulated_task(body: CreateSimulatedTaskBody, request: Request):
    await require_user(request)
    from db.models import Conversation as Conv
    import uuid
    from datetime import datetime
    from db.models import Message, ParticipantType
    db = SessionLocal.session_factory()
    try:
        # Only block if an active/paused task already exists (not just a suggestion)
        exists = db.query(Conv).filter(
            Conv.is_task == True,          # noqa: E712
            Conv.source == "ai_suggestion",
            Conv.task_status.in_(["active", "paused"]),
            Conv.subject == body.subject,
            *([Conv.property_id == body.property_id] if body.property_id else []),
            *([Conv.unit_id == body.unit_id] if body.unit_id else []),
        ).first()
        if exists:
            raise HTTPException(status_code=409, detail="Task already exists in action desk")
        task = Conv(
            id=str(uuid.uuid4()),
            subject=body.subject,
            is_task=True,
            task_status="active",
            task_mode="manual",
            source="ai_suggestion",
            category=body.category,
            urgency=body.urgency,
            priority="routine",
            confidential=False,
            property_id=body.property_id,
            unit_id=body.unit_id,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(task)
        db.flush()
        db.add(Message(
            id=str(uuid.uuid4()),
            conversation_id=task.id,
            sender_type=ParticipantType.ACCOUNT_USER,
            body=body.body,
            message_type="context",
            sender_name="RentMate",
            is_ai=True,
            sent_at=datetime.utcnow(),
        ))
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        db.close()
    return {"ok": True}


@router.post("/automations/new")
async def add_automation(body: NewAutomationBody, request: Request):
    await require_user(request)
    if not body.label.strip():
        raise HTTPException(status_code=400, detail="label required")
    _add_custom_automation(body.label.strip(), body.description.strip(), body.interval_hours, script=body.script or None)
    return _build_automations_response()


_GENERATE_SCRIPT_SYSTEM = """\
You are a Property-Flow DSL expert for a property management app called RentMate.
Generate a valid Property-Flow YAML script based on the user's description.
Return ONLY valid YAML — no markdown fences, no explanation, no extra text.

═══════════════════════════════════════════════
FULL YAML SCHEMA
═══════════════════════════════════════════════

schedule:                        # required — how often this automation runs
  interval: <named>              # preferred: daily | weekly | bi-weekly | monthly | hourly
  # OR use a raw number instead:
  interval_hours: <number>       # e.g. 24 = daily, 168 = weekly, 720 = monthly

scope:
  resource: <property|unit|lease|tenant>   # required — what to iterate over
  filters:                                 # optional — pre-filter records
    - field: <dot.path>
      operator: <op>
      value: <value>
    - exists: <attr>        # shorthand: relation/field must be non-null/non-empty
    - not_exists: <attr>    # shorthand: relation/field must be null/empty

conditions:                 # optional — per-record in-memory checks (all must pass)
  - field: <dot.path>
    operator: <op>
    value: <value>
  - any_of:                 # OR group
    - field: <dot.path>
      operator: <op>
      value: <value>

actions:
  - type: create_task
    subject: "Template string with {{variable}}"
    category: <rent|leasing|compliance|maintenance>
    urgency: <low|medium|high|critical>
    # OR conditional urgency (literal block scalar):
    urgency: |
      high if <field> <op> <value>
      medium if <field> <op> <value>
      low otherwise
    body: "Longer description with {{variable}} placeholders."

═══════════════════════════════════════════════
RESOURCES & FIELDS
═══════════════════════════════════════════════

property:
  id, name, address_line1, city, state, postal_code
  computed: unit_count

unit:
  id, label, property_id
  computed: active_lease_count, days_vacant, last_lease
  relations: property, leases

lease:
  id, start_date, end_date, rent_amount, payment_status, property_id, unit_id
  payment_status values: "current", "late", "overdue"
  computed: days_until_end
  relations: tenant, unit, property

tenant:
  id, first_name, last_name, email, phone
  relations: leases

Dot-path relation traversal:
  lease.tenant.first_name
  lease.unit.label
  unit.property.address_line1
  unit.last_lease.end_date
  unit.last_lease.rent_amount

Template syntax: {{expr}} — e.g. {{unit.label}}, {{days_vacant}}, {{today + 30}}

═══════════════════════════════════════════════
OPERATORS
═══════════════════════════════════════════════
equals, not_equals, gt, lt, gte, lte, in, exists, not_exists, contains

Urgency conditional operators (inline): <=, >=, <, >, ==, !=

═══════════════════════════════════════════════
COMPLETE EXAMPLE — vacant units alert
═══════════════════════════════════════════════

schedule:
  interval: daily

scope:
  resource: unit
  filters:
    - field: active_lease_count
      operator: equals
      value: 0

conditions:
  - field: days_vacant
    operator: gt
    value: 7

actions:
  - type: create_task
    subject: "Vacant unit: {{unit.label}} at {{unit.property.address_line1}}"
    category: leasing
    urgency: |
      high if days_vacant > 60
      medium if days_vacant > 14
      low otherwise
    body: >
      Unit {{unit.label}} at {{unit.property.address_line1}} has been vacant
      for {{days_vacant}} days. List the unit and follow up with prospects.

═══════════════════════════════════════════════
COMPLETE EXAMPLE — overdue rent
═══════════════════════════════════════════════

schedule:
  interval: daily

scope:
  resource: lease
  filters:
    - field: payment_status
      operator: in
      value: [late, overdue]

actions:
  - type: create_task
    subject: "Overdue rent: {{lease.tenant.first_name}} {{lease.tenant.last_name}}"
    category: rent
    urgency: high
    body: >
      {{lease.tenant.first_name}} {{lease.tenant.last_name}} at
      {{lease.unit.label}} has payment status '{{lease.payment_status}}'.
      Rent is ${{lease.rent_amount}}/month. Follow up immediately.

═══════════════════════════════════════════════
COMPLETE EXAMPLE — tenants missing contact info
═══════════════════════════════════════════════

schedule:
  interval: weekly

scope:
  resource: tenant

conditions:
  - any_of:
    - field: phone
      operator: not_exists
    - field: email
      operator: not_exists

actions:
  - type: create_task
    subject: "Missing contact info: {{tenant.first_name}} {{tenant.last_name}}"
    category: compliance
    urgency: low
    body: >
      Tenant {{tenant.first_name}} {{tenant.last_name}} is missing
      contact information. Add a phone number or email address.
\
"""


_GENERATE_SCRIPT_USER = """\
Before writing the YAML, wrap your reasoning in <thinking>...</thinking> tags.
Think through: what resource to iterate over, what filters/conditions apply, and how to phrase the task subject and body.
After the thinking block, output ONLY the raw YAML with no markdown fences.

Automation name: {label}
Description: {description}\
"""


@router.post("/automations/generate-script")
async def generate_script(body: GenerateScriptBody, request: Request):
    import litellm
    import json as _json
    import re as _re
    from fastapi.responses import StreamingResponse as _SR
    await require_user(request)

    kwargs: Dict[str, Any] = dict(
        model=os.getenv("LLM_MODEL", "openai/gpt-4o-mini"),
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_BASE_URL") or None,
        messages=[
            {"role": "system", "content": _GENERATE_SCRIPT_SYSTEM},
            {"role": "user", "content": _GENERATE_SCRIPT_USER.format(
                label=body.label, description=body.description
            )},
        ],
        temperature=0.7,
        stream=True,
    )

    async def _stream():
        raw_buf: list[str] = []
        has_native_reasoning = False
        try:
            async for chunk in await litellm.acompletion(**kwargs):
                delta = chunk.choices[0].delta
                # Native reasoning_content (DeepSeek R1, o1/o3, etc.)
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    has_native_reasoning = True
                    yield f"data: {_json.dumps({'type': 'thinking', 'text': reasoning})}\n\n"
                content = getattr(delta, "content", None) or ""
                if content:
                    raw_buf.append(content)

            raw = "".join(raw_buf).strip()

            # Fallback: extract <thinking>…</thinking> from content for models
            # that don't expose reasoning_content but follow text instructions
            if not has_native_reasoning:
                thinking_match = _re.search(r"<thinking>(.*?)</thinking>", raw, _re.DOTALL)
                if thinking_match:
                    for line in thinking_match.group(1).strip().splitlines():
                        if line.strip():
                            yield f"data: {_json.dumps({'type': 'thinking', 'text': line})}\n\n"
                    raw = raw[thinking_match.end():].strip()

            # Strip residual markdown fences
            if raw.startswith("```"):
                lines = raw.split("\n")
                end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
                raw = "\n".join(lines[1:end]).strip()

            yield f"data: {_json.dumps({'type': 'done', 'script': raw})}\n\n"
        except Exception as exc:
            yield f"data: {_json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return _SR(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/automations/update-script")
async def update_script(body: UpdateScriptBody, request: Request):
    await require_user(request)
    if not body.key or not body.script:
        raise HTTPException(status_code=400, detail="key and script required")
    try:
        _update_custom_script(body.key, body.script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _build_automations_response()


class ValidateScriptBody(BaseModel):
    script: str


@router.post("/automations/validate")
async def validate_script(body: ValidateScriptBody, request: Request):
    await require_user(request)
    import yaml
    _VALID_RESOURCES = {"property", "unit", "lease", "tenant"}
    _VALID_CATEGORIES = {"rent", "leasing", "compliance", "maintenance"}
    errors: list[str] = []
    try:
        parsed = yaml.safe_load(body.script)
    except yaml.YAMLError as e:
        return {"valid": False, "errors": [f"YAML parse error: {e}"]}
    if not isinstance(parsed, dict):
        return {"valid": False, "errors": ["Script must be a YAML mapping."]}
    scope = parsed.get("scope")
    if not scope or not isinstance(scope, dict):
        errors.append("Missing required key: scope")
    else:
        resource = scope.get("resource")
        if not resource:
            errors.append("Missing scope.resource")
        elif resource not in _VALID_RESOURCES:
            errors.append(f"Invalid scope.resource '{resource}'. Must be one of: {', '.join(sorted(_VALID_RESOURCES))}")
    actions = parsed.get("actions")
    if not actions or not isinstance(actions, list) or len(actions) == 0:
        errors.append("Missing required key: actions (must be a non-empty list)")
    else:
        for i, action in enumerate(actions):
            if not isinstance(action, dict):
                errors.append(f"actions[{i}] must be a mapping")
                continue
            if action.get("type") != "create_task":
                errors.append(f"actions[{i}].type must be 'create_task'")
            if not action.get("subject"):
                errors.append(f"actions[{i}].subject is required")
            cat = action.get("category")
            if not cat:
                errors.append(f"actions[{i}].category is required")
            elif cat not in _VALID_CATEGORIES:
                errors.append(f"actions[{i}].category '{cat}' must be one of: {', '.join(sorted(_VALID_CATEGORIES))}")
    return {"valid": len(errors) == 0, "errors": errors}


@router.delete("/automations/{key}")
async def delete_automation(key: str, request: Request):
    await require_user(request)
    if key in _CHECK_META:
        raise HTTPException(status_code=400, detail="Built-in automations cannot be deleted.")
    try:
        _delete_custom_automation(key)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _build_automations_response()


@router.post("/automations/interpret")
async def interpret_automation_nl(body: InterpretNLRequest, request: Request):
    import litellm
    await require_user(request)

    meta = _CHECK_META.get(body.check, {"label": body.check, "params": []})
    param_docs = ", ".join(f'"{p}": integer' for p in meta["params"]) or "none"

    system_prompt = (
        f"You configure the '{meta['label']}' automation check in a property management app.\n"
        f"Configurable parameters: {{{param_docs}}}.\n"
        "Parse the user's natural-language description and return ONLY a JSON object with the "
        "parameter(s) they mentioned. Include 'enabled' (boolean) only if the user explicitly "
        "enables or disables the check. Do not include parameters not mentioned. "
        "Return valid JSON only, no explanation."
    )

    try:
        resp = await litellm.acompletion(
            model=os.getenv("LLM_MODEL", "openai/gpt-4o-mini"),
            api_key=os.getenv("LLM_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL") or None,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": body.description},
            ],
            temperature=0,
        )
        raw = resp.choices[0].message.content or "{}"
        parsed = extract_json(raw)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI unavailable: {e}")

    return {"check": body.check, "params": parsed}
