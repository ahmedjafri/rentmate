"""Persisted notification service for PM, tenant, and vendor recipients."""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.orm import Session

from backends.local_auth import resolve_account_id, resolve_org_id
from db.models import Conversation, Notification, Task, User
from db.session import SessionLocal
from gql.services import portal_auth
from gql.services.settings_service import get_integrations

logger = logging.getLogger("rentmate.notifications")

QUO_API_KEY_ENV = os.getenv("QUO_API_KEY", "")


@dataclass
class NotificationRequest:
    """Request to create and optionally deliver a notification."""

    recipient_user_id: int
    title: str
    body: str | None = None
    conversation_id: int | None = None
    task_id: int | None = None
    kind: str = "generic"
    channel: str | None = None
    messages: list[str] = field(default_factory=list)
    extra: dict[str, Any] | None = None


class NotificationService:
    @staticmethod
    async def notify(sess: Session, notification: NotificationRequest) -> Notification:
        """Persist and deliver a notification synchronously in this request."""
        row = NotificationService.create(sess, notification)
        phone, sms_body = NotificationService._resolve_sms_delivery(sess, row)
        if not phone or not sms_body:
            row.delivery_status = "skipped"
            sess.flush()
            return row

        try:
            await NotificationService._send_sms(phone, sms_body)
            row.delivery_status = "sent"
            row.sent_at = datetime.now(UTC)
            row.failed_at = None
            row.failure_reason = None
        except Exception as exc:
            row.delivery_status = "failed"
            row.failed_at = datetime.now(UTC)
            row.failure_reason = str(exc)[:1000]
            logger.exception("SMS delivery failed for notification %s", row.id)
        sess.flush()
        return row

    @staticmethod
    def dispatch(sess: Session, notification: NotificationRequest) -> Notification:
        """Persist now, deliver asynchronously on a fresh session if possible."""
        row = NotificationService.create(sess, notification)
        if row.channel != "sms":
            return row
        try:
            loop = asyncio.get_event_loop()
            loop.create_task(NotificationService._deliver_in_background(row.id))
        except RuntimeError:
            logger.warning("no running event loop — notification delivery deferred")
        return row

    @staticmethod
    def create(sess: Session, notification: NotificationRequest) -> Notification:
        """Persist the canonical notification record."""
        now = datetime.now(UTC)
        row = Notification(
            org_id=resolve_org_id(),
            creator_id=resolve_account_id(),
            recipient_user_id=notification.recipient_user_id,
            task_id=notification.task_id,
            conversation_id=notification.conversation_id,
            kind=notification.kind,
            channel=notification.channel or NotificationService._default_channel(sess, notification.recipient_user_id),
            delivery_status="pending",
            title=notification.title,
            body=notification.body,
            extra={
                "messages": notification.messages,
                **(notification.extra or {}),
            } if notification.messages or notification.extra else None,
            created_at=now,
        )
        if row.channel == "in_app":
            row.delivery_status = "recorded"
        sess.add(row)
        sess.flush()
        return row

    @staticmethod
    def list_for_user(sess: Session, *, recipient_user_id: int, include_archived: bool = False) -> list[Notification]:
        query = (
            sess.query(Notification)
            .filter(
                Notification.org_id == resolve_org_id(),
                Notification.recipient_user_id == recipient_user_id,
            )
            .order_by(Notification.created_at.desc(), Notification.id.desc())
        )
        if not include_archived:
            query = query.filter(Notification.archived_at.is_(None))
        return query.all()

    @staticmethod
    def mark_read(sess: Session, *, external_id: str, recipient_user_id: int, read: bool) -> Notification | None:
        row = NotificationService._for_recipient(sess, external_id=external_id, recipient_user_id=recipient_user_id)
        if not row:
            return None
        row.read_at = datetime.now(UTC) if read else None
        sess.flush()
        return row

    @staticmethod
    def archive(sess: Session, *, external_id: str, recipient_user_id: int) -> Notification | None:
        row = NotificationService._for_recipient(sess, external_id=external_id, recipient_user_id=recipient_user_id)
        if not row:
            return None
        row.archived_at = datetime.now(UTC)
        sess.flush()
        return row

    @staticmethod
    def _for_recipient(sess: Session, *, external_id: str, recipient_user_id: int) -> Notification | None:
        return (
            sess.query(Notification)
            .filter(
                Notification.org_id == resolve_org_id(),
                Notification.external_id == str(external_id),
                Notification.recipient_user_id == recipient_user_id,
            )
            .first()
        )

    @staticmethod
    def _default_channel(sess: Session, recipient_user_id: int) -> str:
        recipient = sess.get(User, recipient_user_id)
        if recipient and (recipient.user_type or "").lower() in {"tenant", "vendor"}:
            return "sms"
        return "in_app"

    @staticmethod
    async def _deliver_in_background(notification_id: int) -> None:
        sess = SessionLocal()
        try:
            row = (
                sess.query(Notification)
                .filter(Notification.id == notification_id)
                .first()
            )
            if row is None or row.channel != "sms" or row.archived_at is not None:
                return
            phone, sms_body = NotificationService._resolve_sms_delivery(sess, row)
            if not phone or not sms_body:
                row.delivery_status = "skipped"
                sess.commit()
                return
            try:
                await NotificationService._send_sms(phone, sms_body)
                row.delivery_status = "sent"
                row.sent_at = datetime.now(UTC)
                row.failed_at = None
                row.failure_reason = None
            except Exception as exc:
                row.delivery_status = "failed"
                row.failed_at = datetime.now(UTC)
                row.failure_reason = str(exc)[:1000]
                logger.exception("background SMS delivery failed for notification %s", row.id)
            sess.commit()
        finally:
            sess.close()

    @staticmethod
    def _resolve_sms_delivery(sess: Session, notification: Notification) -> tuple[str | None, str | None]:
        recipient = sess.get(User, notification.recipient_user_id)
        if recipient is None:
            logger.warning("notification recipient %s not found", notification.recipient_user_id)
            return None, None
        if not recipient.phone:
            return None, None
        portal_url = ""
        if notification.conversation_id:
            conversation = sess.get(Conversation, notification.conversation_id)
            if conversation is None:
                logger.warning("notification conversation %s not found", notification.conversation_id)
                return None, None
            portal_url = NotificationService._portal_conversation_url(sess, recipient, conversation)
        body = NotificationService._format_sms(notification, portal_url)
        return recipient.phone, body

    @staticmethod
    def _format_sms(notification: Notification, portal_url: str) -> str:
        parts: list[str] = [notification.title]
        if notification.body:
            parts.append(notification.body)
        messages = ((notification.extra or {}).get("messages") or [])
        parts.extend(m for m in messages if m)
        if portal_url:
            parts.append(f"Open: {portal_url}")
        return "\n\n".join(parts)

    @staticmethod
    def _portal_conversation_url(sess: Session, user: User, conversation: Conversation) -> str:
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

    @staticmethod
    def _quo_credentials() -> tuple[str, str]:
        cfg = get_integrations().get("quo", {})
        api_key = cfg.get("api_key") or QUO_API_KEY_ENV
        from_number = cfg.get("from_number") or ""
        return api_key, from_number

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
