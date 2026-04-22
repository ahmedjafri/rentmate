from types import SimpleNamespace

from llm.litellm_utils import completion_with_retries
from llm.model_config import build_litellm_request_kwargs, resolve_model_config


def test_resolve_model_config_strips_deepseek_prefix_with_explicit_base_url():
    resolved = resolve_model_config(
        model="deepseek/deepseek-chat",
        api_base="https://api.deepseek.com/v1",
    )
    assert resolved.model == "deepseek-chat"
    assert resolved.litellm_model == "deepseek/deepseek-chat"
    assert resolved.api_base == "https://api.deepseek.com/v1"


def test_resolve_model_config_infers_base_for_known_provider():
    resolved = resolve_model_config(model="deepseek/deepseek-chat", api_base=None)
    assert resolved.model == "deepseek-chat"
    assert resolved.litellm_model == "deepseek/deepseek-chat"
    assert resolved.api_base == "https://api.deepseek.com"


def test_resolve_model_config_leaves_unknown_prefix_unchanged():
    resolved = resolve_model_config(
        model="ollama/llama3",
        api_base="http://localhost:11434/v1",
    )
    assert resolved.model == "ollama/llama3"
    assert resolved.litellm_model == "ollama/llama3"
    assert resolved.api_base == "http://localhost:11434/v1"


def test_resolve_model_config_normalizes_openrouter_prefixed_model():
    resolved = resolve_model_config(
        model="openrouter/google/gemma-4-26b-a4b-it:free",
        api_base=None,
    )
    assert resolved.model == "google/gemma-4-26b-a4b-it:free"
    assert resolved.litellm_model == "openrouter/google/gemma-4-26b-a4b-it:free"
    assert resolved.api_base == "https://openrouter.ai/api/v1"
    assert resolved.provider == "openrouter"


def test_resolve_model_config_uses_openrouter_contract_when_base_url_is_openrouter():
    resolved = resolve_model_config(
        model="anthropic/claude-sonnet-4.6",
        api_base="https://openrouter.ai/api/v1",
    )
    assert resolved.model == "anthropic/claude-sonnet-4.6"
    assert resolved.litellm_model == "openrouter/anthropic/claude-sonnet-4.6"
    assert resolved.api_base == "https://openrouter.ai/api/v1"
    assert resolved.provider == "openrouter"


def test_build_litellm_request_kwargs_uses_openrouter_headers_and_base_url():
    kwargs = build_litellm_request_kwargs(
        model="anthropic/claude-sonnet-4.6",
        api_base="https://openrouter.ai/api/v1",
        api_key="test-key",
        app_name="RentMate Evals",
        referer="https://rentmate.test",
    )
    assert kwargs["model"] == "openrouter/anthropic/claude-sonnet-4.6"
    assert kwargs["base_url"] == "https://openrouter.ai/api/v1"
    assert kwargs["api_key"] == "test-key"
    assert kwargs["extra_headers"] == {
        "HTTP-Referer": "https://rentmate.test",
        "X-Title": "RentMate Evals",
    }


def test_build_litellm_request_kwargs_non_openrouter_has_no_extra_headers():
    kwargs = build_litellm_request_kwargs(
        model="openai/gpt-4o-mini",
        api_base="https://api.openai.com/v1",
        api_key="test-key",
        app_name="RentMate Evals",
        referer="https://rentmate.test",
    )
    assert kwargs["model"] == "openai/gpt-4o-mini"
    assert kwargs["base_url"] == "https://api.openai.com/v1"
    assert "extra_headers" not in kwargs


def test_completion_with_retries_uses_shared_openrouter_request_shape(monkeypatch):
    captured: dict[str, object] = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
        )

    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setattr("llm.litellm_utils.litellm.completion", fake_completion)

    response, model, base_url = completion_with_retries(
        messages=[{"role": "user", "content": "hi"}],
        model="anthropic/claude-sonnet-4.6",
        api_base="https://openrouter.ai/api/v1",
        retries=1,
    )

    assert response.choices[0].message.content == "ok"
    assert model == "openrouter/anthropic/claude-sonnet-4.6"
    assert base_url == "https://openrouter.ai/api/v1"
    assert captured["model"] == "openrouter/anthropic/claude-sonnet-4.6"
    assert captured["base_url"] == "https://openrouter.ai/api/v1"
    assert captured["api_key"] == "test-key"
    assert captured["timeout"] == 45.0
    assert captured["extra_headers"]["X-Title"] == "RentMate"


def test_completion_with_retries_honors_timeout_env(monkeypatch):
    captured: dict[str, object] = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
        )

    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LITELLM_REQUEST_TIMEOUT_SECONDS", "20")
    monkeypatch.setattr("llm.litellm_utils.litellm.completion", fake_completion)

    completion_with_retries(
        messages=[{"role": "user", "content": "hi"}],
        model="openai/gpt-4o-mini",
        api_base="https://api.openai.com/v1",
        retries=1,
    )

    assert captured["timeout"] == 20.0
