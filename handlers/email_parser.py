"""Parse a Postmark Inbound webhook payload into a clean, typed ParsedEmail.

This module is intentionally dependency-free (no DB, no FastAPI) so it can be
unit tested without any infrastructure running.  All the messy header scraping
and RFC 5322 normalisation lives here so the rest of the codebase only ever
sees clean Python dataclasses.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Optional


# ─── Data container ───────────────────────────────────────────────────────────

@dataclass
class ParsedEmail:
    """Everything we care about from one inbound email.

    ``thread_id`` is the stable, cross-reply identifier for the whole email
    chain.  All emails in the same thread will share the same thread_id and
    will be collapsed into a single Conversation row in the DB.
    """
    from_email: str
    from_name: str
    to_emails: list[str]
    cc_emails: list[str]
    subject: str
    body_text: str
    body_html: str
    # Normalised RFC Message-ID (angle brackets stripped). Used as the
    # per-message dedup key — if we already have this in Message.meta, skip.
    message_id: str
    # The Message-ID this email is replying to (stripped). None for new threads.
    in_reply_to: Optional[str]
    # All prior Message-IDs in the thread from the References header.
    references: list[str]
    # Stable thread anchor — first element of References, or In-Reply-To, or
    # the email's own Message-ID.  This is what we key Conversation rows on.
    thread_id: str
    # List of attachment dicts: {name, content_type, content_base64, size_bytes}
    attachments: list[dict]
    # Actual send time from the email headers / Postmark metadata.
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))


# ─── Public entry point ───────────────────────────────────────────────────────

def parse_postmark_payload(data: dict) -> ParsedEmail:
    """Convert a raw Postmark Inbound JSON dict into a ParsedEmail.

    Postmark's inbound schema reference:
    https://postmarkapp.com/developer/webhooks/inbound-webhook
    """
    from_full = data.get("FromFull") or {}
    from_email = (from_full.get("Email") or data.get("From") or "").strip().lower()
    from_name = (from_full.get("Name") or "").strip()

    to_emails = [
        r["Email"].strip().lower()
        for r in (data.get("ToFull") or [])
        if r.get("Email")
    ]
    cc_emails = [
        r["Email"].strip().lower()
        for r in (data.get("CcFull") or [])
        if r.get("Email")
    ]

    subject = (data.get("Subject") or "").strip()
    body_text = (data.get("TextBody") or "").strip()
    body_html = (data.get("HtmlBody") or "").strip()

    # Parse the Headers array into a lookup dict for easy access.
    # Postmark always includes it but it can be empty on stripped messages.
    headers = _headers_to_dict(data.get("Headers") or [])

    # IMPORTANT: Postmark's top-level "MessageID" field is Postmark's own internal
    # tracking ID, NOT the RFC 5322 Message-ID from the email headers.  Other emails'
    # References and In-Reply-To fields always point to the RFC Message-ID from the
    # email headers, so we must use the header value for threading to work correctly.
    # Postmark's internal ID is kept as a last-resort fallback so deduplication
    # always has a stable key — even if the RFC header is somehow absent.
    raw_message_id = headers.get("message-id") or data.get("MessageID") or ""
    message_id = _strip_angle_brackets(raw_message_id)
    # If both RFC header and Postmark's internal ID are absent (essentially
    # impossible in practice), generate a stable content hash so deduplication
    # still works on Postmark retry storms.
    if not message_id:
        fingerprint = f"{from_email}|{subject}|{body_text[:200]}"
        message_id = "hash-" + hashlib.sha256(fingerprint.encode()).hexdigest()[:32]

    raw_in_reply_to = headers.get("in-reply-to") or ""
    in_reply_to = _strip_angle_brackets(raw_in_reply_to) or None

    raw_references = headers.get("references") or ""
    references = [
        _strip_angle_brackets(r)
        for r in raw_references.split()
        if r.strip()
    ]

    thread_id = _extract_thread_id(message_id, in_reply_to, references)

    # Parse Postmark's attachment objects into a simpler dict we store in JSON.
    attachments = [
        {
            "name": att.get("Name") or "",
            "content_type": att.get("ContentType") or "application/octet-stream",
            "content_base64": att.get("Content") or "",
            "size_bytes": att.get("ContentLength") or 0,
        }
        for att in (data.get("Attachments") or [])
    ]

    # Parse the Date header if present; fall back to current time.
    received_at = _parse_date(data.get("Date") or "")

    return ParsedEmail(
        from_email=from_email,
        from_name=from_name,
        to_emails=to_emails,
        cc_emails=cc_emails,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        message_id=message_id,
        in_reply_to=in_reply_to,
        references=references,
        thread_id=thread_id,
        attachments=attachments,
        received_at=received_at,
    )


# ─── Internal helpers ─────────────────────────────────────────────────────────

def _extract_thread_id(
    message_id: str,
    in_reply_to: Optional[str],
    references: list[str],
) -> str:
    """Compute the stable thread anchor for an email chain.

    Email threading works by following the References header backward to the
    very first email in the conversation.  We use that original Message-ID as
    the thread key so every reply in the chain maps to the same Conversation.

    Priority:
    1. First element of References (the original email that started the thread)
    2. In-Reply-To (direct parent — still part of an existing thread)
    3. Our own Message-ID (this email starts a brand new thread)
    """
    if references:
        return references[0]
    if in_reply_to:
        return in_reply_to
    return message_id


def _strip_angle_brackets(value: str) -> str:
    """Normalise RFC 5322 message-id format: strip surrounding < and >.

    Gmail sends ``<abc123@mail.gmail.com>``; we store ``abc123@mail.gmail.com``
    so comparisons work regardless of how the sender's MTA formats it.
    """
    return value.strip().lstrip("<").rstrip(">").strip()


def _headers_to_dict(headers: list[dict]) -> dict[str, str]:
    """Flatten Postmark's ``[{"Name": "...", "Value": "..."}]`` into a plain dict.

    Uses lowercase keys so lookups are case-insensitive.
    """
    result: dict[str, str] = {}
    for h in headers:
        name = (h.get("Name") or "").lower()
        value = (h.get("Value") or "").strip()
        if name:
            result[name] = value
    return result


def _parse_date(value: str) -> datetime:
    """Parse an RFC 2822 date string from the email headers.

    Falls back to UTC now if the string is missing or unparseable.  We use
    dateutil.parser which handles the many date formats email clients produce.
    """
    if not value:
        return datetime.now(UTC)
    try:
        from dateutil.parser import parse as dateutil_parse
        dt = dateutil_parse(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except Exception:
        return datetime.now(UTC)
