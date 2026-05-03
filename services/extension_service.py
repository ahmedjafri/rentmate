"""Helpers for the GraphQL fields the chrome extension calls.

Three responsibilities:

- ``rank_tenants`` — fuzzy-search the org's active tenants by name, email,
  or phone. Used by the extension to map a scraped sender name from an
  external chat platform onto a rentmate tenant id before drafting.
- ``draft_reply`` — drive the actual rentmate agent over an external chat
  thread and return its drafted reply. The thread is mirrored into
  rentmate as a read-only ``MIRRORED_CHAT`` Conversation so DevTools
  can show the AgentRun, and so re-clicking "Suggest" for the same thread
  doesn't duplicate the mirrored history.
- ``MirrorConversationReadOnly`` — exception raised when something tries
  to send a new message into a mirror conversation; the inbound history
  is authoritative, replies happen back on the source platform.

Both helpers run inside a request context that already has org/account
ids resolved (subdomain middleware / JWT auth), so they don't take ids
explicitly — they piggyback on ``fetch_tenants`` / ``resolve_org_id``.
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from db.models import (
    Conversation,
    ConversationType,
    Message,
    MessageType,
    ParticipantType,
)
from db.queries import fetch_tenants
from integrations.local_auth import resolve_account_id, resolve_org_id

logger = logging.getLogger("rentmate.gql.extension")


_FALLBACK_REPLY = "I'll follow up on this shortly."

_EXACT_MATCH_SCORE = 100
_FULL_NAME_MATCH_SCORE = 50
_PARTIAL_NAME_MATCH_SCORE = 25
_MAX_RESULTS = 3

# Lowercase tokens external chat platforms use for the property manager
# themselves. Used to map a scraped sender name onto
# ParticipantType.ACCOUNT_USER vs EXTERNAL_CONTACT when mirroring
# messages — most platforms label the PM as "You" or "Property Manager".
_PM_SENDER_TOKENS = ("you", "property manager", "manager")

# Source identifier used when the client doesn't supply one. Stored on
# the mirror Conversation's ``extra.source`` so future analytics /
# integrations can tell where a thread came from. The chrome extension
# overrides this with its own platform-specific value.
_DEFAULT_SOURCE = "chrome_extension"


class MirrorConversationReadOnly(Exception):
    """Raised when send-message paths target a MIRRORED_CHAT Conversation.

    The inbound mirror thread is the authoritative record for both PM and
    tenant turns; new replies happen on the source platform, not rentmate.
    """


def _score_tenant(tenant: Any, query: str) -> int:
    """Return a 0-100 fuzzy-match score; 0 means no meaningful match."""
    user = getattr(tenant, "user", None)
    if user is None:
        return 0

    email = (user.email or "").strip().lower()
    phone = (user.phone or "").strip().lower()
    first = (user.first_name or "").strip().lower()
    last = (user.last_name or "").strip().lower()
    full = f"{first} {last}".strip()

    if email and query == email:
        return _EXACT_MATCH_SCORE
    if phone and query == phone:
        return _EXACT_MATCH_SCORE
    if email and query in email:
        return _FULL_NAME_MATCH_SCORE
    if full and query in full:
        return _FULL_NAME_MATCH_SCORE
    if first and query in first:
        return _PARTIAL_NAME_MATCH_SCORE
    if last and query in last:
        return _PARTIAL_NAME_MATCH_SCORE
    return 0


def _result_payload(tenant: Any, score: int) -> dict[str, Any]:
    """Shape a tenant + score into the dict the GraphQL type uses."""
    user = tenant.user
    name = " ".join(filter(None, [user.first_name, user.last_name])).strip() or "Tenant"
    leases = list(getattr(tenant, "leases", []) or [])
    lease = leases[0] if leases else None
    property_id = None
    unit_label = None
    if lease is not None:
        prop = getattr(lease, "property", None)
        if prop is not None:
            property_id = str(prop.id)
        unit = getattr(lease, "unit", None)
        if unit is not None:
            unit_label = unit.label
    return {
        "tenant_id": str(tenant.external_id),
        "name": name,
        "email": user.email or None,
        "phone": user.phone or None,
        "property_id": property_id,
        "unit_label": unit_label,
        "score": score,
    }


def rank_tenants(db: Any, query: str) -> list[dict[str, Any]]:
    """Return the top 3 tenants matching ``query``, ordered by score desc
    then name asc. Returns ``[]`` if nothing meaningfully matches."""
    needle = (query or "").strip().lower()
    if not needle:
        return []
    scored: list[tuple[int, str, Any]] = []
    for tenant in fetch_tenants(db):
        score = _score_tenant(tenant, needle)
        if score <= 0:
            continue
        sort_name = " ".join(filter(None, [
            (tenant.user.first_name or "").lower(),
            (tenant.user.last_name or "").lower(),
        ])).strip()
        scored.append((score, sort_name, tenant))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [_result_payload(t, score) for score, _name, t in scored[:_MAX_RESULTS]]


def _resolve_tenant_for_reply(db: Any, tenant_id: str | None) -> dict[str, Any] | None:
    """Resolve a TenantSearchResult-shaped payload for a known external
    tenant id. Used so the suggestReply mutation can echo back the matched
    tenant for the extension UI."""
    if not tenant_id:
        return None
    for tenant in fetch_tenants(db):
        if str(tenant.external_id) == str(tenant_id):
            return _result_payload(tenant, _EXACT_MATCH_SCORE)
    return None


def _is_pm_sender(sender: str | None, *, is_pm: bool | None = None) -> bool:
    if is_pm is not None:
        return is_pm
    return (sender or "").strip().lower() in _PM_SENDER_TOKENS


def _find_mirror_conversation(db: Session, *, external_thread_id: str) -> Conversation | None:
    """Look up the read-only mirror Conversation for this external thread.

    Stored in ``Conversation.extra->>'external_thread_id'`` so two threads
    with the same external URL don't collide and so we can dedup messages
    on a re-import without scanning every conversation in the org.
    """
    rows = (
        db.query(Conversation)
        .filter_by(
            org_id=resolve_org_id(),
            creator_id=resolve_account_id(),
            conversation_type=ConversationType.MIRRORED_CHAT,
        )
        .all()
    )
    for conv in rows:
        extra = conv.extra or {}
        if str(extra.get("external_thread_id") or "") == str(external_thread_id):
            return conv
    return None


def _upsert_mirror_conversation(
    db: Session,
    *,
    external_thread_id: str,
    header_title: str | None,
    header_description: str | None,
    tenant_payload: dict[str, Any] | None,
    property_id: str | None,
    source: str | None = None,
) -> Conversation:
    """Find or create the read-only mirror Conversation for an external thread.

    The conversation is keyed by ``external_thread_id`` (typically the
    URL pathname scraped from the source platform) so re-clicking
    ``Suggest`` for the same thread reuses the existing row instead of
    spawning duplicates.
    """
    conv = _find_mirror_conversation(db, external_thread_id=external_thread_id)
    now = datetime.now(UTC)
    subject = (header_title or "").strip() or "Mirrored thread"
    resolved_source = (source or _DEFAULT_SOURCE).strip() or _DEFAULT_SOURCE
    if conv is None:
        extra = {
            "source": resolved_source,
            "external_thread_id": str(external_thread_id),
            "read_only": True,
        }
        if tenant_payload:
            extra["matched_tenant_id"] = tenant_payload.get("tenant_id")
        if header_description:
            extra["header_description"] = header_description
        conv = Conversation(
            org_id=resolve_org_id(),
            creator_id=resolve_account_id(),
            subject=subject[:255],
            property_id=property_id or (tenant_payload or {}).get("property_id"),
            conversation_type=ConversationType.MIRRORED_CHAT,
            is_group=False,
            is_archived=False,
            extra=extra,
            created_at=now,
            updated_at=now,
        )
        db.add(conv)
        db.flush()
        return conv

    extra = dict(conv.extra or {})
    changed = False
    if header_description and extra.get("header_description") != header_description:
        extra["header_description"] = header_description
        changed = True
    if tenant_payload and not extra.get("matched_tenant_id"):
        extra["matched_tenant_id"] = tenant_payload.get("tenant_id")
        changed = True
    if changed:
        conv.extra = extra
        flag_modified(conv, "extra")
    if subject and conv.subject != subject[:255]:
        conv.subject = subject[:255]
    conv.updated_at = now
    db.flush()
    return conv


def _existing_mirror_indices(conv: Conversation) -> set[int]:
    """Indices of external turns already mirrored on this conversation.

    Each mirrored Message stores its zero-based position inside the
    source thread under ``meta.mirror_index``. The set is used to skip
    turns that were imported on a previous ``Suggest`` click.
    """
    indices: set[int] = set()
    for msg in conv.messages or []:
        meta = msg.meta or {}
        idx = meta.get("mirror_index")
        if isinstance(idx, int):
            indices.add(idx)
    return indices


def _sync_mirror_messages(
    db: Session,
    *,
    conv: Conversation,
    conversation_history: list[dict[str, str]],
    source: str | None = None,
) -> int:
    """Insert any external turns we haven't mirrored yet.

    Dedup is position-based: external chat platforms render a thread
    chronologically, so the n-th turn is stable across scrapes. Each
    mirrored Message records ``meta.mirror_index = n``; turns whose
    index is already on the conversation are skipped. Returns the number
    of messages newly inserted.
    """
    if not conversation_history:
        return 0
    existing = _existing_mirror_indices(conv)
    inserted = 0
    now = datetime.now(UTC)
    for idx, turn in enumerate(conversation_history):
        if idx in existing:
            continue
        text = (turn.get("text") or "").strip()
        if not text:
            continue
        sender = (turn.get("sender") or "").strip() or "Tenant"
        is_pm = _is_pm_sender(sender, is_pm=turn.get("is_pm"))
        msg = Message(
            org_id=resolve_org_id(),
            conversation_id=conv.id,
            sender_type=(
                ParticipantType.ACCOUNT_USER if is_pm else ParticipantType.EXTERNAL_CONTACT
            ),
            sender_id=None,
            body=text,
            message_type=MessageType.MESSAGE,
            sender_name=sender[:255],
            is_ai=False,
            is_system=False,
            meta={
                "source": (source or _DEFAULT_SOURCE),
                "mirror_index": idx,
                "direction": "outbound" if is_pm else "inbound",
            },
            sent_at=now,
        )
        db.add(msg)
        inserted += 1
    if inserted:
        conv.updated_at = now
    return inserted


def _build_system_prompt(
    *,
    tenant_payload: dict[str, Any] | None,
    header_title: str | None,
    header_description: str | None,
    refine_mode: bool = False,
) -> str:
    if refine_mode:
        intro = (
            "You are RentMate refining a draft reply the property manager "
            "has already typed in their tenant chat tool. Polish the "
            "draft for clarity, tone, and completeness while preserving "
            "the PM's intent and any concrete facts they wrote (dates, "
            "vendor names, dollar amounts). Keep it SMS-style, 1-3 "
            "sentences, warm and professional. Do not invent facts the "
            "PM didn't include. Do not mention RentMate or that you are "
            "an AI. Output only the refined reply text, no labels or "
            "commentary."
        )
    else:
        intro = (
            "You are RentMate drafting a reply on behalf of the property "
            "manager. The PM is composing a response to a tenant in their "
            "tenant chat tool. Draft a single SMS-style reply, 1-3 "
            "sentences, warm and professional. Do not invent facts or "
            "commit to dates. Do not mention RentMate or that you are an "
            "AI. Output only the reply text, no labels."
        )
    parts: list[str] = [intro]
    if tenant_payload:
        unit = f" ({tenant_payload['unit_label']})" if tenant_payload.get("unit_label") else ""
        parts.append(
            f"You are replying to {tenant_payload['name']}{unit}, "
            "an active tenant. Address them by first name."
        )
    if header_title:
        parts.append(f"Maintenance request title: {header_title}")
    if header_description:
        parts.append(f"Maintenance request details: {header_description}")
    return "\n".join(parts)


def _build_user_message(
    history: list[dict[str, str]],
    *,
    draft_text: str | None = None,
) -> str:
    transcript_lines = [
        f"{(turn.get('sender') or 'Tenant')}: {(turn.get('text') or '').strip()}"
        for turn in history
        if (turn.get('text') or '').strip()
    ]
    transcript = "\n".join(transcript_lines) if transcript_lines else "(No prior messages.)"
    if draft_text:
        return (
            "Recent conversation between the property manager and the "
            "tenant (most recent last):\n\n"
            f"{transcript}\n\n"
            "The property manager has typed the following draft reply. "
            "Refine it — keep their intent and any concrete facts, but "
            "improve clarity and tone:\n\n"
            f"{draft_text.strip()}"
        )
    if not transcript_lines:
        return "(No prior messages.) Open the conversation with a brief acknowledgement."
    return (
        "Recent conversation between the property manager and the tenant "
        "(most recent last):\n\n"
        f"{transcript}\n\n"
        "Draft the property manager's next reply."
    )


def _classify_llm_error(exc: Exception) -> str:
    """Map a LiteLLM / agent failure to a short, actionable user-facing
    string. The chrome extension renders this verbatim so PMs know
    whether the hosted LLM is mis-configured (auth, model, base URL) vs.
    a transient blip worth retrying."""
    name = type(exc).__name__
    msg = str(exc).lower()
    if "authentication" in name.lower() or "incorrect api key" in msg or "401" in msg:
        return "Hosted LLM rejected the API key. Check LLM_API_KEY in the rentmate Cloud Run service."
    if "model" in msg and ("not found" in msg or "does not exist" in msg or "invalid" in msg):
        return "Hosted LLM doesn't recognise the configured model. Check LLM_MODEL."
    if "rate" in msg and "limit" in msg or "429" in msg:
        return "Hosted LLM is rate-limiting. Try again in a moment."
    if "timeout" in msg or "timed out" in msg:
        return "Hosted LLM timed out. Try again."
    if "connection" in msg or "could not reach" in msg or "unreachable" in msg:
        return "Hosted LLM endpoint unreachable. Check LLM_BASE_URL."
    detail = str(exc)[:160]
    return f"Hosted LLM error: {detail}"


async def _draft_via_agent(
    *,
    conv: Conversation,
    system: str,
    user_msg: str,
) -> str:
    """Run the rentmate chat agent for the mirror conversation.

    Wraps the call in ``start_run(source="extension", conversation_id=...)``
    so DevTools surfaces the run alongside chat/task agent runs. The agent
    is invoked with a single-turn user prompt containing the formatted
    transcript — we don't replay individual messages as alternating
    user/assistant turns because external chat platforms' PM/tenant labels don't
    cleanly map onto the agent's role taxonomy.
    """
    from agent import registry as agent_registry_mod
    from agent.client import call_agent
    from agent.runs import derive_run_metadata, start_run

    agent_id = agent_registry_mod.agent_registry.ensure_agent(resolve_account_id(), None)
    run_metadata = derive_run_metadata(
        source_override="extension",
        conversation_id=str(conv.external_id),
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg},
    ]
    with start_run(**run_metadata, trigger_input=user_msg) as run:
        agent_resp = await call_agent(
            agent_id,
            session_key=f"extension:{conv.external_id}",
            messages=messages,
            trace_context={"conversation_id": str(conv.external_id)},
        )
        suggestion = (getattr(agent_resp, "reply", "") or "").strip()
        if suggestion:
            run.complete(status="completed", final_response=suggestion[:500])
    return suggestion


async def _one_shot_completion(*, system: str, user_msg: str) -> str:
    """Fallback completion for callers without an external_thread_id.

    Older versions of the chrome extension don't send a thread id; rather
    than refuse those requests we still draft a reply via a single
    LiteLLM call (no AgentRun, no mirror). Once every PM has updated the
    extension this branch can go away.
    """
    import litellm

    from agent.model_config import build_litellm_request_kwargs

    model = os.getenv("LLM_MODEL", "openai/gpt-4o-mini")
    kwargs = build_litellm_request_kwargs(
        model=model,
        api_base=os.getenv("LLM_BASE_URL") or None,
        api_key=os.getenv("LLM_API_KEY"),
        app_name="rentmate-chrome-extension",
    )
    resp = await litellm.acompletion(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        max_tokens=200,
        temperature=0.4,
        **kwargs,
    )
    return (resp.choices[0].message.content or "").strip()


async def draft_reply(
    db: Any,
    *,
    conversation_history: list[dict[str, str]],
    header_title: str | None,
    header_description: str | None,
    tenant_id: str | None,
    property_id: str | None,
    external_thread_id: str | None = None,
    draft_text: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Draft (or refine) a reply for an external chat thread and persist
    a read-only mirror.

    When ``draft_text`` is non-empty the extension is in *Refine* mode —
    the PM has already typed something into the source platform's reply
    box, so we ask the agent to polish that draft instead of composing
    fresh. Otherwise behaves as the standard *Suggest* flow.

    When ``external_thread_id`` is provided we upsert a
    ``MIRRORED_CHAT`` Conversation, dedup-insert any new turns from
    ``conversation_history``, and run the actual rentmate agent so the
    invocation appears in DevTools. Without an external thread id we fall
    back to a one-shot LiteLLM completion (older extension versions).

    On any failure the result still contains a canned ``suggestion`` plus
    ``error`` + ``fallback=True`` so the extension banner can surface the
    real cause instead of pretending the canned reply is a draft.
    """
    refine_mode = bool((draft_text or "").strip())
    matched_tenant = _resolve_tenant_for_reply(db, tenant_id)
    system = _build_system_prompt(
        tenant_payload=matched_tenant,
        header_title=header_title,
        header_description=header_description,
        refine_mode=refine_mode,
    )
    user_msg = _build_user_message(
        conversation_history or [],
        draft_text=draft_text if refine_mode else None,
    )

    mirror_conv: Conversation | None = None
    if external_thread_id:
        mirror_conv = _upsert_mirror_conversation(
            db,
            external_thread_id=external_thread_id,
            header_title=header_title,
            header_description=header_description,
            tenant_payload=matched_tenant,
            property_id=property_id,
            source=source,
        )
        _sync_mirror_messages(
            db,
            conv=mirror_conv,
            conversation_history=conversation_history or [],
            source=source,
        )
        db.commit()
        db.refresh(mirror_conv)

    try:
        if mirror_conv is not None:
            suggestion = await _draft_via_agent(conv=mirror_conv, system=system, user_msg=user_msg)
        else:
            suggestion = await _one_shot_completion(system=system, user_msg=user_msg)
        if not suggestion:
            raise ValueError("empty completion")
        suggestion = suggestion[:500]
    except Exception as exc:  # noqa: BLE001
        logger.warning("[extension] suggestReply agent/LLM failed; using canned: %s", exc)
        return {
            "suggestion": _FALLBACK_REPLY,
            "matched_tenant": matched_tenant,
            "error": _classify_llm_error(exc),
            "fallback": True,
            "conversation_external_id": str(mirror_conv.external_id) if mirror_conv else None,
        }

    return {
        "suggestion": suggestion,
        "matched_tenant": matched_tenant,
        "error": None,
        "fallback": False,
        "conversation_external_id": str(mirror_conv.external_id) if mirror_conv else None,
    }
