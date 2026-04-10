"""Tests for the /settings GET and POST endpoints."""

import os
import unittest
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

DEFAULT_USER_ID = "1"  # test-only JWT sub claim
from handlers.deps import get_db
from main import app


def make_token():
    import jwt
    return jwt.encode(
        {"sub": DEFAULT_USER_ID, "email": "admin@localhost"},
        os.getenv("JWT_SECRET", "rentmate-local-secret"),
        algorithm="HS256",
    )


@pytest.mark.usefixtures("db")
class TestSettingsEndpoint(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)
        app.dependency_overrides[get_db] = lambda: self.db

    def tearDown(self):
        app.dependency_overrides = {}

    # ------------------------------------------------------------------
    # Auth guards
    # ------------------------------------------------------------------

    def test_get_settings_requires_auth(self):
        response = self.client.get("/settings")
        self.assertEqual(response.status_code, 401)

    def test_post_settings_requires_auth(self):
        response = self.client.post("/settings", json={"model": "openai/gpt-4o"})
        self.assertEqual(response.status_code, 401)

    # ------------------------------------------------------------------
    # GET /settings
    # ------------------------------------------------------------------

    def test_get_settings_returns_llm_status(self):
        with (
            patch.dict(os.environ, {"LLM_API_KEY": "sk-test", "LLM_MODEL": "openai/gpt-4o-mini"}),
            patch("handlers.settings.get_llm_settings", return_value={"api_key": "sk-test", "model": "openai/gpt-4o-mini", "base_url": ""}),
        ):
            response = self.client.get(
                "/settings", headers={"Authorization": f"Bearer {make_token()}"}
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        # API key is masked (bullets if set, empty if not)
        self.assertIn(data["api_key"], ("", "\u2022" * 8))
        self.assertEqual(data["model"], "openai/gpt-4o-mini")
        self.assertIn("autonomy", data)

    def test_get_settings_api_key_not_set(self):
        env = {k: v for k, v in os.environ.items() if k != "LLM_API_KEY"}
        with (
            patch.dict(os.environ, env, clear=True),
            patch("handlers.settings.get_llm_settings", return_value={"api_key": "", "model": "openai/gpt-4o-mini", "base_url": ""}),
        ):
            response = self.client.get(
                "/settings", headers={"Authorization": f"Bearer {make_token()}"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["api_key"], "")  # always empty

    # ------------------------------------------------------------------
    # POST /settings
    # ------------------------------------------------------------------

    @patch("llm.llm.reconfigure")
    def test_post_settings_updates_env_and_reconfigures(self, mock_reconf):
        response = self.client.post(
            "/settings",
            json={"api_key": "sk-new", "model": "openai/gpt-4o"},
            headers={"Authorization": f"Bearer {make_token()}"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        self.assertEqual(os.environ.get("LLM_API_KEY"), "sk-new")
        self.assertEqual(os.environ.get("LLM_MODEL"), "openai/gpt-4o")
        mock_reconf.assert_called_once()

    def test_post_settings_no_body_returns_ok(self):
        response = self.client.post(
            "/settings",
            json={},
            headers={"Authorization": f"Bearer {make_token()}"},
        )
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
