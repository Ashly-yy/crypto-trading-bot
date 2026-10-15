"""Unit tests for safe, rotating log configuration."""

import logging
from pathlib import Path

from crypto_bot.config.settings import Settings, TradingMode
from crypto_bot.logging_config import configure_logging


_MARKER = "_crypto_bot_managed_handler"


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        app_host="127.0.0.1",
        dashboard_host="127.0.0.1",
        default_trading_mode=TradingMode.BACKTEST,
        live_trading_enabled=False,
        log_level="INFO",
    )


def close_managed_handlers() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _MARKER, False):
            root.removeHandler(handler)
            handler.close()


def test_configure_logging_writes_utf8_file(tmp_path: Path) -> None:
    try:
        log_path = configure_logging(make_settings(), log_dir=tmp_path)
        logging.getLogger("crypto_bot.test").info("foundation log message")
        for handler in logging.getLogger().handlers:
            if getattr(handler, _MARKER, False):
                handler.flush()
        assert log_path.exists()
        assert "foundation log message" in log_path.read_text(encoding="utf-8")
    finally:
        close_managed_handlers()


def test_configure_logging_is_idempotent(tmp_path: Path) -> None:
    try:
        configure_logging(make_settings(), log_dir=tmp_path)
        first_count = sum(
            getattr(handler, _MARKER, False)
            for handler in logging.getLogger().handlers
        )
        configure_logging(make_settings(), log_dir=tmp_path)
        second_count = sum(
            getattr(handler, _MARKER, False)
            for handler in logging.getLogger().handlers
        )
        assert first_count == 2
        assert second_count == 2
    finally:
        close_managed_handlers()