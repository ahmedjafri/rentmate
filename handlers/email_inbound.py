"""Inbound email webhook handler for Postmark Inbound.

When someone CCs agent@snoresidences.rentmate.io on an email, Postmark receives
it and POSTs the parsed email as JSON to /api/email/inbound.  This module:

  1. Verifies the Postmark HMAC signature so only genuine Postmark payloads
     are accepted (skipped in development mode for easy local testing).
  2. Parses the Postmark JSON into a clean ParsedEmail dataclass.
  3. Delegates to process_inbound_email() in handlers/chat.py which stores
     the email and triggers the agent if a tenant was resolved.

We always return {"status": "ok"} with HTTP 200 even on duplicates or
unresolvable senders — Postmark retries on non-2xx responses and we'd end
up processing the same email twice if we returned an error code.

DNS setup for the domain owner is documented in the plan file at:
  .claude/plans/abstract-scribbling-rabin.md → "DNS Instructions" section
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from handlers.deps import get_db
from handlers.email_parser import ParsedEmail, parse_postmark_payload

router = APIRouter()


# ─── Signature verification ───────────────────────────────────────────────────

async def verify_postmark_signature(request: Request, raw_body: bytes) -> None:
    """Verify the X-Postmark-Signature HMAC-SHA256 header.

    Postmark signs every inbound webhook with an HMAC-SHA256 digest of the
    raw request body using the server's webhook token as the key.  Skipped
    in development mode (RENTMATE_ENV=development) so you can test with a
    plain curl without needing the real token.

    The token is read from the env var POSTMARK_INBOUND_WEBHOOK_TOKEN first,
    then falls back to the value stored in the integrations settings table
    (the same pattern quo_poller.py uses for its API key).
    """
    if os.getenv("RENTMATE_ENV", "").lower() == "development":
        # Local dev — skip signature check so curl / pytest work without a token.
        return

    # Resolve token: env var takes precedence over DB setting.
    token = os.getenv("POSTMARK_INBOUND_WEBHOOK_TOKEN", "").strip()
    if not token:
        from handlers.settings import load_integrations
        token = (load_integrations().get("email") or {}).get("webhook_token", "").strip()

    if not token:
        # Token not configured in either place — refuse all requests in
        # production so a mis-configured server doesn't accept spoofed emails.
        raise HTTPException(
            status_code=500,
            detail="POSTMARK_INBOUND_WEBHOOK_TOKEN not configured.",
        )

    given_sig = request.headers.get("X-Postmark-Signature", "")
    expected_sig = base64.b64encode(
        hmac.new(token.encode(), raw_body, hashlib.sha256).digest()
    ).decode()

    # Use constant-time comparison to prevent timing attacks.
    if not hmac.compare_digest(given_sig, expected_sig):
        raise HTTPException(status_code=403, detail="Invalid webhook signature.")


# ─── Webhook endpoint ─────────────────────────────────────────────────────────

@router.post("/api/email/inbound")
async def handle_inbound_email(
    request: Request,
    db: Session = Depends(get_db),
):
    """Receive and process one inbound email from Postmark.

    Postmark POSTs here whenever an email arrives at the configured inbound
    address (e.g. agent@snoresidences.rentmate.io).  The full parsed email
    JSON is in the request body.

    Always returns {"status": "ok"} so Postmark doesn't retry on legitimate
    skips (duplicates, unknown senders, disabled integration).
    """
    # Read the raw bytes before anything else — we need them for both the
    # signature check and the JSON parse.  Once consumed they can't be re-read.
    raw_body = await request.body()

    # ── Signature check (skipped in dev mode) ─────────────────────────────
    await verify_postmark_signature(request, raw_body)

    # ── Integration enabled? ───────────────────────────────────────────────
    from handlers.settings import load_integrations
    email_cfg = load_integrations().get("email") or {}
    if email_cfg.get("enabled") is False:
        # Explicitly disabled — store nothing, return ok so Postmark stops retrying.
        print("[email-inbound] Email integration disabled — skipping")
        return {"status": "ok"}

    # ── Parse payload ─────────────────────────────────────────────────────
    try:
        data = json.loads(raw_body)
    except Exception:
        # Malformed JSON — nothing we can do, ack to prevent infinite retries.
        print("[email-inbound] Could not parse request body as JSON")
        return {"status": "ok"}

    # ── Optional domain allowlist ─────────────────────────────────────────
    # If allowed_domains is configured, only accept emails from those domains.
    # Unknown domains are stored (mirrored) but we don't fire the agent for them.
    allowed_domains: list[str] = email_cfg.get("allowed_domains") or []
    parsed: ParsedEmail = parse_postmark_payload(data)

    if allowed_domains:
        sender_domain = parsed.from_email.split("@")[-1].lower()
        if sender_domain not in [d.lower() for d in allowed_domains]:
            print(
                f"[email-inbound] Sender domain {sender_domain!r} not in allowlist — "
                "storing without triggering agent"
            )
            # Mirror the email for context but skip autonomous task creation.
            # process_inbound_email resolves account context correctly — don't
            # hardcode account_id here.
            from handlers.chat import process_inbound_email
            await process_inbound_email(db, parsed, auto_spawn_task=False)
            return {"status": "ok"}

    # ── Main ingest path ──────────────────────────────────────────────────
    # Delegates to process_inbound_email() in handlers/chat.py which owns
    # the full email→task→agent pipeline (same function shape as process_inbound_sms).
    from handlers.chat import process_inbound_email
    await process_inbound_email(db, parsed)

    return {"status": "ok"}
