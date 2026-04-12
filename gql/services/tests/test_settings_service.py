import os

import pytest
from sqlalchemy.orm import sessionmaker

from db.models import AppSetting
from gql.services import settings_service


@pytest.fixture(autouse=True)
def _settings_session_factory(monkeypatch, engine):
    AppSetting.__table__.drop(engine, checkfirst=True)
    AppSetting.__table__.create(engine, checkfirst=True)
    monkeypatch.setattr(
        settings_service.SessionLocal,
        "session_factory",
        sessionmaker(bind=engine, autoflush=False, autocommit=False),
    )


@pytest.fixture(autouse=True)
def _seed_current_user():
    yield


def test_settings_round_trip_and_action_policy_defaults(engine):
    settings_service.set_setting("feature_flags", value={"x": True})

    assert settings_service.get_setting("feature_flags") == {"x": True}
    assert settings_service.load_app_settings()["feature_flags"] == {"x": True}
    assert settings_service.get_action_policy_settings()["entity_changes"] == "balanced"
    assert settings_service.entity_change_confidence_threshold() == 0.75
    assert settings_service.outbound_message_allows_risk("medium") is True
    assert settings_service.outbound_message_allows_risk("high", "strict") is False


def test_llm_and_integration_settings_merge_with_env(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "env-key")
    monkeypatch.setenv("LLM_MODEL", "env-model")
    monkeypatch.setenv("LLM_BASE_URL", "https://env.example")

    assert settings_service.get_llm_settings() == {
        "api_key": "env-key",
        "model": "env-model",
        "base_url": "https://env.example",
    }

    settings_service.save_llm_settings(api_key="db-key", model="db-model")
    settings = settings_service.get_llm_settings()
    assert settings["api_key"] == "db-key"
    assert settings["model"] == "db-model"
    assert settings["base_url"] == "https://env.example"

    settings_service.save_agent_integrations(brave_api_key="brave", web_search_enabled=True)
    assert settings_service.get_agent_integrations() == {
        "brave_api_key": "brave",
        "web_search_enabled": True,
    }


def test_load_helpers_write_missing_env_vars(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)

    settings_service.save_llm_settings(api_key="saved-key", model="saved-model", base_url="https://saved.example")
    settings_service.save_agent_integrations(brave_api_key="saved-brave")

    settings_service.load_llm_into_env()
    settings_service.load_agent_integrations_into_env()

    assert os.environ["LLM_API_KEY"] == "saved-key"
    assert os.environ["LLM_MODEL"] == "saved-model"
    assert os.environ["LLM_BASE_URL"] == "https://saved.example"
    assert os.environ["BRAVE_API_KEY"] == "saved-brave"
    assert settings_service.get_integrations() == {}
    assert settings_service.save_integrations({"quo": {"api_key": "123"}}) is None
    assert settings_service.get_integrations() == {"quo": {"api_key": "123"}}
