"""SMS and outbound messaging service.

Handles sending SMS via Quo (OpenPhone) and dispatching messages to the
appropriate outbound channel (SMS, email). No handler-layer dependencies.
"""
import asyncio
import os

import httpx

from gql.services.settings_service import get_integrations

QUO_API_KEY = os.getenv("QUO_API_KEY", "")


def _normalize_phone(num: str) -> str:
    """Ensure phone number has +1 prefix for US numbers."""
    digits = ''.join(c for c in num if c.isdigit())
    if digits.startswith('1') and len(digits) == 11:
        return f"+{digits}"
    if len(digits) == 10:
        return f"+1{digits}"
    if num.startswith('+'):
        return num
    return f"+{digits}"


def get_quo_api_key() -> str:
    """Get Quo (OpenPhone) API key from integrations config or env var."""
    cfg = get_integrations().get("quo", {})
    return cfg.get("api_key") or QUO_API_KEY


def get_quo_from_number() -> str:
    """Get the outbound phone number from Quo config."""
    cfg = get_integrations().get("quo", {})
    return cfg.get("from_number") or ""


async def send_sms_reply(from_num: str, to_num: str, text: str, api_key: str | None = None):
    """Send an SMS via Quo (OpenPhone) API."""
    key = api_key or get_quo_api_key()
    if not key:
        print("[sms] No Quo API key configured — skipping SMS")
        return
    to_num = _normalize_phone(to_num)
    if from_num:
        from_num = _normalize_phone(from_num)
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.openphone.com/v1/messages",
            headers={
                "Authorization": key,
                "Content-Type": "application/json",
            },
            json={
                "content": text,
                "from": from_num,
                "to": [to_num],
            },
        )
        print(f"[sms] Quo response: {response.status_code} {response.text[:200]}")


async def send_email_reply(conv, body: str, inbound_meta: dict):
    """Send an email reply via Gmail. Requires GmailClient to be configured."""
    try:
        from backends.gmail import GmailClient  # noqa: F401 — optional dep
        client = GmailClient()
        to_address = inbound_meta.get("from_address", "")
        subject = inbound_meta.get("subject", conv.subject or "Re: Your message")
        thread_id = inbound_meta.get("thread_id")
        await asyncio.to_thread(
            client.send_reply,
            to=to_address,
            subject=subject,
            body=body,
            thread_id=thread_id,
        )
    except Exception as e:
        print(f"[send_email_reply] Failed: {e}")


async def send_via_channel(conv, reply: str, inbound_meta: dict):
    """Dispatch an agent reply to the appropriate outbound channel."""
    if conv.channel_type == "sms":
        await send_sms_reply(
            from_num=inbound_meta.get("to_number", ""),
            to_num=inbound_meta.get("from_number", ""),
            text=reply,
        )
    elif conv.channel_type == "email":
        await send_email_reply(conv=conv, body=reply, inbound_meta=inbound_meta)
