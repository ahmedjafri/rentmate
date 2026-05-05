"""RentMate agent client.

When ``RENTMATE_AGENT_URL`` is set, agent calls go to the hosted service.
Otherwise, falls back to the local agent.

``chat_with_agent`` is the core LLM execution function — it initializes
the AI agent, runs a conversation, and bridges progress events.
"""
import asyncio
import contextvars
import json
import os
import queue
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from agent.loop import AgentLoop
from agent.model_config import resolve_model_config
from agent.registry import agent_registry
from agent.rentmate_policy_provider import RentmatePolicyProvider
from agent.runs import accumulate_run_totals, derive_run_metadata, start_run
from agent.tools import current_request_context, current_user_message
from agent.tracing import log_trace, make_trace_envelope
from integrations.local_auth import (
    reset_fallback_request_context,
    resolve_account_id,
    resolve_org_id,
    set_fallback_request_context,
)

AGENT_URL = os.getenv("RENTMATE_AGENT_URL")  # e.g. https://agent.rentmate.com

current_trace_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "current_trace_context",
    default=None,
)
current_failed_tools: contextvars.ContextVar[list[dict[str, Any]]] = contextvars.ContextVar(
    "current_failed_tools",
    default=[],
)
current_completed_tools: contextvars.ContextVar[list[str]] = contextvars.ContextVar(
    "current_completed_tools",
    default=[],
)
_last_eval_debug_payload: dict[str, Any] | None = None


def _set_last_eval_debug_payload(payload: dict[str, Any] | None) -> None:
    global _last_eval_debug_payload
    _last_eval_debug_payload = payload


def get_last_eval_debug_payload() -> dict[str, Any] | None:
    return _last_eval_debug_payload


def _augment_system_message_with_policies(
    base: str,
    *,
    user_message: str,
    session_id: str = "",
) -> str:
    """Inline RentMate policy text into the system prompt.

    The constitution block always applies; ``prefetch(user_message)`` adds
    keyword-matched policy excerpts for the current ask.
    """
    provider = RentmatePolicyProvider()
    provider.initialize(session_id=session_id)
    parts: list[str] = []
    if base:
        parts.append(base)
    constitution = provider.system_prompt_block().strip()
    if constitution:
        parts.append(constitution)
    dynamic = provider.prefetch(user_message or "").strip()
    if dynamic:
        parts.append(dynamic)
    return "\n\n---\n\n".join(parts)


_ONBOARDING_PROMPT_PATH = Path(__file__).parent / "policies" / "onboarding.md"


def _read_onboarding_prompt() -> str:
    return _ONBOARDING_PROMPT_PATH.read_text().strip()


def _load_onboarding_prompt(*, session_key: str) -> str:
    if not str(session_key).startswith("chat:"):
        return ""
    try:
        from db.models import Property
        from db.session import SessionLocal
        from services.settings_service import get_onboarding_state

        db = SessionLocal()
        try:
            state = get_onboarding_state(db)
            # An account with no properties is still in onboarding even if
            # init_onboarding hasn't been called yet (e.g. the user uploaded a
            # lease before the frontend hit /onboarding/state).
            implicit_active = (state is None) and (db.query(Property).count() == 0)
        finally:
            db.close()
    except Exception:
        return ""
    if not implicit_active and (not state or state.get("status") != "active"):
        return ""
    try:
        return _read_onboarding_prompt()
    except Exception:
        return ""


@dataclass
class AgentResponse:
    reply: str
    side_effects: list[dict] = field(default_factory=list)


# ─── Tool labels for progress display ────────────────────────────────────────

_TOOL_LABELS = {
    "lookup_vendors": "Searching vendors",
    "propose_task": "Proposing task",
    "close_task": "Closing task",
    "message_person": "Sending message",
    "create_vendor": "Creating vendor",
    "remember_about_entity": "Saving note",
    "add_task_note": "Adding task note",
    "recall_memory": "Checking memory",
    "edit_memory": "Editing memory",
    "create_property": "Creating property",
    "create_tenant": "Creating tenant",
    "create_suggestion": "Creating suggestion",
    "create_routine": "Creating routine",
    "create_document": "Creating document",
    "read_document": "Reading document",
    "analyze_document": "Analyzing document",
    "update_onboarding": "Updating setup progress",
}


_DOCUMENT_CLAIM_PATTERNS = [
    re.compile(pattern, re.I)
    for pattern in [
        r"\bcreated document\b",
        r"\bi(?:'ve| have) created .*document\b",
        r"\bavailable in your documents area\b",
        r"\bdownload (?:it|the pdf)\b",
    ]
]

_TENANT_ACCESS_REPLY_PATTERNS = [
    re.compile(pattern, re.I)
    for pattern in [
        r"\bcheck (?:with )?(?:the )?tenant\b",
        r"\bconfirm (?:access|availability) with (?:the )?tenant\b",
        r"\bneed to (?:check|confirm).{0,80}\btenant\b",
        r"\bneed to (?:check|confirm).{0,80}\baccess\b",
        r"\bif (?:that|the proposed|this) time works\b",
        r"\bwhether .* access\b",
    ]
]

