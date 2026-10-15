from dataclasses import replace
from decimal import Decimal
import math

import pytest

from crypto_bot.config.timeframe_config import TIMEFRAME_SECONDS
from crypto_bot.indicators.engine import (
    IndicatorConfig,
    IndicatorInputError,
    calculate_indicator_series,
    calculate_latest_indicators,
    required_warmup_candles,
)
from crypto_bot.market_data.candles import Candle


def make_candles(count: int = 100, *, symbol: str = "BTCUSDT", interval: str = "5m", flat: bool = False) -> tuple[Candle, ...]:
    duration_ms = TIMEFRAME_SECONDS[interval] * 1000
    rows = []
    for i in range(count):
        close = 100.0 if flat else 100.0 + i * 0.04 + (i % 5) * 0.18
        open_price = close - (0.03 if i % 2 else -0.02)
        high = max(open_price, close) + 0.2
        low = min(open_price, close) - 0.2
        rows.append(Candle(
            symbol=symbol,
            interval=interval,
            open_time_ms=i * duration_ms,
            open=Decimal(str(open_price)),
            high=Decimal(str(high)),
            low=Decimal(str(low)),
            close=Decimal(str(close)),
            volume=Decimal(str(10 + (i % 9))),
            close_time_ms=(i + 1) * duration_ms - 1,
            quote_asset_volume=Decimal("1000"),
            number_of_trades=10,
            taker_buy_base_volume=Decimal("4"),
            taker_buy_quote_volume=Decimal("400"),
        ))
    return tuple(rows)


def test_output_has_one_snapshot_per_input_candle_and_keeps_timestamp_order() -> None:
    candles = make_candles()
    result = calculate_indicator_series(candles)
    assert len(result) == len(candles)
    assert [row.open_time_ms for row in result] == [row.open_time_ms for row in candles]
    assert all(row.symbol == "BTCUSDT" and row.interval == "5m" for row in result)


def test_ema_warmup_starts_after_complete_seed_window() -> None:
    result = calculate_indicator_series(make_candles(15))
    assert result[3].ema_short is None
    assert result[4].ema_short is not None
    assert result[8].ema_long is None
    assert result[9].ema_long is not None


def test_macd_signal_has_correct_sma_seed_warmup() -> None:
    result = calculate_indicator_series(make_candles(40))
    assert result[24].macd is None
    assert result[25].macd is not None
    assert result[32].macd_signal is None
    assert result[33].macd_signal is not None
    assert result[33].macd_histogram is not None


def test_rsi_warmup_requires_period_price_changes() -> None:
    result = calculate_indicator_series(make_candles(16))
    assert result[13].rsi is None
    assert result[14].rsi is not None
    assert 0 <= result[14].rsi <= 100


def test_bollinger_and_volume_warmup_use_complete_window() -> None:
    result = calculate_indicator_series(make_candles(25))
    assert result[18].bollinger_middle is None
    assert result[19].bollinger_upper is not None
    assert result[18].volume_sma is None
    assert result[19].volume_sma is not None
    assert result[19].volume_above_average is not None


def test_flat_series_has_neutral_rsi_by_documented_convention() -> None:
    latest = calculate_latest_indicators(make_candles(40, flat=True))
    assert latest is not None
    assert latest.rsi == 50.0


def test_warmed_up_flag_is_false_before_macd_signal_is_available() -> None:
    rows = calculate_indicator_series(make_candles(33))
    assert not rows[-1].is_warmed_up
    rows = calculate_indicator_series(make_candles(34))
    assert rows[-1].is_warmed_up


def test_required_warmup_candles_matches_default_macd_signal_window() -> None:
    assert required_warmup_candles() == 34


def test_custom_configuration_changes_warmup_and_uses_same_engine() -> None:
    config = IndicatorConfig(ema_short_period=3, ema_long_period=6, macd_fast_period=4,
                             macd_slow_period=8, macd_signal_period=3, rsi_period=5,
                             bollinger_period=6, volume_period=6)
    assert required_warmup_candles(config) == 10
    result = calculate_indicator_series(make_candles(12), config)
    assert result[-1].is_warmed_up


def test_latest_snapshot_is_same_as_last_series_snapshot() -> None:
    candles = make_candles()
    assert calculate_latest_indicators(candles) == calculate_indicator_series(candles)[-1]


def test_empty_input_returns_empty_series_and_no_latest_snapshot() -> None:
    assert calculate_indicator_series(()) == ()
    assert calculate_latest_indicators(()) is None


