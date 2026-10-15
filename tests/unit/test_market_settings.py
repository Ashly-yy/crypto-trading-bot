"""Validation tests for configurable market allowlists and exclusions."""

import pytest
from pydantic import ValidationError

from crypto_bot.config.settings import Settings


def make_settings(**overrides):
    values = {"_env_file": None, "app_env": "test"}
    values.update(overrides)
    return Settings(**values)


def test_default_markets_preserve_original_bot_universe():
    settings = make_settings()
    assert settings.market_symbol_list == ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    assert settings.market_quote_asset == "USDT"


def test_market_symbol_configuration_is_normalised():
    settings = make_settings(market_symbols=" btcusdt, ethusdt ")
    assert settings.market_symbol_list == ("BTCUSDT", "ETHUSDT")


def test_duplicate_symbols_are_rejected():
    with pytest.raises(ValidationError, match="duplicate"):
        make_settings(market_symbols="BTCUSDT,btcusdt")


def test_malformed_symbols_are_rejected():
    with pytest.raises(ValidationError, match="malformed"):
        make_settings(market_symbols="BTC/USDT")


def test_empty_items_are_rejected():
    with pytest.raises(ValidationError, match="empty"):
        make_settings(market_symbols="BTCUSDT,,ETHUSDT")


def test_exclusion_list_can_be_empty_or_configured():
    assert make_settings().excluded_market_symbol_list == ()
    assert make_settings(market_excluded_symbols="dogeusdt").excluded_market_symbol_list == ("DOGEUSDT",)


def test_quote_asset_is_normalised_and_validated():
    assert make_settings(market_quote_asset="usdt").market_quote_asset == "USDT"
    with pytest.raises(ValidationError):
        make_settings(market_quote_asset="USDT/USDC")