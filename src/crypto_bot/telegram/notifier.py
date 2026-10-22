"""Safe, notification-only Telegram Bot API client."""

import asyncio
import logging
from collections.abc import Callable

import aiohttp

from crypto_bot.config.settings import Settings

logger = logging.getLogger(__name__)

_MAX_MESSAGE_LENGTH = 4096
_REQUEST_TIMEOUT_SECONDS = 10


class TelegramNotifier:
    """Send Telegram notifications without controlling trading."""

    def __init__(
        self,
        settings: Settings,
        session_factory: Callable[..., aiohttp.ClientSession] | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory or aiohttp.ClientSession

    @property
    def is_configured(self) -> bool:
        """Return whether both Telegram credentials are present."""
        token = self._settings.telegram_bot_token
        chat_id = self._settings.telegram_chat_id
        return bool(
            token is not None
            and token.get_secret_value().strip()
            and chat_id
            and chat_id.strip()
        )

    async def send_message(self, message: str) -> bool:
        """Send one message; return False on configuration or delivery failure."""
        if not isinstance(message, str) or not message.strip():
            logger.warning("Telegram notification skipped: message is empty or invalid.")
            return False

        if len(message) > _MAX_MESSAGE_LENGTH:
            logger.warning("Telegram notification skipped: message exceeds size limit.")
            return False

        if not self.is_configured:
            logger.info("Telegram notifications are not configured; message skipped.")
            return False

        token = self._settings.telegram_bot_token.get_secret_value()
        chat_id = self._settings.telegram_chat_id.strip()
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message}
        timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_SECONDS)

        try:
            async with self._session_factory(timeout=timeout) as session:
                async with session.post(url, json=payload) as response:
                    if response.status < 200 or response.status >= 300:
                        logger.warning(
                            "Telegram notification failed with HTTP status %s.",
                            response.status,
                        )
                        return False

                    result = await response.json(content_type=None)
                    if not isinstance(result, dict) or result.get("ok") is not True:
                        logger.warning("Telegram rejected a notification.")
                        return False

                    return True

        except (aiohttp.ClientError, asyncio.TimeoutError):
            logger.warning("Telegram notification failed due to a network error.")
            return False