_VENDOR_APPOINTMENT_WINDOW_PATTERNS = [
    re.compile(pattern, re.I | re.S)
    for pattern in [
        r"External conversation:.*\[[^\]]+\]:.*\b(?:i can come|i have|available|open|works on my end|works for me)\b.{0,120}\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|next week|\d{1,2}(?::\d{2})?\s*(?:am|pm))\b",
        r"Vendor conversation:.*\[[^\]]+\]:.*\b(?:i can come|i have|available|open|works on my end|works for me)\b.{0,120}\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|next week|\d{1,2}(?::\d{2})?\s*(?:am|pm))\b",
    ]
]

_MUTATING_TOOLS = {
    "propose_task",
    "close_task",
    "message_person",
    "create_vendor",
    "create_property",
    "create_tenant",
    "create_suggestion",
    "create_routine",
    "create_document",
}


def _collect_pending_side_effects(*, pending_items: list[dict] | None) -> list[dict]:
    side_effects: list[dict] = []
    for pending in (pending_items or []):
        side_effects.append({
            "type": pending.get("type", "suggestion_message"),
            **pending,
        })
    return side_effects


def _reply_claims_document_created(reply: str) -> bool:
    return any(pattern.search(reply or "") for pattern in _DOCUMENT_CLAIM_PATTERNS)


def _has_document_side_effect(side_effects: list[dict]) -> bool:
    for effect in side_effects:
        action_card = ((effect.get("meta") or {}).get("action_card") or {}) if isinstance(effect, dict) else {}
        if action_card.get("kind") == "document":
            return True
    return False


def _has_tenant_message_side_effect(side_effects: list[dict]) -> bool:
    for effect in side_effects:
        if not isinstance(effect, dict):
            continue
        payload = effect.get("action_payload") or {}
        if (
            payload.get("action") == "message_person"
            and payload.get("entity_type") == "tenant"
        ):
            return True
    return False


def _reply_needs_tenant_access_message(
    reply: str,
    *,
    latest_user_message: str,
    side_effects: list[dict],
) -> bool:
    if _has_tenant_message_side_effect(side_effects):
        return False
    has_vendor_window = any(
        pattern.search(latest_user_message or "")
        for pattern in _VENDOR_APPOINTMENT_WINDOW_PATTERNS
    )
    if not has_vendor_window:
        return False
    if reply and any(pattern.search(reply) for pattern in _TENANT_ACCESS_REPLY_PATTERNS):
        return True
    return True


def _summarize_non_document_side_effects(side_effects: list[dict]) -> str | None:
    if not side_effects:
        return None
    counts: dict[str, int] = {}
    for effect in side_effects:
        if not isinstance(effect, dict):
            continue
        action_card = ((effect.get("meta") or {}).get("action_card") or {})
        kind = action_card.get("kind")
        if not kind or kind == "document":
            continue
        counts[kind] = counts.get(kind, 0) + 1
    if not counts:
        return None
    if counts == {"property": 1}:
        return "I created the property record from the lease."
    if counts == {"tenant": 1}:
        return "I created the tenant record."
    if counts == {"property": 1, "tenant": 1}:
        return "I created the property and tenant records from the lease."
    parts = []
    for kind in ("property", "unit", "tenant", "suggestion"):
        count = counts.get(kind)
        if not count:
            continue
        noun = kind if count == 1 else f"{kind}s"
        parts.append(f"{count} {noun}")
    if not parts:
        return None
    return f"I completed the requested updates: {', '.join(parts)}."


def _failed_mutating_tool_switched(
    *,
    failed_tools: list[dict[str, Any]],
    completed_tools: list[str],
) -> tuple[str, str] | None:
    failed_mutating = [
        tool.get("tool_name")
        for tool in failed_tools
        if tool.get("tool_name") in _MUTATING_TOOLS
    ]
    completed_mutating = [tool for tool in completed_tools if tool in _MUTATING_TOOLS]
    for failed_tool in failed_mutating:
        for completed_tool in completed_mutating:
            if completed_tool != failed_tool:
                return failed_tool, completed_tool
    return None


def _synthesize_failed_tool_reply(failed_tools: list[dict[str, Any]]) -> str | None:
    if not failed_tools:
        return None
    failed_tool = failed_tools[-1]
    tool_name = str(failed_tool.get("tool_name") or "tool")
    label = _TOOL_LABELS.get(tool_name, tool_name.replace("_", " "))
    raw_error = str(failed_tool.get("error") or "").strip()
    if raw_error:
        if len(raw_error) > 500:
            raw_error = raw_error[:500] + "…"
        return f"{label} failed: {raw_error}"
    return f"{label} failed."


def _reply_is_only_tool_progress(reply: str) -> bool:
    lines = [line.strip() for line in (reply or "").splitlines() if line.strip()]
    if not lines:
        return True
    for line in lines:
        if line == "Thinking…":
            continue
        if line.startswith("Thinking ("):
            continue
        if any(
            line == label or line.startswith(f"{label}:")
            for label in _TOOL_LABELS.values()
        ):
            continue
        return False
    return True


