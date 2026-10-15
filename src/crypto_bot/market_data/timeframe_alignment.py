"""Causal multi-timeframe alignment for closed Binance Spot candles.

A decision made after an entry candle closes can use only context candles whose
close time is strictly earlier than the decision timestamp. Future context rows
may exist in a historical dataset, but they are never selected for earlier rows.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Sequence

from crypto_bot.config.settings import Settings, get_settings
from crypto_bot.config.timeframe_config import (
    SUPPORTED_CANDLE_INTERVALS,
    TIMEFRAME_SECONDS,
    StrategyTimeframeMapping,
)
from crypto_bot.market_data.candles import Candle


@dataclass(frozen=True)
class AlignedCandle:
    """One closed entry candle and only the higher-timeframe data available then."""

    symbol: str
    entry_interval: str
    entry_candle: Candle
    decision_time_ms: int
    context_candles: Mapping[str, Candle | None]

    @property
    def missing_context_intervals(self) -> tuple[str, ...]:
        return tuple(interval for interval, candle in self.context_candles.items() if candle is None)


def configured_strategy_timeframes(settings: Settings | None = None) -> dict[str, StrategyTimeframeMapping]:
    """Return the validated, configurable mappings for scalp/day/swing styles."""
    current = settings or get_settings()
    return {
        "scalp": StrategyTimeframeMapping(
            style="scalp",
            entry_interval=current.scalp_entry_interval,
            context_intervals=current.scalp_context_interval_list,
        ),
        "day": StrategyTimeframeMapping(
            style="day",
            entry_interval=current.day_entry_interval,
            context_intervals=current.day_context_interval_list,
        ),
        "swing": StrategyTimeframeMapping(
            style="swing",
            entry_interval=current.swing_entry_interval,
            context_intervals=current.swing_context_interval_list,
        ),
    }


def _validate_series(
    candles: Sequence[Candle],
    *,
    label: str,
    expected_symbol: str | None = None,
    expected_interval: str | None = None,
) -> tuple[Candle, ...]:
    series = tuple(candles)
    previous: Candle | None = None
    for candle in series:
        if not isinstance(candle, Candle):
            raise ValueError(f"{label} must contain normalized Candle objects only.")
        if candle.interval not in SUPPORTED_CANDLE_INTERVALS:
            raise ValueError(f"{label} contains unsupported interval {candle.interval!r}.")
        if expected_symbol is not None and candle.symbol != expected_symbol:
            raise ValueError(f"{label} mixes symbols; expected {expected_symbol}.")
        if expected_interval is not None and candle.interval != expected_interval:
            raise ValueError(f"{label} key/interval mismatch; expected {expected_interval}.")
        if candle.close_time_ms < candle.open_time_ms:
            raise ValueError(f"{label} contains a candle whose close precedes its open.")
        if previous is not None:
            if candle.open_time_ms == previous.open_time_ms:
                raise ValueError(f"{label} contains duplicate candle open timestamps.")
            if candle.open_time_ms < previous.open_time_ms:
                raise ValueError(f"{label} is not in chronological order.")
            if candle.close_time_ms <= previous.close_time_ms:
                raise ValueError(f"{label} close timestamps are duplicated or out of order.")
        previous = candle
    return series


def align_entry_to_context(
    entry_candles: Sequence[Candle],
    context_candles_by_interval: Mapping[str, Sequence[Candle]],
    *,
    as_of_time_ms: int | None = None,
) -> tuple[AlignedCandle, ...]:
    """Align each closed entry candle to the latest context candle already closed.

    The decision timestamp is `entry.close_time_ms + 1`, because Binance candle
    close timestamps are inclusive. A context candle is eligible only when
    `context.close_time_ms < decision_time_ms`; equal/future timestamps are not
    allowed. When `as_of_time_ms` is provided, live callers receive only entry
    candles with `close_time_ms < as_of_time_ms`. Historical callers may omit it
    to simulate one decision at each entry candle's own close.

    Missing context is represented as None. Callers must not silently turn a
    missing higher-timeframe candle into a positive signal.
    """
    if as_of_time_ms is not None and (
        isinstance(as_of_time_ms, bool) or not isinstance(as_of_time_ms, int) or as_of_time_ms < 0
    ):
        raise ValueError("as_of_time_ms must be a non-negative integer timestamp in milliseconds.")

    entries = tuple(entry_candles)
    if not entries:
        return ()
    symbol = entries[0].symbol
    entry_interval = entries[0].interval
    if entry_interval not in SUPPORTED_CANDLE_INTERVALS:
        raise ValueError(f"Unsupported entry interval {entry_interval!r}.")
    entries = _validate_series(
        entries, label="entry candles", expected_symbol=symbol, expected_interval=entry_interval
    )

    context_series: dict[str, tuple[Candle, ...]] = {}
    context_close_times: dict[str, list[int]] = {}
    for interval, raw_series in context_candles_by_interval.items():
        if interval not in SUPPORTED_CANDLE_INTERVALS:
            raise ValueError(f"Unsupported context interval {interval!r}.")
        if TIMEFRAME_SECONDS[interval] <= TIMEFRAME_SECONDS[entry_interval]:
            raise ValueError(
                f"Context interval {interval} must be strictly higher than entry interval {entry_interval}."
            )
        series = _validate_series(
            raw_series, label=f"{interval} context candles", expected_symbol=symbol,
            expected_interval=interval,
        )
        context_series[interval] = series
        context_close_times[interval] = [candle.close_time_ms for candle in series]

    aligned: list[AlignedCandle] = []
    for entry in entries:
        if as_of_time_ms is not None and entry.close_time_ms >= as_of_time_ms:
            continue
        decision_time_ms = entry.close_time_ms + 1
        selected: dict[str, Candle | None] = {}
        for interval, series in context_series.items():
            # bisect_left gives the first close >= decision time; taking its
            # predecessor guarantees strict point-in-time causality.
            index = bisect_left(context_close_times[interval], decision_time_ms) - 1
            selected[interval] = series[index] if index >= 0 else None
        aligned.append(AlignedCandle(
            symbol=symbol,
            entry_interval=entry_interval,
            entry_candle=entry,
            decision_time_ms=decision_time_ms,
            context_candles=MappingProxyType(selected),
        ))
    return tuple(aligned)