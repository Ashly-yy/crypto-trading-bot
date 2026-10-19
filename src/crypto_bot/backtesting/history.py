from __future__ import annotations

from collections.abc import Iterable, Iterator
from decimal import Decimal
from typing import TypeAlias

from crypto_bot.market_data.candles import Candle, INTERVAL_MILLISECONDS


CandleSeries: TypeAlias = tuple[Candle, ...]


class HistoricalDataError(ValueError):
    """Raised when historical candles are unsafe for deterministic replay."""


def validate_historical_candles(
    candles: Iterable[Candle],
    *,
    symbol: str,
    interval: str,
    require_contiguous: bool = True,
) -> CandleSeries:
    """Validate one chronological, single-symbol/interval candle series.

    This function never sorts or repairs input. Invalid ordering, duplicate
    timestamps, malformed OHLC values, and (by default) missing intervals fail
    closed so a caller cannot unknowingly backtest corrupted history.
    """
    if not isinstance(symbol, str) or not symbol.strip() or symbol != symbol.strip():
        raise HistoricalDataError("symbol must be a non-empty trimmed string")
    if not isinstance(interval, str) or interval not in INTERVAL_MILLISECONDS:
        raise HistoricalDataError(f"unsupported candle interval: {interval!r}")

    series = tuple(candles)
    if not series:
        raise HistoricalDataError("historical candle series is empty")

    duration = INTERVAL_MILLISECONDS[interval]
    previous: Candle | None = None

    for index, candle in enumerate(series):
        if not isinstance(candle, Candle):
            raise HistoricalDataError(f"candle {index} is not a Candle instance")
        if candle.symbol != symbol or candle.interval != interval:
            raise HistoricalDataError(
                f"candle {index} symbol/interval does not match requested series"
            )
        if (
            isinstance(candle.open_time_ms, bool)
            or not isinstance(candle.open_time_ms, int)
            or candle.open_time_ms < 0
            or isinstance(candle.close_time_ms, bool)
            or not isinstance(candle.close_time_ms, int)
            or candle.close_time_ms < candle.open_time_ms
        ):
            raise HistoricalDataError(f"candle {index} has invalid timestamps")
        if candle.close_time_ms != candle.open_time_ms + duration - 1:
            raise HistoricalDataError(
                f"candle {index} close timestamp does not match interval duration"
            )

        prices = (candle.open, candle.high, candle.low, candle.close)
        if any(
            not isinstance(value, Decimal) or not value.is_finite() or value <= 0
            for value in prices
        ):
            raise HistoricalDataError(f"candle {index} has invalid OHLC prices")
        if candle.low > min(candle.open, candle.close) or candle.high < max(
            candle.open, candle.close
        ) or candle.low > candle.high:
            raise HistoricalDataError(f"candle {index} has inconsistent OHLC values")

        if (
            not isinstance(candle.volume, Decimal)
            or not candle.volume.is_finite()
            or candle.volume < 0
        ):
            raise HistoricalDataError(f"candle {index} has invalid volume")

        if previous is not None:
            if candle.open_time_ms <= previous.open_time_ms:
                raise HistoricalDataError(
                    "candles must be strictly chronological with no duplicates"
                )
            expected_open = previous.open_time_ms + duration
            if require_contiguous and candle.open_time_ms != expected_open:
                raise HistoricalDataError(
                    f"historical gap before candle {index}: expected open "
                    f"{expected_open}, received {candle.open_time_ms}"
                )
        previous = candle

    return series


def iter_historical_prefixes(
    candles: Iterable[Candle],
    *,
    symbol: str,
    interval: str,
    min_candles: int = 1,
    require_contiguous: bool = True,
) -> Iterator[CandleSeries]:
    """Yield expanding prefixes in time order, never exposing future candles.

    Each yielded tuple ends at the current historical candle. This primitive
    deliberately does not evaluate strategies, infer fills, or execute orders.
    """
    if isinstance(min_candles, bool) or not isinstance(min_candles, int) or min_candles < 1:
        raise HistoricalDataError("min_candles must be a positive integer")

    series = validate_historical_candles(
        candles,
        symbol=symbol,
        interval=interval,
        require_contiguous=require_contiguous,
    )
    for end in range(min_candles, len(series) + 1):
        yield series[:end]