_GENERIC_REPLY_PATTERNS = [
    re.compile(pattern, re.I)
    for pattern in [
        r"^i(?:'ve| have) answered\b",
        r"^i(?:'ve| have) responded\b",
        r"^i(?:'ve| have) closed the task\b",
        r"^i(?:'ve| have) answered .* and closed the task\b",
        r"^i(?:'ve| have) handled\b",
        r"^done\b",
    ]
]

_NARROW_REQUEST_PATTERNS = [
    re.compile(pattern, re.I)
    for pattern in [
        r"^i don't see .* in the system\b",
        r"^to coordinate .* i'll need\b",
        r"^i need .* contact information\b",
        r"^do you want me to create .* vendor entry\b",
    ]
]

_STAGE_SETTING_PATTERNS = [
    re.compile(pattern, re.I)
    for pattern in [
        r"\bthe next phase is\b",
        r"\brequired first step\b",
        r"\bmust be served before\b",
        r"\bwait the\b",
        r"\bcourt filing\b",
        r"\bunlawful detainer\b",
    ]
]

_EXCLUDED_SUBSET_PATTERNS = [
    re.compile(pattern, re.I)
    for pattern in [
        r"\bexcluded as requested\b",
        r"\bwas excluded\b",
        r"\bwere excluded\b",
        r"\bnon-matching\b",
        r"\bskipped the\b",
    ]
]

_EMPATHY_PATTERNS = [
    re.compile(pattern, re.I)
    for pattern in [
        r"\bi understand\b",
        r"\bi'm sorry\b",
        r"\bi am sorry\b",
        r"\bi(?:'m| am) deeply concerned\b",
        r"\bconcerned\b",
        r"\breach out\b",
        r"\bsupport\b",
        r"\bcare\b",
        r"\bfrustration\b",
        r"\btoo long\b",
        r"\bthat sounds\b",
    ]
]

_PLANNING_REPLY_PATTERNS = [
    re.compile(pattern, re.I)
    for pattern in [
        r"\bnow i need to\b",
        r"\blet me create\b",
        r"\bfrom the list provided\b",
        r"\bthe user requested\b",
        r"\bfirst, let me\b",
    ]
]


def _reply_is_generic_summary(reply: str) -> bool:
    text = (reply or "").strip()
    if not text:
        return True
    return any(pattern.search(text) for pattern in _GENERIC_REPLY_PATTERNS) or _looks_like_planning_reply(text)


def _reply_is_narrow_information_request(reply: str) -> bool:
    text = (reply or "").strip()
    if not text:
        return False
    return any(pattern.search(text) for pattern in _NARROW_REQUEST_PATTERNS)


def _has_stage_setting(content: str) -> bool:
    text = (content or "").strip()
    return any(pattern.search(text) for pattern in _STAGE_SETTING_PATTERNS)


def _contains_excluded_subset_language(content: str) -> bool:
    text = (content or "").strip()
    return any(pattern.search(text) for pattern in _EXCLUDED_SUBSET_PATTERNS)


def _contains_empathy(content: str) -> bool:
    text = (content or "").strip()
    return any(pattern.search(text) for pattern in _EMPATHY_PATTERNS)


def _looks_like_planning_reply(content: str) -> bool:
    text = (content or "").strip()
    return any(pattern.search(text) for pattern in _PLANNING_REPLY_PATTERNS)


def _sanitize_filtered_subset_reply(reply: str) -> str:
    text = (reply or "").strip()
    if not text:
        return text
    sentences = re.split(r"(?<=[.!?])\s+", text)
    kept = [sentence for sentence in sentences if not _contains_excluded_subset_language(sentence)]
    sanitized = " ".join(part.strip() for part in kept if part.strip()).strip()
    sanitized = sanitized or text
    replacements = [
        (r"\bcheck your lease\b", "confirm the lease policy details"),
        (r"\bchecks? your lease\b", "confirms the lease policy details"),
        (r"\bproperty manager to check your lease\b", "property manager to confirm the lease policy details"),
        (r"\bmanager to check your lease\b", "manager to confirm the lease policy details"),
        (r"\brefer to your lease\b", "review the lease policy details with the property manager"),
        (r"\blook at your lease\b", "review the lease policy details with the property manager"),
        (r"\breview your documents\b", "confirm the policy details"),
    ]
    for pattern, replacement in replacements:
        sanitized = re.sub(pattern, replacement, sanitized, flags=re.I)
    move_out_markers = (
        "move-out",
        "move out",
        "30-day notice",
        "30 day notice",
        "notice to move",
    )
    next_step_markers = (
        "inspection",
        "walkthrough",
        "key",
        "keys",
        "clean",
        "deposit",
        "security deposit",
    )
    if any(marker in sanitized.lower() for marker in move_out_markers) and not any(
        marker in sanitized.lower() for marker in next_step_markers
    ):
        sanitized = (
            f"{sanitized} The usual next steps are key return, cleaning the unit, "
            "a final walkthrough, and security deposit processing."
        ).strip()
    return sanitized


