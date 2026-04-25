from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backends.local_auth import resolve_account_id
from gql.services.notification_service import NotificationService
from handlers.deps import get_db, require_user

router = APIRouter()


class NotificationResponse(BaseModel):
    uid: str
    kind: str
    channel: str
    delivery_status: str
    title: str
    body: str | None
    task_id: int | None
    conversation_id: int | None
    created_at: datetime
    read_at: datetime | None
    archived_at: datetime | None
    sent_at: datetime | None
    failed_at: datetime | None
    failure_reason: str | None
    extra: dict | None


def _to_response(notification) -> NotificationResponse:
    return NotificationResponse(
        uid=str(notification.external_id),
        kind=notification.kind,
        channel=notification.channel,
        delivery_status=notification.delivery_status,
        title=notification.title,
        body=notification.body,
        task_id=notification.task_id,
        conversation_id=notification.conversation_id,
        created_at=notification.created_at,
        read_at=notification.read_at,
        archived_at=notification.archived_at,
        sent_at=notification.sent_at,
        failed_at=notification.failed_at,
        failure_reason=notification.failure_reason,
        extra=notification.extra,
    )


@router.get("/notifications", response_model=list[NotificationResponse])
async def list_notifications(
    request: Request,
    include_archived: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    await require_user(request)
    rows = NotificationService.list_for_user(
        db,
        recipient_user_id=resolve_account_id(),
        include_archived=include_archived,
    )
    return [_to_response(row) for row in rows]


@router.post("/notifications/{notification_id}/read", response_model=NotificationResponse)
async def mark_notification_read(
    notification_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    await require_user(request)
    row = NotificationService.mark_read(
        db,
        external_id=notification_id,
        recipient_user_id=resolve_account_id(),
        read=True,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    db.commit()
    return _to_response(row)


@router.post("/notifications/{notification_id}/unread", response_model=NotificationResponse)
async def mark_notification_unread(
    notification_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    await require_user(request)
    row = NotificationService.mark_read(
        db,
        external_id=notification_id,
        recipient_user_id=resolve_account_id(),
        read=False,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    db.commit()
    return _to_response(row)


@router.post("/notifications/{notification_id}/archive", response_model=NotificationResponse)
async def archive_notification(
    notification_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    await require_user(request)
    row = NotificationService.archive(
        db,
        external_id=notification_id,
        recipient_user_id=resolve_account_id(),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    db.commit()
    return _to_response(row)
