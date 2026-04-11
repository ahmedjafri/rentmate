import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from gql.services import sms_service


def test_quo_config_helpers_prefer_integrations(monkeypatch):
    monkeypatch.setattr(sms_service, "QUO_API_KEY", "env-key")
    with patch("gql.services.sms_service.get_integrations", return_value={"quo": {"api_key": "cfg-key", "from_number": "+15551234567"}}):
        assert sms_service.get_quo_api_key() == "cfg-key"
        assert sms_service.get_quo_from_number() == "+15551234567"


def test_send_sms_reply_normalizes_numbers_and_posts(monkeypatch):
    response = SimpleNamespace(status_code=200, text="ok")
    post = AsyncMock(return_value=response)
    client = AsyncMock()
    client.post = post
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False

    monkeypatch.setattr(sms_service.httpx, "AsyncClient", lambda: client)

    asyncio.run(sms_service.send_sms_reply("5551112222", "(555) 333-4444", "hello", api_key="test-key"))

    post.assert_awaited_once()
    _, kwargs = post.await_args
    assert kwargs["headers"]["Authorization"] == "test-key"
    assert kwargs["json"] == {
        "content": "hello",
        "from": "+15551112222",
        "to": ["+15553334444"],
    }


def test_send_via_channel_routes_sms_only():
    conv = SimpleNamespace(channel_type="sms")

    with patch("gql.services.sms_service.send_sms_reply", new=AsyncMock()) as send_sms:
        asyncio.run(sms_service.send_via_channel(
            conv,
            "reply body",
            {"to_number": "5551112222", "from_number": "5553334444"},
        ))

    send_sms.assert_awaited_once_with(
        from_num="5551112222",
        to_num="5553334444",
        text="reply body",
    )