def _select_best_reply(result: dict[str, Any], reply: str) -> str:
    def _finalize(text: str) -> str:
        return _sanitize_filtered_subset_reply(text)

    messages = result.get("messages", []) if isinstance(result, dict) else []
    candidates: list[str] = []
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        if content == (reply or "").strip():
            continue
        candidates.append(content)
    if not candidates:
        return _finalize(reply)
    best_candidate = candidates[0]
    if _reply_is_only_tool_progress(reply) or _reply_is_generic_summary(reply):
        return _finalize(best_candidate)
    if _reply_is_narrow_information_request(reply):
        for candidate in candidates:
            if _has_stage_setting(candidate) and not _has_stage_setting(reply):
                return _finalize(candidate)
    if _contains_excluded_subset_language(reply):
        for candidate in candidates:
            if not _contains_excluded_subset_language(candidate) and not _looks_like_planning_reply(candidate):
                return _finalize(candidate)
    if not _contains_empathy(reply):
        for candidate in candidates:
            if _contains_empathy(candidate):
                return _finalize(candidate)
    if len(best_candidate) > max(len(reply or ""), 1) * 1.4:
        return _finalize(best_candidate)
    return _finalize(reply)


# ─── Local agent execution ───────────────────────────────────────────────────


async def chat_with_agent(
    agent_id: str,
    session_key: str,
    messages: list[dict],
    on_progress: Optional[Callable] = None,
    trace_context: dict[str, Any] | None = None,
) -> str:
    """Run the AI agent with the given messages and return its text reply."""
    model = os.getenv("LLM_MODEL", "anthropic/claude-haiku-4-5-20251001")
    api_key = os.getenv("LLM_API_KEY", "")
    explicit_base = os.getenv("LLM_BASE_URL") or None
    resolved_model = resolve_model_config(model=model, api_base=explicit_base)
    # Use the litellm-prefixed name so litellm.acompletion can route correctly.
    actual_model = resolved_model.litellm_model
    # Only override LiteLLM's provider-default base URL if the user explicitly
    # set LLM_BASE_URL. For known-provider prefixes (anthropic/, together_ai/,
    # etc.) LiteLLM picks the right base — passing the resolver's fallback
    # would mis-route those requests.
    api_base = explicit_base
    provider = resolved_model.provider

    conversation_history = [
        {"role": m["role"], "content": m["content"]}
        for m in messages
        if m.get("role") in ("user", "assistant")
        # Filter out poisoned responses that contain simulated tool calls
        and "[True]" not in (m.get("content") or "")
    ]

    user_message = ""
    if conversation_history and conversation_history[-1]["role"] == "user":
        user_message = conversation_history.pop()["content"]
    user_message_token = current_user_message.set(user_message)
    request_context_token = current_request_context.set(trace_context)

    # Extract system message and conversation history. Pass user_message
    # into the bundle builder so persistent-memory retrieval is biased
    # toward the current ask rather than a static account-overview query.
    prompt_bundle = agent_registry.build_system_prompt_bundle(agent_id, query=user_message)
    system_message = str(prompt_bundle.get("system_prompt") or "")
    onboarding_prompt = _load_onboarding_prompt(session_key=session_key)
    if onboarding_prompt:
        system_message = f"{system_message}\n\n---\n\n{onboarding_prompt}" if system_message else onboarding_prompt
    sys_content = next((m["content"] for m in messages if m.get("role") == "system"), None)
    if sys_content:
        system_message = f"{system_message}\n\n---\n\n{sys_content}"

    # Queue for bridging progress from the sync agent thread to async SSE
    progress_queue: queue.Queue[str] = queue.Queue()
    progress_events: list[str] = []

    # Extract task_id from session_key for tracing (e.g. "task:abc-123")
    _trace_task_id = session_key.split(":", 1)[1] if session_key.startswith("task:") else None
    _trace_source = "assess" if session_key.startswith("eval:") else ("chat" if not _trace_task_id else "chat")
    _trace_conversation_id = str((trace_context or {}).get("conversation_id") or "") or None

    def _tool_progress(event_type: str, tool_name: str, preview: str | None, args: dict | None, **kwargs):
        from agent.trajectory import current_step_builder
        label = _TOOL_LABELS.get(tool_name, tool_name)
        trace_detail = current_trace_context.get() or trace_context or {}
        if event_type == "tool.started":
            hint = ""
            if args:
                if tool_name == "lookup_vendors" and args.get("vendor_type"):
                    hint = f" ({args['vendor_type']})"
                elif tool_name == "propose_task" and args.get("title"):
                    hint = f": {args['title'][:60]}"
                elif tool_name == "remember_about_entity":
                    et = args.get("entity_type", "")
                    nk = args.get("note_kind", "")
                    if et and nk:
                        hint = f" → {et} ({nk})"
                    elif et:
                        hint = f" → {et}"
                elif tool_name == "add_task_note":
                    note = args.get("note", "")
                    if note:
                        hint = f": {note[:60]}"
                elif tool_name == "recall_memory":
                    et = args.get("entity_type")
                    if et:
                        hint = f" ({et})"
                elif tool_name == "message_person":
                    etype = args.get("entity_type", "")
                    draft = args.get("draft_message", "")
                    hint = f" → {etype}"
                    if draft:
                        hint += f": {draft[:80]}"
                elif tool_name == "create_property":
                    addr = args.get("address", "")
                    hint = f": {addr[:60]}" if addr else ""
                elif tool_name == "create_tenant":
                    name = f"{args.get('first_name', '')} {args.get('last_name', '')}".strip()
                    hint = f": {name}" if name else ""
                elif tool_name == "create_suggestion":
                    title = args.get("title", "")
                    hint = f": {title[:60]}" if title else ""
                elif tool_name in ("read_document", "analyze_document"):
                    doc_id = args.get("document_id", "")
                    if doc_id:
                        try:
                            from db.models import Document as _Doc
                            from db.session import SessionLocal as _SL
                            _db = _SL()
                            _d = _db.query(_Doc.filename).filter_by(id=doc_id).first()
                            _db.close()
                            hint = f": {_d[0]}" if _d else f" ({doc_id[:12]}…)"
                        except Exception:
                            hint = f" ({doc_id[:12]}…)"
                    if args.get("list_recent"):
                        hint = " (listing recent)"
                elif tool_name == "edit_memory":
                    et = args.get("entity_type", "")
                    hint = f" → {et}" if et else ""
                elif tool_name == "close_task":
                    hint = ""
            msg = f"{label}{hint}"
            progress_events.append(msg)
            progress_queue.put(msg)
            # The ATIF tool_call entry is created at completion (when we
            # have the real ``tool_call_id`` from litellm). For runs not
            # wrapped in an active step builder — e.g. background paths
            # that haven't migrated yet — fall back to the legacy
            # AgentTrace shim so DevTools' historical view stays intact.
            if current_step_builder() is None:
                log_trace(
                    "tool_call",
                    _trace_source,
                    msg,
                    tool_name=tool_name,
                    detail=make_trace_envelope(
                        "tool_call",
                        tool_name=tool_name,
                        args=args or {},
                        preview=preview,
                        trace_context=trace_detail,
                    ),
                )
        elif event_type == "tool.completed":
            # All "tool.completed" bookkeeping (failed/completed contextvars,
            # progress events, trace rows) happens in ``_tool_complete_cb``
            # below — that callback is the only place where we can see the
            # real ``function_result`` string. The upstream library's
            # tool.completed event passes only ``is_error`` and ``duration``,
            # so relying on it alone produces "Sending message: error" with
            # no detail about *what* failed.
            pass

    def _tool_complete_cb(tool_call_id, function_name, function_args, function_result):
        """Fires right after tool.completed with the real result string.

        Parses the tool's return payload to extract success/error status and
        any ``message`` / ``error`` field, then attaches the call + observation
        to the active ATIF step builder (or falls back to the legacy
        AgentTrace shim if no builder is open).
        """
        from agent.trajectory import current_step_builder
        try:
            label = _TOOL_LABELS.get(function_name, function_name)
            trace_detail = current_trace_context.get() or trace_context or {}
            builder = current_step_builder()

            result_text = function_result if isinstance(function_result, str) else str(function_result or "")
            is_error = False
            error_message: str | None = None
            try:
                parsed = json.loads(result_text) if result_text else None
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                status_val = str(parsed.get("status") or "").lower()
                if status_val == "error" or parsed.get("error"):
                    is_error = True
                    error_message = str(
                        parsed.get("message") or parsed.get("error") or ""
                    ).strip() or None
            if not is_error and result_text and "error" in result_text[:80].lower():
                is_error = True
                error_message = result_text

            if is_error:
                display_error = error_message or result_text or ""
                if isinstance(display_error, str) and len(display_error) > 400:
                    display_error = display_error[:400] + "…"
                msg = f"{label}: error" + (f" — {display_error}" if display_error else "")
                progress_events.append(msg)
                progress_queue.put(msg)
                failed = list(current_failed_tools.get())
                failed.append({
                    "tool_name": function_name,
                    "args": function_args or {},
                    "error": error_message or result_text,
                })
                current_failed_tools.set(failed)
                if builder is not None:
                    builder.add_tool_call(
                        tool_call_id=str(tool_call_id),
                        function_name=function_name,
                        arguments=function_args or {},
                    )
                    err_text = error_message or result_text or ""
                    if isinstance(err_text, str) and len(err_text) > 500:
                        err_text = err_text[:500] + "…"
                    builder.add_observation(
                        source_call_id=str(tool_call_id),
                        content=f"ERROR: {err_text}",
                    )
                    builder.add_extra("error_kind", "tool_error")
                else:
                    log_trace(
                        "error",
                        _trace_source,
                        msg,
                        tool_name=function_name,
                        detail=make_trace_envelope(
                            "tool_error",
                            tool_name=function_name,
                            args=function_args or {},
                            error=error_message or result_text,
                            result=result_text,
                            trace_context=trace_detail,
                        ),
                    )
                return

            # Success path — record completion + emit the (truncated) result.
            completed = list(current_completed_tools.get())
            completed.append(function_name)
            current_completed_tools.set(completed)
            trimmed = result_text
            if len(trimmed) > 500:
                trimmed = trimmed[:500] + "…"
            if builder is not None:
                builder.add_tool_call(
                    tool_call_id=str(tool_call_id),
                    function_name=function_name,
                    arguments=function_args or {},
                )
                builder.add_observation(
                    source_call_id=str(tool_call_id),
                    content=trimmed,
                )
            else:
                log_trace(
                    "tool_result",
                    _trace_source,
                    f"{label} completed",
                    tool_name=function_name,
                    detail=make_trace_envelope(
                        "tool_result",
                        tool_name=function_name,
                        args=function_args or {},
                        result=trimmed,
                        trace_context=trace_detail,
                    ),
                )
        except Exception:
            # Never let trace plumbing break tool execution.
            pass

    def _step_callback(iteration: int, prev_tools: list | None, **kwargs):
        pass  # progress is emitted via _tool_progress

    print(f"[agent] model={actual_model} provider={provider} base_url={api_base}")
    print(f"[agent] system_prompt={len(system_message)} chars, history={len(conversation_history)} msgs, user_message={len(user_message)} chars")
    if str(session_key).startswith("eval:"):
        _set_last_eval_debug_payload({
            "agent_id": agent_id,
            "session_key": session_key,
            "model": actual_model,
            "provider": provider,
            "api_base": api_base,
            "system_prompt": system_message,
            "memory_context": str(prompt_bundle.get("memory_context") or ""),
            "prompt_parts": prompt_bundle.get("parts") or [],
            "system_message_override": sys_content or "",
            "conversation_history": conversation_history,
            "user_message": user_message,
        })
    system_message = _augment_system_message_with_policies(
        system_message,
        user_message=user_message,
        session_id=str(session_key),
    )

    extra_completion_kwargs: dict[str, Any] = {}
    if api_base:
        extra_completion_kwargs["api_base"] = api_base
    if api_key:
        extra_completion_kwargs["api_key"] = api_key
    if str(session_key).startswith("eval:"):
        try:
            extra_completion_kwargs["temperature"] = float(os.getenv("EVAL_AGENT_TEMPERATURE", "0"))
        except ValueError:
            extra_completion_kwargs["temperature"] = 0.0
    extra_completion_kwargs["caching"] = False
    # Per-call timeout so a stalled provider can't wedge the whole agent
    # loop indefinitely. Override via LLM_REQUEST_TIMEOUT_SECONDS.
    try:
        extra_completion_kwargs["timeout"] = float(
            os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "60")
        )
    except ValueError:
        extra_completion_kwargs["timeout"] = 60.0
    extra_completion_kwargs["metadata"] = {
        "account_id": str(resolve_account_id()),
        "org_id": str(resolve_org_id() or ""),
        "session_key": str(session_key),
    }

    loop_obj = AgentLoop(
        model=actual_model,
        system_message=system_message,
        account_id=resolve_account_id(),
        org_id=resolve_org_id(),
        max_iterations=20,
        tool_progress_callback=_tool_progress,
        tool_complete_callback=_tool_complete_cb,
        step_callback=_step_callback,
        extra_completion_kwargs=extra_completion_kwargs,
    )

    async def _run_with_progress():
        run_task = asyncio.create_task(
            loop_obj.run(
                user_message=user_message,
                conversation_history=conversation_history if conversation_history else None,
            )
        )
        while not run_task.done():
            try:
                msg = progress_queue.get_nowait()
                if msg and on_progress:
                    await on_progress(msg)
            except queue.Empty:
                pass
            await asyncio.sleep(0.05)
        while not progress_queue.empty():
            msg = progress_queue.get_nowait()
            if msg and on_progress:
                await on_progress(msg)
        return await run_task

    try:
        result = await _run_with_progress()
    finally:
        current_user_message.reset(user_message_token)
        current_request_context.reset(request_context_token)

    if isinstance(result, dict):
        print(f"[agent] api_calls={result.get('api_calls', '?')} "
              f"completed={result.get('completed', '?')} "
              f"input_tokens={result.get('input_tokens', '?')} "
              f"output_tokens={result.get('output_tokens', '?')} "
              f"progress_events={len(progress_events)}")
        accumulate_run_totals(
            input_tokens=int(result.get("input_tokens", 0) or 0),
            output_tokens=int(result.get("output_tokens", 0) or 0),
            iteration_count=int(result.get("api_calls", 0) or 0),
        )
        if progress_events:
            for evt in progress_events:
                print(f"[agent]   progress: {evt}")
        reply = result.get("final_response", "")
        if not reply:
            msgs = result.get("messages", [])
            for m in reversed(msgs):
                if m.get("role") == "assistant" and m.get("content"):
                    reply = m["content"]
                    break
        reply = _select_best_reply(result, reply)
        # The agent library returns API errors as normal text replies
        # rather than raising exceptions.  Detect these and re-raise so
        # the SSE error path fires (red bubble, not blue).
        if reply and _is_agent_error_reply(reply):
            raise RuntimeError(reply)
        return reply
    return str(result)


