"""Unit tests for the notification-only Telegram client."""

import asyncio

import aiohttp
import pytest
from pydantic import SecretStr

from crypto_bot.config.settings import Settings
from crypto_bot.telegram.notifier import TelegramNotifier


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status = status
        self.body = body if body is not None else {"ok": True}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self, **kwargs):
        return self.body


class FakeSession:
    def __init__(self, response=None, error=None, **kwargs):
        self.response = response if response is not None else FakeResponse()
        self.error = error
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def configured_settings():
    return Settings(
        telegram_bot_token=SecretStr("test-token-not-real"),
        telegram_chat_id="123456",
    )


def test_successful_message_uses_telegram_send_message_endpoint():
    sessions = []

    def session_factory(**kwargs):
        session = FakeSession(**kwargs)
        sessions.append(session)
        return session

    notifier = TelegramNotifier(configured_settings(), session_factory)

    assert asyncio.run(notifier.send_message("Test notification")) is True
    assert len(sessions[0].calls) == 1
    url, kwargs = sessions[0].calls[0]
    assert url == "https://api.telegram.org/bottest-token-not-real/sendMessage"
    assert kwargs["json"] == {
        "chat_id": "123456",
        "text": "Test notification",
    }


def test_missing_credentials_skip_delivery():
    def unexpected_session(**kwargs):
        pytest.fail("HTTP session must not be created without credentials")

    notifier = TelegramNotifier(Settings(), unexpected_session)

    assert notifier.is_configured is False
    assert asyncio.run(notifier.send_message("Hello")) is False


@pytest.mark.parametrize("message", ["", "   ", "x" * 4097, None])
def test_invalid_messages_are_rejected_before_delivery(message):
    def unexpected_session(**kwargs):
        pytest.fail("Invalid message must not be sent")

    notifier = TelegramNotifier(configured_settings(), unexpected_session)

    assert asyncio.run(notifier.send_message(message)) is False


def test_telegram_api_rejection_returns_false():
    notifier = TelegramNotifier(
        configured_settings(),
        lambda **kwargs: FakeSession(
            response=FakeResponse(body={"ok": False, "description": "Rejected"})
        ),
    )

    assert asyncio.run(notifier.send_message("Hello")) is False


def test_http_error_returns_false():
    notifier = TelegramNotifier(
        configured_settings(),
        lambda **kwargs: FakeSession(response=FakeResponse(status=500)),
    )

    assert asyncio.run(notifier.send_message("Hello")) is False


def test_network_error_returns_false():
    notifier = TelegramNotifier(
        configured_settings(),
        lambda **kwargs: FakeSession(error=aiohttp.ClientConnectionError()),
    )

    assert asyncio.run(notifier.send_message("Hello")) is False
