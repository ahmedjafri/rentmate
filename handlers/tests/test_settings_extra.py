"""Extended tests for handlers/settings.py — env file helpers and integrations endpoint."""
import os
import unittest
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

DEFAULT_USER_ID = "1"  # test-only JWT sub claim
from handlers.deps import get_db
from handlers.settings import _mask_integrations
from main import app


def make_token():
    import jwt
    return jwt.encode(
        {"sub": DEFAULT_USER_ID, "email": "admin@localhost"},
        os.getenv("JWT_SECRET", "rentmate-local-secret"),
        algorithm="HS256",
    )


AUTH = {"Authorization": f"Bearer {make_token()}"}


# ---------------------------------------------------------------------------
# _mask_integrations
# ---------------------------------------------------------------------------

class TestMaskIntegrations:
    def test_masks_token_field(self):
        stored = {"telegram": {"enabled": True, "token": "secret123"}}
        result = _mask_integrations(stored)
        assert result["telegram"]["token"] == "\u2022" * 8
        assert result["telegram"]["enabled"] is True

    def test_masks_bridge_token(self):
        stored = {"whatsapp": {"enabled": True, "bridge_token": "wha-secret"}}
        result = _mask_integrations(stored)
        assert result["whatsapp"]["bridge_token"] == "\u2022" * 8

    def test_missing_channel_returns_empty_dict(self):
        result = _mask_integrations({})
        assert result["telegram"] == {}
        assert result["whatsapp"] == {}


# ---------------------------------------------------------------------------
# Integrations endpoint tests
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("db")
class TestIntegrationsEndpoint(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        app.dependency_overrides[get_db] = lambda: self.db

    def tearDown(self):
        app.dependency_overrides = {}

    def test_get_integrations_requires_auth(self):
        response = self.client.get("/settings/integrations")
        assert response.status_code == 401

    def test_get_integrations_returns_masked_secrets(self):
        stored = {"telegram": {"enabled": True, "token": "super-secret"}}
        with patch("handlers.settings.load_integrations", return_value=stored):
            response = self.client.get("/settings/integrations", headers=AUTH)
        assert response.status_code == 200
        assert response.json()["telegram"]["token"] == "\u2022" * 8

    def test_post_integrations_requires_auth(self):
        response = self.client.post("/settings/integrations", json={})
        assert response.status_code == 401

    def test_post_integrations_saves_and_restarts_channels(self):
        with (
            patch("handlers.settings.load_integrations", return_value={}),
            patch("handlers.settings.save_integrations") as mock_save,
            patch("llm.registry.agent_registry.restart_channels_async", new_callable=AsyncMock) as mock_restart,
        ):
            response = self.client.post(
                "/settings/integrations",
                json={"telegram": {"enabled": True, "token": "tok123"}},
                headers=AUTH,
            )
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        mock_save.assert_called_once()
        mock_restart.assert_called_once()

    def test_post_integrations_does_not_overwrite_blank_token(self):
        stored = {"telegram": {"enabled": True, "token": "existing-secret"}}
        saved = {}

        def capture_save(data):
            saved.update(data)

        with (
            patch("handlers.settings.load_integrations", return_value=stored),
            patch("handlers.settings.save_integrations", side_effect=capture_save),
            patch("llm.registry.agent_registry.restart_channels_async", new_callable=AsyncMock),
        ):
            self.client.post(
                "/settings/integrations",
                json={"telegram": {"enabled": False, "token": ""}},  # blank token
                headers=AUTH,
            )
        # Token should be preserved
        assert saved.get("telegram", {}).get("token") == "existing-secret"

    def test_post_settings_autonomy(self):
        with (
            patch("handlers.settings.load_app_settings", return_value={}),
            patch("handlers.settings.save_app_settings") as mock_save,
        ):
            response = self.client.post(
                "/settings",
                json={"autonomy": {"rent": "autonomous", "maintenance": "suggest"}},
                headers=AUTH,
            )
        assert response.status_code == 200
        saved_arg = mock_save.call_args[0][0]
        assert saved_arg["autonomy"]["rent"] == "autonomous"

    def test_post_settings_base_url(self):
        with patch("llm.llm.reconfigure"):
            response = self.client.post(
                "/settings",
                json={"base_url": "http://localhost:11434", "model": "ollama/llama3"},
                headers=AUTH,
            )
        assert response.status_code == 200
        assert os.environ.get("LLM_BASE_URL") == "http://localhost:11434"
