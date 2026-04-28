"""Helpers for the GraphQL fields the chrome extension calls.

Two responsibilities:

- ``rank_tenants`` — fuzzy-search the org's active tenants by name, email,
  or phone. Used by the extension to map a TenantCloud sender name onto a
  rentmate tenant id before drafting a reply.
- ``draft_reply`` — one-shot LiteLLM completion that returns an SMS-style
  reply for the conversation the PM is looking at in TenantCloud. No DB
  writes, no agent loop, no tools. On any LLM error it falls back to a
  canned acknowledgement so the extension never crashes.

Both helpers run inside a request context that already has org/account
ids resolved (subdomain middleware / JWT auth), so they don't take ids
explicitly — they piggyback on ``fetch_tenants`` / ``resolve_org_id``.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from db.queries import fetch_tenants

logger = logging.getLogger("rentmate.gql.extension")


_FALLBACK_REPLY = "I'll follow up on this shortly."

_EXACT_MATCH_SCORE = 100
_FULL_NAME_MATCH_SCORE = 50
_PARTIAL_NAME_MATCH_SCORE = 25
_MAX_RESULTS = 3


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


def _build_system_prompt(*, tenant_payload: dict[str, Any] | None, header_title: str | None, header_description: str | None) -> str:
    intro = (
        "You are RentMate drafting a reply on behalf of the property "
        "manager. The PM is composing a response to a tenant inside "
        "TenantCloud. Draft a single SMS-style reply, 1-3 sentences, "
        "warm and professional. Do not invent facts or commit to dates. "
        "Do not mention RentMate or that you are an AI."
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


def _build_user_message(history: list[dict[str, str]]) -> str:
    if not history:
        return "(No prior messages.) Open the conversation with a brief acknowledgement."
    transcript = "\n".join(
        f"{(turn.get('sender') or 'Tenant')}: {(turn.get('text') or '').strip()}"
        for turn in history
        if (turn.get('text') or '').strip()
    )
    return (
        "Recent conversation between the property manager and the tenant "
        "(most recent last):\n\n"
        f"{transcript}\n\n"
        "Draft the property manager's next reply. Output only the reply "
        "text, no labels."
    )


def _classify_llm_error(exc: Exception) -> str:
    """Map a LiteLLM failure to a short, actionable user-facing string.
    The chrome extension renders this verbatim so PMs know whether the
    hosted LLM is mis-configured (auth, model, base URL) vs. a transient
    blip worth retrying."""
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
    # Generic — include a short prefix of the upstream error so support
    # has something to grep without leaking full tracebacks to the UI.
    detail = str(exc)[:160]
    return f"Hosted LLM error: {detail}"


async def draft_reply(
    db: Any,
    *,
    conversation_history: list[dict[str, str]],
    header_title: str | None,
    header_description: str | None,
    tenant_id: str | None,
    property_id: str | None,
) -> dict[str, Any]:
    """Run a single LiteLLM completion and return the drafted reply +
    matched-tenant echo. On any LLM error, populate ``error`` and
    ``fallback`` so the chrome extension can surface a real banner
    instead of pretending the canned reply is a draft."""
    import litellm

    from llm.model_config import build_litellm_request_kwargs

    matched_tenant = _resolve_tenant_for_reply(db, tenant_id)
    system = _build_system_prompt(
        tenant_payload=matched_tenant,
        header_title=header_title,
        header_description=header_description,
    )
    user_msg = _build_user_message(conversation_history or [])

    model = os.getenv("LLM_MODEL", "openai/gpt-4o-mini")
    kwargs = build_litellm_request_kwargs(
        model=model,
        api_base=os.getenv("LLM_BASE_URL") or None,
        api_key=os.getenv("LLM_API_KEY"),
        app_name="rentmate-chrome-extension",
    )
    try:
        resp = await litellm.acompletion(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=200,
            temperature=0.4,
            **kwargs,
        )
        suggestion = (resp.choices[0].message.content or "").strip()
        if not suggestion:
            raise ValueError("empty completion")
        suggestion = suggestion[:500]
    except Exception as exc:  # noqa: BLE001
        logger.warning("[extension] suggestReply LLM failed; using canned: %s", exc)
        return {
            "suggestion": _FALLBACK_REPLY,
            "matched_tenant": matched_tenant,
            "error": _classify_llm_error(exc),
            "fallback": True,
        }

    return {
        "suggestion": suggestion,
        "matched_tenant": matched_tenant,
        "error": None,
        "fallback": False,
    }
