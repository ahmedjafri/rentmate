from llm.model_config import resolve_model_config


def test_resolve_model_config_strips_deepseek_prefix_with_explicit_base_url():
    resolved = resolve_model_config(
        model="deepseek/deepseek-chat",
        api_base="https://api.deepseek.com/v1",
    )
    assert resolved.model == "deepseek-chat"
    assert resolved.api_base == "https://api.deepseek.com/v1"


def test_resolve_model_config_infers_base_for_known_provider():
    resolved = resolve_model_config(model="deepseek/deepseek-chat", api_base=None)
    assert resolved.model == "deepseek-chat"
    assert resolved.api_base == "https://api.deepseek.com"


def test_resolve_model_config_leaves_unknown_prefix_unchanged():
    resolved = resolve_model_config(
        model="ollama/llama3",
        api_base="http://localhost:11434/v1",
    )
    assert resolved.model == "ollama/llama3"
    assert resolved.api_base == "http://localhost:11434/v1"
