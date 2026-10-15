"""Unit tests for configuration validation and safety defaults."""

import pytest
from pydantic import ValidationError

from crypto_bot.config.settings import Settings, TradingMode


def make_settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "app_env": "test",
        "app_host": "127.0.0.1",
        "app_port": 8000,
        "dashboard_host": "127.0.0.1",
        "dashboard_port": 5173,
        "log_level": "INFO",
        "default_trading_mode": TradingMode.BACKTEST,
        "live_trading_enabled": False,
    }
    values.update(overrides)
    return Settings(**values)


def test_defaults_are_backtest_and_live_disabled() -> None:
    settings = make_settings()
    assert settings.default_trading_mode is TradingMode.BACKTEST
    assert settings.live_trading_enabled is False


@pytest.mark.parametrize("field", ["app_host", "dashboard_host"])
def test_network_bound_hosts_must_be_loopback(field: str) -> None:
    with pytest.raises(ValidationError, match="loopback"):
        make_settings(**{field: "0.0.0.0"})


def test_local_ipv6_loopback_is_allowed() -> None:
    settings = make_settings(app_host="::1")
    assert settings.app_host == "::1"


@pytest.mark.parametrize("field", ["app_port", "dashboard_port"])
def test_ports_must_be_in_allowed_range(field: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(**{field: 80})


def test_live_mode_is_rejected_while_live_flag_is_false() -> None:
    with pytest.raises(ValidationError, match="LIVE_TRADING_ENABLED"):
        make_settings(default_trading_mode=TradingMode.BINANCE_SPOT_LIVE)


def test_credentials_are_represented_as_secret_values() -> None:
    settings = make_settings(telegram_bot_token="fake-test-token")
    assert "fake-test-token" not in repr(settings)
    assert settings.telegram_bot_token is not None
    assert settings.telegram_bot_token.get_secret_value() == "fake-test-token"