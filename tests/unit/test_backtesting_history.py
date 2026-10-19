from dataclasses import replace
from decimal import Decimal

import pytest

from crypto_bot.backtesting.history import (
    HistoricalDataError,
    iter_historical_prefixes,
    validate_historical_candles,
)
from crypto_bot.market_data.candles import Candle, INTERVAL_MILLISECONDS


def make_candle(index: int, *, interval: str = "1m", symbol: str = "BTCUSDT") -> Candle:
    duration = INTERVAL_MILLISECONDS[interval]
    open_price = Decimal("100") + Decimal(index)
    return Candle(
        symbol=symbol,
        interval=interval,
        open_time_ms=index * duration,
        open=open_price,
        high=open_price + Decimal("2"),
        low=open_price - Decimal("1"),
        close=open_price + Decimal("1"),
        volume=Decimal("10"),
        close_time_ms=(index + 1) * duration - 1,
        quote_asset_volume=Decimal("1000"),
        number_of_trades=1,
        taker_buy_base_volume=Decimal("4"),
        taker_buy_quote_volume=Decimal("400"),
    )


def series(count: int = 4) -> tuple[Candle, ...]:
    return tuple(make_candle(i) for i in range(count))


def test_valid_series_is_preserved_without_sorting_or_mutation():
    candles = series()
    assert validate_historical_candles(
        candles, symbol="BTCUSDT", interval="1m"
    ) == candles


def test_empty_series_is_rejected():
    with pytest.raises(HistoricalDataError, match="empty"):
        validate_historical_candles((), symbol="BTCUSDT", interval="1m")


def test_wrong_symbol_is_rejected():
    with pytest.raises(HistoricalDataError, match="symbol/interval"):
        validate_historical_candles(series(), symbol="ETHUSDT", interval="1m")


def test_wrong_interval_is_rejected():
    with pytest.raises(HistoricalDataError, match="symbol/interval"):
        validate_historical_candles(series(), symbol="BTCUSDT", interval="5m")


def test_unsupported_interval_is_rejected():
    with pytest.raises(HistoricalDataError, match="unsupported"):
        validate_historical_candles(series(), symbol="BTCUSDT", interval="2m")


def test_out_of_order_candles_are_rejected_not_silently_sorted():
    candles = series()
    with pytest.raises(HistoricalDataError, match="chronological"):
        validate_historical_candles(
            (candles[0], candles[2], candles[1]),
            symbol="BTCUSDT",
            interval="1m",
            require_contiguous=False,
        )


def test_duplicate_open_timestamp_is_rejected():
    candles = series()
    duplicate = replace(candles[1], close=Decimal("102"))
    with pytest.raises(HistoricalDataError, match="chronological"):
        validate_historical_candles(
            (candles[0], candles[1], duplicate),
            symbol="BTCUSDT",
            interval="1m",
            require_contiguous=False,
        )


def test_missing_interval_is_rejected_by_default():
    candles = series()
    with pytest.raises(HistoricalDataError, match="gap"):
        validate_historical_candles(
            (candles[0], candles[2]),
            symbol="BTCUSDT",
            interval="1m",
        )


def test_gap_can_only_be_allowed_when_explicitly_requested():
    candles = series()
    result = validate_historical_candles(
        (candles[0], candles[2]),
        symbol="BTCUSDT",
        interval="1m",
        require_contiguous=False,
    )
    assert result == (candles[0], candles[2])


def test_invalid_ohlc_relationship_is_rejected():
    candles = series()
    bad = replace(candles[1], high=Decimal("1"))
    with pytest.raises(HistoricalDataError, match="inconsistent OHLC"):
        validate_historical_candles(
            (candles[0], bad),
            symbol="BTCUSDT",
            interval="1m",
        )


def test_non_positive_price_is_rejected():
    candles = series()
    bad = replace(candles[1], low=Decimal("0"))
    with pytest.raises(HistoricalDataError, match="invalid OHLC"):
        validate_historical_candles(
            (candles[0], bad),
            symbol="BTCUSDT",
            interval="1m",
        )


def test_wrong_close_timestamp_is_rejected():
    candles = series()
    bad = replace(candles[1], close_time_ms=candles[1].close_time_ms - 1)
    with pytest.raises(HistoricalDataError, match="close timestamp"):
        validate_historical_candles(
            (candles[0], bad),
            symbol="BTCUSDT",
            interval="1m",
        )


def test_prefixes_are_chronological_and_do_not_include_future_candles():
    candles = series()
    prefixes = list(
        iter_historical_prefixes(candles, symbol="BTCUSDT", interval="1m")
    )
    assert prefixes == [candles[:1], candles[:2], candles[:3], candles[:4]]
    assert all(len(prefix) <= len(candles) for prefix in prefixes)


def test_minimum_prefix_length_is_respected():
    candles = series()
    prefixes = list(
        iter_historical_prefixes(
            candles, symbol="BTCUSDT", interval="1m", min_candles=3
        )
    )
    assert prefixes == [candles[:3], candles[:4]]


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_invalid_minimum_prefix_length_is_rejected(value):
    with pytest.raises(HistoricalDataError, match="min_candles"):
        list(
            iter_historical_prefixes(
                series(), symbol="BTCUSDT", interval="1m", min_candles=value
            )
        )