def _is_agent_error_reply(reply: str) -> bool:
    """Return True if the agent reply is actually an API/infrastructure error."""
    _ERROR_PREFIXES = (
        "API call failed",
        "Operation interrupted",
        "I apologize, but I encountered repeated errors",
    )
    return reply.startswith(_ERROR_PREFIXES)


# ─── Public API ──────────────────────────────────────────────────────────────


async def call_agent(
    agent_id: str,
    *, session_key: str,
    messages: list[dict],
    on_progress: Optional[Callable] = None,
    account_context: dict[str, Any] | None = None,
    trace_context: dict[str, Any] | None = None,
) -> AgentResponse:
    """Call the agent and return its response.

    If ``RENTMATE_AGENT_URL`` is configured, sends an HTTP request to the
    hosted service.  Otherwise, falls back to the local agent.
    """
    if not AGENT_URL:
        return await _local_fallback(agent_id, session_key, messages, on_progress, trace_context)

    stream = on_progress is not None
    payload = {
        "agent_id": agent_id,
        "session_key": session_key,
        "messages": messages,
        "stream": stream,
    }
    if account_context:
        payload["account_context"] = account_context

    if stream:
        return await _stream_request(payload, on_progress)
    else:
        return await _sync_request(payload)


async def _stream_request(
    payload: dict,
    on_progress: Callable,
) -> AgentResponse:
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
        async with client.stream("POST", f"{AGENT_URL}/v1/agent/chat", json=payload) as resp:
            resp.raise_for_status()
            buffer = ""
            async for chunk in resp.aiter_text():
                buffer += chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line[6:])
                    if event["type"] == "progress":
                        await on_progress(event.get("text", ""), tool_hint=event.get("tool_hint"))
                    elif event["type"] == "done":
                        return AgentResponse(
                            reply=event["reply"],
                            side_effects=event.get("side_effects", []),
                        )
                    elif event["type"] == "error":
                        raise RuntimeError(event.get("message", "Agent error"))
    raise RuntimeError("Agent stream ended without done event")


