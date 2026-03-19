"""Tests for handlers/auth.py login endpoint."""
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from main import app


@pytest.fixture
def client():
    return TestClient(app)


class TestLoginEndpoint:
    def test_login_success(self, client):
        with patch("handlers.auth.auth_backend") as mock_backend:
            mock_backend.login = AsyncMock(return_value="fake-jwt-token")
            response = client.post("/auth/login", json={"password": "correct"})
        assert response.status_code == 200
        assert response.json()["access_token"] == "fake-jwt-token"

    def test_login_wrong_password(self, client):
        with patch("handlers.auth.auth_backend") as mock_backend:
            mock_backend.login = AsyncMock(side_effect=ValueError("bad password"))
            response = client.post("/auth/login", json={"password": "wrong"})
        assert response.status_code == 401
        assert "Invalid password" in response.json()["detail"]

    def test_login_missing_password_field(self, client):
        response = client.post("/auth/login", json={})
        assert response.status_code == 422

    def test_login_returns_token_key(self, client):
        with patch("handlers.auth.auth_backend") as mock_backend:
            mock_backend.login = AsyncMock(return_value="tok")
            response = client.post("/auth/login", json={"password": "pw"})
        assert "access_token" in response.json()
