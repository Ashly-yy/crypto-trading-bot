"""Causal strategy-driven historical backtesting for spot-only research.

This adapter prepares a strategy decision at each replay candle-open boundary using
only candles that had closed before that boundary. It delegates entry authorization,
portfolio updates, fills, and replay to the existing central backtesting components.
It never creates an exchange client or submits orders.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Mapping, Sequence

from crypto_bot.backtesting.orchestration import DecisionPoint, OrchestrationReport
from crypto_bot.backtesting.stateful_orchestration import run_stateful_historical_orchestration
from crypto_bot.market_data.candles import Candle, CandleBatch, CandleGap, INTERVAL_MILLISECONDS
from crypto_bot.market_intelligence.regime import assess_market_regime
from crypto_bot.risk.manager import RiskContext
from crypto_bot.strategies.common import MarketCostEstimate
from crypto_bot.strategies.scalp import ScalpStrategyConfig
from crypto_bot.strategies.day import DayStrategyConfig
from crypto_bot.strategies.swing import SwingStrategyConfig
from crypto_bot.market_data.timeframe_alignment import configured_strategy_timeframes


class StrategyDrivenBacktestError(ValueError):
    """Raised when historical strategy inputs cannot be built safely."""


_CONFIG_TYPES = {
    "scalp": ScalpStrategyConfig,
    "day": DayStrategyConfig,
    "swing": SwingStrategyConfig,
}


def _strategy_intervals(style: str, config: Any) -> tuple[str, tuple[str, ...]]:
    if style not in _CONFIG_TYPES:
        raise StrategyDrivenBacktestError("style must be scalp, day, or swing.")
    if not isinstance(config, _CONFIG_TYPES[style]):
        raise StrategyDrivenBacktestError(f"{style} requires {_CONFIG_TYPES[style].__name__}.")
    configured = configured_strategy_timeframes()[style]
    entry = config.entry_interval or configured.entry_interval
    contexts = config.context_intervals if config.context_intervals is not None else configured.context_intervals
    if not isinstance(entry, str) or not entry or not contexts or len(set(contexts)) != len(contexts):
        raise StrategyDrivenBacktestError("Strategy timeframe mapping is invalid.")
    if entry in contexts:
        raise StrategyDrivenBacktestError("Entry interval must not also be a context interval.")
    if entry not in INTERVAL_MILLISECONDS or INTERVAL_MILLISECONDS[entry] is None:
        raise StrategyDrivenBacktestError("Entry interval must have a supported fixed duration.")
    for interval in contexts:
        if interval not in INTERVAL_MILLISECONDS or INTERVAL_MILLISECONDS[interval] is None:
            raise StrategyDrivenBacktestError(f"Context interval {interval!r} must have a supported fixed duration.")
    return entry, tuple(contexts)


def _validated_history(values: Sequence[Candle], symbol: str, interval: str) -> tuple[Candle, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence) or not values:
        raise StrategyDrivenBacktestError(f"Historical {interval} candles must be a non-empty sequence.")
    duration = INTERVAL_MILLISECONDS.get(interval)
    if duration is None:
        raise StrategyDrivenBacktestError(f"Interval {interval!r} is not supported for strategy-driven replay.")
    output = tuple(values)
    previous_open = None
    for candle in output:
        if not isinstance(candle, Candle):
            raise StrategyDrivenBacktestError("Historical input must contain normalized Candle objects.")
        if candle.symbol != symbol or candle.interval != interval:
            raise StrategyDrivenBacktestError("Historical candle symbol/interval does not match its input key.")
        if candle.open_time_ms % duration != 0 or candle.close_time_ms != candle.open_time_ms + duration - 1:
            raise StrategyDrivenBacktestError("Historical candle timestamps are not aligned to interval boundaries.")
        if previous_open is not None and candle.open_time_ms <= previous_open:
            raise StrategyDrivenBacktestError("Historical candles must be strictly chronological without duplicates.")
        previous_open = candle.open_time_ms
    return output


def _batch_at(
    history: Sequence[Candle], *, symbol: str, interval: str, decision_time_ms: int,
    not_after_close_ms: int | None = None,
) -> CandleBatch | None:
    duration = INTERVAL_MILLISECONDS[interval]
    assert duration is not None
    eligible = tuple(
        candle for candle in history
        if candle.close_time_ms < decision_time_ms
        and (not_after_close_ms is None or candle.close_time_ms <= not_after_close_ms)
    )
    if not eligible:
        return None
    gaps: list[CandleGap] = []
    for previous, current in zip(eligible, eligible[1:]):
        expected = previous.open_time_ms + duration
        if current.open_time_ms != expected:
            missing = max(0, (current.open_time_ms - previous.open_time_ms) // duration - 1)
            gaps.append(CandleGap(previous.open_time_ms, current.open_time_ms, expected, missing))
    age = decision_time_ms - eligible[-1].close_time_ms
    # Historical freshness is measured against the simulated decision time, not wall time.
    stale = age < 0 or age > duration * 2
    return CandleBatch(
        symbol=symbol,
        interval=interval,
        requested_limit=len(eligible),
        received_count=len(eligible),
        candles=eligible,
        incomplete_candles_dropped=0,
        gaps=tuple(gaps),
        is_stale=stale,
        latest_candle_age_ms=age,
        as_of_time_ms=decision_time_ms,
    )


def build_strategy_decision_points(
    *,
    style: str,
    config: Any,
    historical_candles: Mapping[str, Sequence[Candle]],
    symbol: str,
    portfolio: Any,
    risk_manager: Any,
    risk_context: RiskContext,
    cost_provider: Callable[[str, int], MarketCostEstimate | None],
) -> tuple[tuple[Candle, ...], tuple[DecisionPoint, ...]]:
    """Build causal decision points from full historical candle series.

    ``historical_candles`` maps interval strings to normalized candles for one symbol.
    ``cost_provider(symbol, decision_time_ms)`` must return an explicit cost estimate
    observed no later than the decision time, or None (which blocks entries).
    """
    if not isinstance(symbol, str) or not symbol.strip():
        raise StrategyDrivenBacktestError("symbol must be a non-empty string.")
    if not isinstance(getattr(config, "enabled", None), bool) or not config.enabled:
        raise StrategyDrivenBacktestError("Strategy config must be explicitly enabled for backtesting.")
    if not isinstance(risk_context, RiskContext):
        raise StrategyDrivenBacktestError("An explicit RiskContext is required.")
    if not callable(cost_provider):
        raise StrategyDrivenBacktestError("A cost_provider callback is required; zero costs are never assumed.")
    entry_interval, context_intervals = _strategy_intervals(style, config)
    required_intervals = (entry_interval, *context_intervals)
    normalized: dict[str, tuple[Candle, ...]] = {}
    for interval in required_intervals:
        if interval not in historical_candles:
            raise StrategyDrivenBacktestError(f"Missing historical candle series for required interval {interval}.")
        normalized[interval] = _validated_history(historical_candles[interval], symbol, interval)
    replay_candles = normalized[entry_interval]
    points: list[DecisionPoint] = []
    for replay_candle in replay_candles:
        boundary = replay_candle.open_time_ms
        entry_batch = _batch_at(normalized[entry_interval], symbol=symbol, interval=entry_interval, decision_time_ms=boundary)
        if entry_batch is None or not entry_batch.candles:
            continue
        last_entry_close = entry_batch.candles[-1].close_time_ms
        context_batches: dict[str, CandleBatch] = {}
        context_ready = True
        for interval in context_intervals:
            batch = _batch_at(
                normalized[interval], symbol=symbol, interval=interval,
                decision_time_ms=boundary, not_after_close_ms=last_entry_close,
            )
            if batch is None:
                context_ready = False
                break
            context_batches[interval] = batch
        if not context_ready:
            continue
        regime = assess_market_regime(entry_batch)
        cost_estimate = cost_provider(symbol, boundary)
        if cost_estimate is not None and not isinstance(cost_estimate, MarketCostEstimate):
            raise StrategyDrivenBacktestError("cost_provider must return MarketCostEstimate or None.")
        points.append(DecisionPoint(
            decision_time_ms=boundary,
            style=style,
            entry_batch=entry_batch,
            context_batches=context_batches,
            regime=regime,
            config=config,
            cost_estimate=cost_estimate,
            portfolio=portfolio,
            risk_manager=risk_manager,
            risk_context=risk_context,
            position_open=False,
        ))
    if not points:
        raise StrategyDrivenBacktestError("No causal decision points could be built; check candle history and warm-up requirements.")
    return replay_candles, tuple(points)


def run_strategy_historical_backtest(
    *,
    style: str,
    config: Any,
    historical_candles: Mapping[str, Sequence[Candle]],
    symbol: str,
    initial_cash: Decimal,
    portfolio: Any,
    risk_manager: Any,
    risk_context: RiskContext,
    cost_provider: Callable[[str, int], MarketCostEstimate | None],
    replay_callable: Callable[..., Any] | None = None,
) -> OrchestrationReport:
    """Run a strategy-driven historical replay through the existing risk/accounting path."""
    replay_candles, points = build_strategy_decision_points(
        style=style, config=config, historical_candles=historical_candles,
        symbol=symbol, portfolio=portfolio, risk_manager=risk_manager,
        risk_context=risk_context, cost_provider=cost_provider,
    )
    kwargs: dict[str, Any] = {"symbol": symbol, "interval": replay_candles[0].interval}
    extra: dict[str, Any] = {}
    if replay_callable is not None:
        extra["replay_callable"] = replay_callable
    return run_stateful_historical_orchestration(
        candles=replay_candles, decision_points=points, initial_cash=initial_cash,
        replay_kwargs=kwargs, **extra,
    )
