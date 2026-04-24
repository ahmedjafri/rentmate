"""Notification service — deliver structured messages to tenants/vendors.

SMS is the only channel implemented today; email/push/webhook can be added here
without touching call sites. An SMS notification carries a short blurb, any
quoted messages, and a login-less portal link that deep-links to the
conversation being notified about.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field

import httpx
from sqlalchemy.orm import Session

from db.models import Conversation, User
from gql.services import portal_auth
from gql.services.settings_service import get_integrations

logger = logging.getLogger("rentmate.notifications")

QUO_API_KEY_ENV = os.getenv("QUO_API_KEY", "")


@dataclass
class Notification:
    """A notification to be delivered to a user.

    `blurb` is a short header shown first (e.g. "New update on your task");
    `messages` are any quoted bodies that the recipient should see; the
    conversation deep-link is appended automatically.
    """
    recipient_user_id: int
    conversation_id: int
    blurb: str
    messages: list[str] = field(default_factory=list)


class NotificationService:
    @staticmethod
    async def notify(sess: Session, notification: Notification) -> bool:
        """Deliver the notification. Awaitable; failures are swallowed."""
        for phone, body in NotificationService._resolve_deliveries(sess, notification):
            try:
                await NotificationService._send_sms(phone, body)
                return True
            except Exception:
                logger.exception("SMS delivery failed for user %s", notification.recipient_user_id)
        return False

    @staticmethod
    def dispatch(sess: Session, notification: Notification) -> None:
        """Fire-and-forget: resolve recipient + body synchronously, then
        schedule the SMS call on the running event loop. Safe to call from
        request handlers that will close the session before the SMS completes.
        """
        for phone, body in NotificationService._resolve_deliveries(sess, notification):
            try:
                loop = asyncio.get_event_loop()
                loop.create_task(NotificationService._send_sms(phone, body))
            except RuntimeError:
                logger.warning("no running event loop — notification dropped")

    @staticmethod
    def _resolve_deliveries(
        sess: Session, notification: Notification,
    ) -> list[tuple[str, str]]:
        """Return (phone, body) pairs for each channel the recipient supports."""
        recipient = sess.get(User, notification.recipient_user_id)
        if recipient is None:
            logger.warning("notification recipient %s not found", notification.recipient_user_id)
            return []
        conversation = sess.get(Conversation, notification.conversation_id)
        if conversation is None:
            logger.warning("notification conversation %s not found", notification.conversation_id)
            return []

        deliveries: list[tuple[str, str]] = []
        if recipient.phone:
            portal_url = NotificationService._portal_conversation_url(sess, recipient, conversation)
            body = NotificationService._format_sms(notification, portal_url)
            deliveries.append((recipient.phone, body))
        return deliveries

    # ── Formatting ────────────────────────────────────────────────────────

    @staticmethod
    def _format_sms(notification: Notification, portal_url: str) -> str:
        parts: list[str] = [notification.blurb]
        parts.extend(m for m in notification.messages if m)
        if portal_url:
            parts.append(f"Open: {portal_url}")
        return "\n\n".join(parts)

    # ── Recipient-type dispatch ──────────────────────────────────────────

    @staticmethod
    def _portal_conversation_url(sess: Session, user: User, conversation: Conversation) -> str:
        """Build a login-less deep-link for the recipient to view this conversation."""
        token = NotificationService._ensure_portal_token(sess, user)
        if not token or not conversation.external_id:
            return ""
        return portal_auth.build_portal_conversation_url(token, conversation.external_id)

    @staticmethod
    def _ensure_portal_token(sess: Session, user: User) -> str:
        user_type = (user.user_type or "").lower()
        if user_type == "tenant":
            from db.models import Tenant
            from gql.services.tenant_service import TenantService
            tenant = sess.query(Tenant).filter_by(user_id=user.id).first()
            if tenant is None:
                return ""
            return TenantService.ensure_portal_token(sess, tenant)
        if user_type == "vendor":
            from gql.services.vendor_service import VendorService
            return VendorService.ensure_portal_token(sess, user)
        return ""

    # ── SMS channel ──────────────────────────────────────────────────────

    @staticmethod
    def _normalize_phone(num: str) -> str:
        digits = "".join(c for c in num if c.isdigit())
        if digits.startswith("1") and len(digits) == 11:
            return f"+{digits}"
        if len(digits) == 10:
            return f"+1{digits}"
        if num.startswith("+"):
            return num
        return f"+{digits}"

    @staticmethod
    def _quo_credentials() -> tuple[str, str]:
        cfg = get_integrations().get("quo", {})
        api_key = cfg.get("api_key") or QUO_API_KEY_ENV
        from_number = cfg.get("from_number") or ""
        return api_key, from_number

    @staticmethod
    async def _send_sms(to_phone: str, body: str) -> None:
        api_key, from_number = NotificationService._quo_credentials()
        if not api_key:
            logger.info("no Quo API key configured — SMS skipped")
            return
        to = NotificationService._normalize_phone(to_phone)
        sender = NotificationService._normalize_phone(from_number) if from_number else ""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.openphone.com/v1/messages",
                headers={
                    "Authorization": api_key,
                    "Content-Type": "application/json",
                },
                json={"content": body, "from": sender, "to": [to]},
            )
            logger.info("Quo response: %s %s", response.status_code, response.text[:200])

