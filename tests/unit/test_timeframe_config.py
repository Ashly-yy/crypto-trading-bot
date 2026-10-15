"""Configuration validation tests for supported and strategy-mapped timeframes."""

import pytest
from pydantic import ValidationError

from crypto_bot.config.settings import Settings
from crypto_bot.config.timeframe_config import SUPPORTED_CANDLE_INTERVALS


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, app_env="test", **overrides)


def test_all_user_approved_intervals_are_supported_by_default() -> None:
    settings = make_settings()
    assert settings.supported_candle_interval_list == SUPPORTED_CANDLE_INTERVALS


def test_default_style_mappings_are_higher_timeframe_contexts() -> None:
    settings = make_settings()
    assert settings.scalp_entry_interval == "1m"
    assert settings.scalp_context_interval_list == ("3m", "5m")
    assert settings.day_entry_interval == "5m"
    assert settings.day_context_interval_list == ("15m", "1h")
    assert settings.swing_entry_interval == "1h"
    assert settings.swing_context_interval_list == ("4h", "1d")


def test_valid_custom_subset_and_mappings_are_accepted() -> None:
    settings = make_settings(
        supported_candle_intervals="1m,3m",
        scalp_entry_interval="1m", scalp_context_intervals="3m",
        day_entry_interval="1m", day_context_intervals="3m",
        swing_entry_interval="1m", swing_context_intervals="3m",
    )
    assert settings.supported_candle_interval_list == ("1m", "3m")


@pytest.mark.parametrize(
    "value",
    ["1m,1m", "1m,2m", "", "1m,,3m"],
)
def test_invalid_supported_interval_lists_are_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(supported_candle_intervals=value)


def test_invalid_entry_interval_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_settings(scalp_entry_interval="2m")


def test_duplicate_context_interval_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_settings(scalp_context_intervals="3m,3m")


def test_context_must_be_strictly_higher_than_entry() -> None:
    with pytest.raises(ValidationError, match="strictly higher"):
        make_settings(scalp_entry_interval="5m", scalp_context_intervals="3m")


def test_context_must_be_in_the_configured_supported_subset() -> None:
    with pytest.raises(ValidationError, match="not in SUPPORTED_CANDLE_INTERVALS"):
        make_settings(
            supported_candle_intervals="1m,3m",
            scalp_entry_interval="1m", scalp_context_intervals="3m",
            day_entry_interval="1m", day_context_intervals="3m",
            swing_entry_interval="1m", swing_context_intervals="5m",
        )