async def _sync_request(payload: dict) -> AgentResponse:
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
        resp = await client.post(f"{AGENT_URL}/v1/agent/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return AgentResponse(
            reply=data["reply"],
            side_effects=data.get("side_effects", []),
        )


async def _agent_turn(
    agent_id: str,
    session_key: str,
    messages: list[dict],
    on_progress: Optional[Callable],
    *,
    trace_context: dict[str, Any] | None,
    model_name: str | None,
) -> str:
    """Run one agent turn inside an ATIF ``begin_agent_step`` context.

    Tool-dispatch callbacks pull the active builder off the contextvar
    and attach tool_calls + observations onto the same step row, so a
    multi-tool turn collapses to one ATIF Step. Token totals from
    ``litellm.acompletion`` flow into the run handle and the step
    builder derives its ``metrics`` from the delta on context exit.
    """
    from agent.trajectory import begin_agent_step
    with begin_agent_step("", model_name=model_name) as step:
        reply = await chat_with_agent(
            agent_id, session_key, messages, on_progress, trace_context=trace_context,
        )
        if step is not None:
            step.update_message(reply or "")
        return reply


async def _local_fallback(
    agent_id: str,
    session_key: str,
    messages: list[dict],
    on_progress: Optional[Callable] = None,
    trace_context: dict[str, Any] | None = None,
) -> AgentResponse:
    """Run the agent locally (dev mode)."""
    from agent.tools import current_request_context, current_user_message, pending_suggestion_messages

    token = pending_suggestion_messages.set([])
    failed_tools_token = current_failed_tools.set([])
    completed_tools_token = current_completed_tools.set([])
    latest_user = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
    user_token = current_user_message.set(latest_user)
    request_context_token = current_request_context.set(trace_context)
    trace_token = current_trace_context.set(trace_context)
    fallback_token = None
    try:
        fallback_token = set_fallback_request_context(
            account_id=resolve_account_id(),
            org_id=resolve_org_id(),
        )
    except RuntimeError:
        fallback_token = None

    run_metadata = derive_run_metadata(
        session_key=session_key,
        conversation_id=str((trace_context or {}).get("conversation_id") or "") or None,
    )

    _model_for_step = run_metadata.get("model")

    try:
        with start_run(
            **run_metadata,
            trigger_input=latest_user,
        ) as run:
            reply = await _agent_turn(
                agent_id, session_key, messages, on_progress,
                trace_context=trace_context, model_name=_model_for_step,
            )
            side_effects = _collect_pending_side_effects(pending_items=pending_suggestion_messages.get())
            failed_tools = list(current_failed_tools.get())
            completed_tools = list(current_completed_tools.get())
            if _reply_needs_tenant_access_message(
                reply,
                latest_user_message=latest_user,
                side_effects=side_effects,
            ):
                from agent.trajectory import record_step
                record_step(
                    "system",
                    "Assistant said tenant access needed checking without message_person",
                    extra={
                        "kind": "tool_enforcement",
                        "expected_tool": "message_person",
                        "reply": reply,
                    },
                )
                pending_suggestion_messages.set([])
                current_failed_tools.set([])
                current_completed_tools.set([])
                corrective_messages = [
                    *messages,
                    {"role": "assistant", "content": reply},
                    {
                        "role": "user",
                        "content": (
                            "System correction: you said tenant access or availability needs to be checked, "
                            "but you did not call message_person. Call message_person for the task tenant now. "
                            "Do not message the vendor again until the tenant actually confirms the proposed time works."
                        ),
                    },
                ]
                reply = await _agent_turn(
                    agent_id, session_key, corrective_messages, on_progress,
                    trace_context=trace_context, model_name=_model_for_step,
                )
                side_effects = _collect_pending_side_effects(pending_items=pending_suggestion_messages.get())
                failed_tools = list(current_failed_tools.get())
                completed_tools = list(current_completed_tools.get())
            if _reply_claims_document_created(reply) and not _has_document_side_effect(side_effects):
                side_effect_reply = _summarize_non_document_side_effects(side_effects)
                if side_effect_reply:
                    reply = side_effect_reply
                    run.complete(status="completed", final_response=reply)
                    return AgentResponse(reply=reply, side_effects=side_effects)
                from agent.trajectory import record_step
                record_step(
                    "system",
                    "Assistant claimed document creation without create_document tool call",
                    extra={
                        "kind": "tool_enforcement",
                        "expected_tool": "create_document",
                        "reply": reply,
                    },
                )
                pending_suggestion_messages.set([])
                corrective_messages = [
                    *messages,
                    {"role": "assistant", "content": reply},
                    {
                        "role": "user",
                        "content": (
                            "System correction: you claimed a document was created, but no create_document tool was called. "
                            "If the user asked for a document, call create_document now. "
                            "Do not claim a document exists unless the tool succeeds."
                        ),
                    },
                ]
                reply = await _agent_turn(
                    agent_id, session_key, corrective_messages, on_progress,
                    trace_context=trace_context, model_name=_model_for_step,
                )
                side_effects = _collect_pending_side_effects(pending_items=pending_suggestion_messages.get())
                if _reply_claims_document_created(reply) and not _has_document_side_effect(side_effects):
                    reply = (
                        "I did not create the document. The document tool was not executed successfully, "
                        "so there is no new file in Documents."
                    )
            switched_mutating_tool = _failed_mutating_tool_switched(
                failed_tools=failed_tools,
                completed_tools=completed_tools,
            )
            if switched_mutating_tool:
                failed_tool, replacement_tool = switched_mutating_tool
                from agent.trajectory import record_step
                record_step(
                    "system",
                    "Assistant switched to a different mutating tool after tool failure",
                    extra={
                        "kind": "tool_enforcement",
                        "failed_tool": failed_tool,
                        "replacement_tool": replacement_tool,
                        "reply": reply,
                    },
                )
                pending_suggestion_messages.set([])
                current_failed_tools.set([])
                current_completed_tools.set([])
                corrective_messages = [
                    *messages,
                    {"role": "assistant", "content": reply},
                    {
                        "role": "user",
                        "content": (
                            "System correction: your last mutating tool failed. "
                            f"Do not switch from {failed_tool} to a different mutating tool such as {replacement_tool} in the same turn. "
                            "Either retry the same tool if appropriate, ask the user for missing input, or explain the failure without creating other side effects."
                        ),
                    },
                ]
                reply = await _agent_turn(
                    agent_id, session_key, corrective_messages, on_progress,
                    trace_context=trace_context, model_name=_model_for_step,
                )
                side_effects = _collect_pending_side_effects(pending_items=pending_suggestion_messages.get())
                failed_tools = list(current_failed_tools.get())
                completed_tools = list(current_completed_tools.get())
                if _failed_mutating_tool_switched(
                    failed_tools=failed_tools,
                    completed_tools=completed_tools,
                ):
                    pending_suggestion_messages.set([])
                    side_effects = []
                    reply = (
                        f"The requested action failed when attempting {failed_tool}. "
                        "I did not perform a different side effect in its place."
                    )
            synthesized_failure_reply = _synthesize_failed_tool_reply(failed_tools)
            if synthesized_failure_reply and not side_effects and _reply_is_only_tool_progress(reply):
                reply = synthesized_failure_reply
            run.complete(status="completed", final_response=reply)
            return AgentResponse(reply=reply, side_effects=side_effects)
    finally:
        if fallback_token is not None:
            reset_fallback_request_context(fallback_token)
        current_user_message.reset(user_token)
        current_request_context.reset(request_context_token)
        current_trace_context.reset(trace_token)
        current_failed_tools.reset(failed_tools_token)
        current_completed_tools.reset(completed_tools_token)
        pending_suggestion_messages.reset(token)