def test_future_rows_do_not_change_prior_indicator_snapshot() -> None:
    candles = make_candles(80)
    prefix = calculate_indicator_series(candles[:50])[-1]
    full = calculate_indicator_series(candles)[49]
    assert prefix == full


def test_duplicate_timestamps_are_rejected() -> None:
    candles = list(make_candles(5))
    candles[3] = replace(candles[3], open_time_ms=candles[2].open_time_ms)
    with pytest.raises(IndicatorInputError, match="strictly increasing"):
        calculate_indicator_series(candles)


def test_out_of_order_timestamps_are_rejected() -> None:
    candles = list(make_candles(5))
    candles[2], candles[3] = candles[3], candles[2]
    with pytest.raises(IndicatorInputError, match="strictly increasing"):
        calculate_indicator_series(candles)


def test_gap_in_candle_series_is_rejected_not_interpolated() -> None:
    candles = list(make_candles(6))
    candles.pop(3)
    with pytest.raises(IndicatorInputError, match="gap or irregular"):
        calculate_indicator_series(candles)


def test_mixed_symbols_are_rejected() -> None:
    candles = list(make_candles(5))
    candles[2] = replace(candles[2], symbol="ETHUSDT")
    with pytest.raises(IndicatorInputError, match="exactly one symbol"):
        calculate_indicator_series(candles)


def test_mixed_intervals_are_rejected() -> None:
    candles = list(make_candles(5))
    # Keep timestamps aligned to 5m so this failure is specifically mixed interval.
    candles[2] = replace(candles[2], interval="1m")
    with pytest.raises(IndicatorInputError, match="exactly one symbol and interval"):
        calculate_indicator_series(candles)


def test_invalid_ohlc_values_are_rejected() -> None:
    candles = list(make_candles(5))
    candles[2] = replace(candles[2], low=Decimal("999"))
    with pytest.raises(IndicatorInputError, match="internally inconsistent"):
        calculate_indicator_series(candles)


@pytest.mark.parametrize("kwargs", [
    {"ema_short_period": 0},
    {"ema_short_period": 10, "ema_long_period": 5},
    {"macd_fast_period": 26, "macd_slow_period": 12},
    {"rsi_period": True},
    {"bollinger_std_dev": 0},
    {"bollinger_std_dev": math.inf},
])
def test_invalid_indicator_config_is_rejected(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        IndicatorConfig(**kwargs)


def test_all_returned_numeric_indicators_are_finite() -> None:
    result = calculate_indicator_series(make_candles())
    for snapshot in result:
        for name in (
            "close", "ema_short", "ema_long", "macd", "macd_signal", "macd_histogram",
            "rsi", "bollinger_upper", "bollinger_middle", "bollinger_lower", "volume_sma",
        ):
            value = getattr(snapshot, name)
            assert value is None or math.isfinite(value)

def test_non_candle_element_is_rejected_with_indicator_input_error() -> None:
    candles = list(make_candles(5))
    candles[2] = object()
    with pytest.raises(IndicatorInputError, match="normalized Candle"):
        calculate_indicator_series(candles)


def test_close_timestamp_must_match_interval_duration() -> None:
    candles = list(make_candles(5))
    candles[2] = replace(candles[2], close_time_ms=candles[2].close_time_ms + 1)
    with pytest.raises(IndicatorInputError, match="close timestamp must equal interval close"):
        calculate_indicator_series(candles)


def test_open_timestamp_must_align_to_interval_boundary() -> None:
    candles = list(make_candles(5))
    candles[2] = replace(candles[2], open_time_ms=candles[2].open_time_ms + 1)
    with pytest.raises(IndicatorInputError, match="not aligned to its interval boundary"):
        calculate_indicator_series(candles)


def test_timestamp_types_are_validated_before_order_comparison() -> None:
    candles = list(make_candles(5))
    candles[2] = replace(candles[2], open_time_ms="bad")
    with pytest.raises(IndicatorInputError, match="open timestamps must be integer milliseconds"):
        calculate_indicator_series(candles)



def test_weekly_candle_uses_monday_utc_alignment() -> None:
    # Unix epoch began on Thursday; Binance weekly candles begin Monday at 00:00 UTC.
    open_time_ms = 4 * 86_400_000
    weekly = Candle(
        symbol="BTCUSDT",
        interval="1w",
        open_time_ms=open_time_ms,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        volume=Decimal("10"),
        close_time_ms=open_time_ms + 604_800_000 - 1,
        quote_asset_volume=Decimal("1000"),
        number_of_trades=10,
        taker_buy_base_volume=Decimal("4"),
        taker_buy_quote_volume=Decimal("400"),
    )
    assert calculate_indicator_series((weekly,))[0].close == 100.5
