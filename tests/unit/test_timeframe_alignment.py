"""Tests proving timeframe joins are chronological and do not leak future candles."""

from decimal import Decimal

import pytest

from crypto_bot.market_data.candles import Candle
from crypto_bot.market_data.timeframe_alignment import align_entry_to_context
from crypto_bot.config.timeframe_config import TIMEFRAME_SECONDS


def make_candle(symbol: str, interval: str, open_time_ms: int, close: str = "100") -> Candle:
    duration_ms = TIMEFRAME_SECONDS[interval] * 1000
    price = Decimal(close)
    return Candle(
        symbol=symbol, interval=interval, open_time_ms=open_time_ms,
        open=price, high=price + Decimal("1"), low=price - Decimal("1"),
        close=price, volume=Decimal("10"), close_time_ms=open_time_ms + duration_ms - 1,
        quote_asset_volume=Decimal("1000"), number_of_trades=5,
        taker_buy_base_volume=Decimal("4"), taker_buy_quote_volume=Decimal("400"),
    )


def test_context_is_missing_until_its_candle_has_closed() -> None:
    entry = [make_candle("BTCUSDT", "1m", 0), make_candle("BTCUSDT", "1m", 60_000), make_candle("BTCUSDT", "1m", 120_000)]
    context = [make_candle("BTCUSDT", "3m", 0)]
    aligned = align_entry_to_context(entry, {"3m": context})
    assert aligned[0].context_candles["3m"] is None
    assert aligned[1].context_candles["3m"] is None
    assert aligned[2].context_candles["3m"] == context[0]


def test_future_context_candle_is_never_used_for_earlier_decision() -> None:
    entry = [make_candle("BTCUSDT", "1m", 0)]
    context = [make_candle("BTCUSDT", "3m", 0), make_candle("BTCUSDT", "3m", 180_000)]
    aligned = align_entry_to_context(entry, {"3m": context})
    assert aligned[0].context_candles["3m"] is None
    assert aligned[0].decision_time_ms == 60_000


def test_exactly_closed_context_is_available_after_its_close_timestamp() -> None:
    entry = [make_candle("BTCUSDT", "1m", 120_000)]
    context = [make_candle("BTCUSDT", "3m", 0)]
    aligned = align_entry_to_context(entry, {"3m": context})
    assert aligned[0].decision_time_ms == 180_000
    assert aligned[0].context_candles["3m"] == context[0]
    assert context[0].close_time_ms < aligned[0].decision_time_ms


def test_as_of_time_excludes_unclosed_entry_candles() -> None:
    entry = [make_candle("BTCUSDT", "1m", 0), make_candle("BTCUSDT", "1m", 60_000)]
    aligned = align_entry_to_context(entry, {"3m": []}, as_of_time_ms=65_000)
    assert len(aligned) == 1
    assert aligned[0].entry_candle.open_time_ms == 0


def test_missing_context_is_explicit_not_fabricated() -> None:
    aligned = align_entry_to_context([make_candle("BTCUSDT", "1m", 0)], {"3m": []})
    assert aligned[0].context_candles["3m"] is None
    assert aligned[0].missing_context_intervals == ("3m",)


def test_out_of_order_entry_series_is_rejected() -> None:
    entry = [make_candle("BTCUSDT", "1m", 60_000), make_candle("BTCUSDT", "1m", 0)]
    with pytest.raises(ValueError, match="chronological order"):
        align_entry_to_context(entry, {"3m": []})


def test_duplicate_entry_open_time_is_rejected() -> None:
    candle = make_candle("BTCUSDT", "1m", 0)
    with pytest.raises(ValueError, match="duplicate"):
        align_entry_to_context([candle, candle], {"3m": []})


def test_cross_symbol_context_is_rejected() -> None:
    entry = [make_candle("BTCUSDT", "1m", 0)]
    context = [make_candle("ETHUSDT", "3m", 0)]
    with pytest.raises(ValueError, match="mixes symbols"):
        align_entry_to_context(entry, {"3m": context})


def test_context_key_must_match_actual_candle_interval() -> None:
    entry = [make_candle("BTCUSDT", "1m", 0)]
    context = [make_candle("BTCUSDT", "5m", 0)]
    with pytest.raises(ValueError, match="key/interval mismatch"):
        align_entry_to_context(entry, {"3m": context})


def test_context_must_be_higher_than_entry_interval() -> None:
    entry = [make_candle("BTCUSDT", "5m", 0)]
    context = [make_candle("BTCUSDT", "3m", 0)]
    with pytest.raises(ValueError, match="strictly higher"):
        align_entry_to_context(entry, {"3m": context})


def test_empty_entry_series_returns_no_rows() -> None:
    assert align_entry_to_context([], {"3m": []}) == ()