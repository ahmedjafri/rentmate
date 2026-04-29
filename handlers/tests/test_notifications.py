import os
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backends.local_auth import get_org_external_id, set_request_context
from db.models import Notification, User
from gql.services.notification_service import NotificationRequest, NotificationService
from handlers.deps import get_db
from main import app


def make_token():
    import jwt

    return jwt.encode(
        {"sub": "1", "uid": "1", "org_uid": get_org_external_id(), "email": "admin@localhost"},
        os.getenv("JWT_SECRET", "rentmate-local-secret"),
        algorithm="HS256",
    )


AUTH = {"Authorization": f"Bearer {make_token()}"}


async def _fake_require_user(request):
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.replace("Bearer ", "").strip():
        raise HTTPException(status_code=401, detail="Not authenticated")
    set_request_context(account_id=1, org_id=1)
    return {"account_id": 1, "org_id": 1, "uid": "1", "email": "admin@localhost"}


@pytest.mark.usefixtures("db")
class TestNotificationsHandler(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        app.dependency_overrides[get_db] = lambda: self.db
        self.require_user_patcher = patch("handlers.notifications.require_user", side_effect=_fake_require_user)
        self.require_user_patcher.start()
        self.pm_user = self.db.get(User, 1)
        if self.pm_user is None:
            self.pm_user = User(id=1, org_id=1, email="pm@example.com", active=True, user_type="account")
            self.db.add(self.pm_user)
            self.db.flush()

    def tearDown(self):
        self.require_user_patcher.stop()
        app.dependency_overrides = {}

    def test_lists_notifications_for_current_pm(self):
        NotificationService.create(
            self.db,
            NotificationRequest(
                recipient_user_id=1,
                title="Task needs your input",
                body="Which quote should I accept?",
                kind="manager_attention",
                channel="in_app",
                extra={"message_id": "42"},
            ),
        )
        self.db.commit()

        response = self.client.get("/api/notifications", headers=AUTH)

        assert response.status_code == 200
        payload = response.json()
        assert len(payload) == 1
        assert payload[0]["kind"] == "manager_attention"
        assert payload[0]["title"] == "Task needs your input"
        assert payload[0]["message_id"] == "42"

    def test_can_mark_read_and_archive_notification(self):
        row = NotificationService.create(
            self.db,
            NotificationRequest(
                recipient_user_id=1,
                title="Task needs your input",
                kind="manager_attention",
                channel="in_app",
            ),
        )
        self.db.commit()

        read_response = self.client.post(f"/api/notifications/{row.external_id}/read", headers=AUTH)
        assert read_response.status_code == 200
        assert read_response.json()["read_at"] is not None

        archive_response = self.client.post(f"/api/notifications/{row.external_id}/archive", headers=AUTH)
        assert archive_response.status_code == 200
        assert archive_response.json()["archived_at"] is not None

        listing = self.client.get("/api/notifications", headers=AUTH)
        assert listing.status_code == 200
        assert listing.json() == []

        archived = self.client.get("/api/notifications?include_archived=true", headers=AUTH)
        assert archived.status_code == 200
        assert len(archived.json()) == 1

    def test_cannot_access_other_users_notification(self):
        other_user = User(org_id=1, email="other@example.com", active=True, user_type="account")
        self.db.add(other_user)
        self.db.flush()
        row = Notification(
            org_id=1,
            creator_id=1,
            recipient_user_id=other_user.id,
            kind="manager_attention",
            channel="in_app",
            delivery_status="recorded",
            title="Other PM",
            created_at=datetime.now(UTC),
        )
        self.db.add(row)
        self.db.commit()

        response = self.client.post(f"/api/notifications/{row.external_id}/read", headers=AUTH)
        assert response.status_code == 404
