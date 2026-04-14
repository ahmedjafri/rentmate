"""Extended tests for handlers/settings.py — env file helpers and integrations endpoints."""
import os
import unittest
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backends.local_auth import get_org_external_id, set_request_context
from handlers.deps import get_db
from handlers.settings import _list_agent_workspace_files, _mask_integrations
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
        self.require_user_patcher = patch("handlers.settings.require_user", side_effect=_fake_require_user)
        self.require_user_patcher.start()

    def tearDown(self):
        self.require_user_patcher.stop()
        app.dependency_overrides = {}

    def test_get_integrations_requires_auth(self):
        response = self.client.get("/api/settings/integrations")
        assert response.status_code == 401

    def test_get_integrations_returns_masked_secrets(self):
        stored = {"telegram": {"enabled": True, "token": "super-secret"}}
        with patch("handlers.settings.load_integrations", return_value=stored):
            response = self.client.get("/api/settings/integrations", headers=AUTH)
        assert response.status_code == 200
        assert response.json()["telegram"]["token"] == "\u2022" * 8

    def test_post_integrations_requires_auth(self):
        response = self.client.post("/api/settings/integrations", json={})
        assert response.status_code == 401

    def test_post_integrations_saves_and_restarts_channels(self):
        with (
            patch("handlers.settings.load_integrations", return_value={}),
            patch("handlers.settings.save_integrations") as mock_save,
            patch("llm.registry.agent_registry.restart_channels_async", new_callable=AsyncMock) as mock_restart,
        ):
            response = self.client.post(
                "/api/settings/integrations",
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
                "/api/settings/integrations",
                json={"telegram": {"enabled": False, "token": ""}},  # blank token
                headers=AUTH,
            )
        # Token should be preserved
        assert saved.get("telegram", {}).get("token") == "existing-secret"

    def test_post_settings_action_policy(self):
        with (
            patch("handlers.settings.save_action_policy_settings") as mock_save,
        ):
            response = self.client.post(
                "/api/settings",
                json={"action_policy": {"entity_changes": "aggressive", "outbound_messages": "strict"}},
                headers=AUTH,
            )
        assert response.status_code == 200
        saved_arg = mock_save.call_args[0][0]
        assert saved_arg["entity_changes"] == "aggressive"

    def test_post_settings_base_url(self):
        with (
            patch("llm.llm.reconfigure"),
            patch("handlers.settings.save_llm_settings"),
        ):
            response = self.client.post(
                "/api/settings",
                json={"base_url": "http://localhost:11434", "model": "ollama/llama3"},
                headers=AUTH,
            )
        assert response.status_code == 200
        assert os.environ.get("LLM_BASE_URL") == "http://localhost:11434"


class TestAgentWorkspaceFiles:
    def test_lists_all_workspace_files_and_marks_unknown_files_readonly(self, tmp_path):
        (tmp_path / "SOUL.md").write_text("soul")
        (tmp_path / "memory").mkdir()
        (tmp_path / "memory" / "MEMORY.md").write_text("memory")
        (tmp_path / "home").mkdir()
        (tmp_path / "home" / ".gitconfig").write_text("[user]\nname = test\n")

        files = _list_agent_workspace_files(tmp_path)
        by_name = {entry["filename"]: entry for entry in files}

        assert by_name["SOUL.md"]["readonly"] is True
        assert by_name["memory/MEMORY.md"]["readonly"] is False
        assert by_name["home/.gitconfig"]["readonly"] is True
