"""Foundation-only startup check; no market data or orders are used here."""

import logging

from crypto_bot.config.settings import get_settings
from crypto_bot.logging_config import configure_logging


def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    logger = logging.getLogger("crypto_bot.startup")
    logger.info("Configuration validated successfully.")
    logger.info("Application environment: %s", settings.app_env)
    logger.info("Configured default mode: %s", settings.default_trading_mode.value)
    logger.info("Live trading enabled flag: %s", settings.live_trading_enabled)
    logger.info("Foundation stage only: no exchange connection or order execution was started.")


if __name__ == "__main__":
    main()