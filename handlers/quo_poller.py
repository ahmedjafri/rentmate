"""Poll Quo (OpenPhone) for new SMS messages.

Local/dev: every 5 minutes — primary inbound channel (no webhook needed).
Production: every 15 minutes with jitter — backup to catch messages the webhook missed.
"""
import asyncio
import os
import random
from datetime import UTC, datetime, timedelta

import httpx

from handlers.deps import SessionLocal

# Watermark: last successful poll time. Resets on server restart,
# at which point we look back 30 minutes to catch anything missed.
_last_poll: datetime | None = None

_QUO_API = "https://api.openphone.com/v1"


def _get_config() -> tuple[str, str]:
    from handlers.settings import load_integrations
    cfg = load_integrations().get("quo", {})
    api_key = cfg.get("api_key") or os.getenv("QUO_API_KEY", "")
    from_number = cfg.get("from_number") or ""
    return api_key, from_number


def _is_production() -> bool:
    return bool(os.environ.get("RENTMATE_PUBLIC_URL"))


async def _get_phone_number_ids(client: httpx.AsyncClient, api_key: str) -> list[str]:
    """List all Quo phone number IDs in the workspace."""
    try:
        r = await client.get(
            f"{_QUO_API}/phone-numbers",
            headers={"Authorization": api_key},
            timeout=15,
        )
        if r.status_code != 200:
            print(f"[quo-poll] Failed to list phone numbers: {r.status_code}")
            return []
        return [pn["id"] for pn in r.json().get("data", [])]
    except Exception as e:
        print(f"[quo-poll] Error listing phone numbers: {e}")
        return []


async def _fetch_messages(
    client: httpx.AsyncClient,
    api_key: str,
    phone_number_id: str,
    participant: str,
    since: datetime,
) -> list[dict]:
    """Fetch inbound messages from Quo for a specific participant since a timestamp."""
    try:
        r = await client.get(
            f"{_QUO_API}/messages",
            headers={"Authorization": api_key},
            params={
                "phoneNumberId": phone_number_id,
                "participants": [participant],
                "maxResults": 50,
                "createdAfter": since.isoformat() + "Z",
            },
            timeout=15,
        )
        if r.status_code != 200:
            return []
        return [
            m for m in r.json().get("data", [])
            if m.get("direction") == "incoming"
        ]
    except Exception as e:
        print(f"[quo-poll] Error fetching messages for {participant}: {e}")
        return []


def _get_contacts_with_phones() -> list[str]:
    """Return all phone numbers for tenants and vendors."""
    db = SessionLocal()
    try:
        from db.models import Tenant, User
        phones: list[str] = []
        for t in db.query(Tenant).join(User, Tenant.user_id == User.id).filter(User.phone.isnot(None)).all():
            if t.user and t.user.phone:
                phones.append(t.user.phone)
        for v in db.query(User).filter(User.user_type == "vendor", User.phone.isnot(None)).all():
            if v.phone:
                phones.append(v.phone)
        return phones
    finally:
        db.close()


def _is_duplicate(body: str, sent_at_iso: str | None) -> bool:
    """Check if we already recorded a message with same body near the same time."""
    if not sent_at_iso:
        return False
    db = SessionLocal()
    try:
        from dateutil.parser import isoparse

        from db.models import Message
        msg_time = isoparse(sent_at_iso)
        window = timedelta(seconds=10)
        return db.query(Message).filter(
            Message.body == body,
            Message.sent_at >= msg_time - window,
            Message.sent_at <= msg_time + window,
        ).first() is not None
    except Exception:
        return False
    finally:
        db.close()


async def poll_quo_messages():
    """Single poll cycle: fetch new inbound messages from Quo for all known contacts."""
    global _last_poll

    api_key, from_number = _get_config()
    if not api_key:
        return

    since = _last_poll or (datetime.now(UTC) - timedelta(minutes=30))
    now = datetime.now(UTC)

    phones = _get_contacts_with_phones()
    if not phones:
        _last_poll = now
        return

    async with httpx.AsyncClient() as client:
        pn_ids = await _get_phone_number_ids(client, api_key)
        if not pn_ids:
            _last_poll = now
            return

        processed = 0
        for phone in phones:
            for pn_id in pn_ids:
                messages = await _fetch_messages(client, api_key, pn_id, phone, since)
                for msg in messages:
                    body = msg.get("body") or msg.get("text", "")
                    if not body:
                        continue

                    # Deduplicate against messages already in DB (from webhook or prior poll)
                    if _is_duplicate(body, msg.get("createdAt")):
                        continue

                    from_num = msg.get("from", phone)
                    to_raw = msg.get("to", [])
                    to_num = to_raw[0] if isinstance(to_raw, list) and to_raw else from_number

                    db = SessionLocal()
                    try:
                        from handlers.chat import process_inbound_sms
                        ok = await process_inbound_sms(db, from_num, to_num, body)
                        if ok:
                            processed += 1
                    except Exception as e:
                        print(f"[quo-poll] Error processing message from {phone}: {e}")
                        import traceback
                        traceback.print_exc()
                    finally:
                        db.close()

            # Stagger between contacts to be polite to the API
            await asyncio.sleep(0.5)

    _last_poll = now
    if processed:
        print(f"[quo-poll] Processed {processed} new message(s)")


async def quo_poll_loop():
    """Background loop that polls Quo for new messages."""
    is_prod = _is_production()
    interval = 15 * 60 if is_prod else 5 * 60
    label = "production (backup)" if is_prod else "local (primary)"
    print(f"[quo-poll] Starting poller — {label}, interval={interval}s")

    # Wait for server to fully start
    await asyncio.sleep(30)

    while True:
        try:
            await poll_quo_messages()
        except Exception as e:
            print(f"[quo-poll] Poll cycle failed: {e}")
            import traceback
            traceback.print_exc()

        if is_prod:
            await asyncio.sleep(interval + random.randint(0, 120))
        else:
            await asyncio.sleep(interval)
