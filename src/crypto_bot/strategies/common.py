"""Shared data-quality gates and advisory models for the three spot strategies.

Strategy functions return research proposals only. They cannot submit orders or
change sizing/risk controls. Every new entry requires a fresh explicit cost
estimate; missing costs never silently become zero.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Mapping, Sequence

from crypto_bot.config.timeframe_config import validate_single_interval
from crypto_bot.indicators.engine import (
    IndicatorConfig,
    IndicatorInputError,
    IndicatorSnapshot,
    calculate_indicator_series,
    required_warmup_candles,
)
from crypto_bot.market_data.candles import Candle, CandleBatch, INTERVAL_MILLISECONDS
from crypto_bot.market_data.timeframe_alignment import configured_strategy_timeframes
from crypto_bot.market_intelligence.regime import DataQuality, RegimeAssessment, RegimeLabel


class StrategyAction(str, Enum):
    """Advisory actions; EXIT_CANDIDATE never means open a short position."""

    BUY_CANDIDATE = "buy_candidate"
    EXIT_CANDIDATE = "exit_candidate"
    NO_ACTION = "no_action"


@dataclass(frozen=True)
class StrategyConfig:
    """Base configuration. Every concrete strategy is disabled by default."""

    enabled: bool = False
    entry_interval: str | None = None
    context_intervals: tuple[str, ...] | None = None
    rsi_entry_min: float = 50.0
    rsi_entry_max: float = 68.0
    stop_lookback: int = 8
    reward_risk_multiple: float = 1.5
    min_gross_reward_to_cost_ratio: float = 2.0
    max_cost_age_ms: int = 60_000

    style: ClassVar[str] = "base"
    version: ClassVar[str] = "1.0.0"

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled must be a boolean.")
        if self.entry_interval is not None:
            validate_single_interval(self.entry_interval, field_name="entry_interval")
        if self.context_intervals is not None:
            if not isinstance(self.context_intervals, tuple) or not self.context_intervals:
                raise ValueError("context_intervals must be a non-empty tuple when supplied.")
            checked = tuple(
                validate_single_interval(interval, field_name="context_intervals")
                for interval in self.context_intervals
            )
            if len(set(checked)) != len(checked):
                raise ValueError("context_intervals must not contain duplicates.")
            if self.entry_interval is not None and self.entry_interval in checked:
                raise ValueError("entry_interval must not also be a context interval.")
        for name in ("rsi_entry_min", "rsi_entry_max", "reward_risk_multiple", "min_gross_reward_to_cost_ratio"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"{name} must be a finite number.")
        if not (0 <= self.rsi_entry_min < self.rsi_entry_max <= 100):
            raise ValueError("RSI entry bounds must satisfy 0 <= min < max <= 100.")
        if self.reward_risk_multiple <= 0:
            raise ValueError("reward_risk_multiple must be greater than zero.")
        if self.min_gross_reward_to_cost_ratio <= 0:
            raise ValueError("min_gross_reward_to_cost_ratio must be greater than zero.")
        if isinstance(self.stop_lookback, bool) or not isinstance(self.stop_lookback, int) or self.stop_lookback < 2:
            raise ValueError("stop_lookback must be an integer >= 2.")
        if isinstance(self.max_cost_age_ms, bool) or not isinstance(self.max_cost_age_ms, int) or self.max_cost_age_ms < 1:
            raise ValueError("max_cost_age_ms must be a positive integer.")


@dataclass(frozen=True)
class MarketCostEstimate:
    """Caller-supplied round-trip cost inputs, in basis points.

    spread_bps is the full bid/ask spread width. round_trip_fee_bps and
    round_trip_slippage_bps already represent round-trip estimates; callers must
    not pass per-side values into those two fields.
    """

    spread_bps: float
    round_trip_fee_bps: float
    round_trip_slippage_bps: float
    observed_at_ms: int
    source: str = "caller_supplied_estimate"

    def __post_init__(self) -> None:
        for name in ("spread_bps", "round_trip_fee_bps", "round_trip_slippage_bps"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative.")
        if isinstance(self.observed_at_ms, bool) or not isinstance(self.observed_at_ms, int) or self.observed_at_ms < 0:
            raise ValueError("observed_at_ms must be a non-negative integer timestamp in milliseconds.")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("source must be a non-empty description.")

    @property
    def estimated_round_trip_cost_bps(self) -> float:
        return float(self.spread_bps + self.round_trip_fee_bps + self.round_trip_slippage_bps)


@dataclass(frozen=True)
class StrategySignal:
    """Explainable advisory result, not an order or a risk authorization."""

    style: str
    symbol: str | None
    entry_interval: str | None
    action: StrategyAction
    decision_time_ms: int | None
    latest_candle_close_time_ms: int | None
    reference_price: float | None
    stop_price: float | None
    target_price: float | None
    reward_risk_ratio: float | None
    gross_target_bps: float | None
    estimated_round_trip_cost_bps: float | None
    estimated_net_reward_bps: float | None
    strategy_version: str
    timeframes_used: tuple[str, ...]
    deduplication_key: str | None
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class PreparedStrategyInputs:
    style: str
    config: StrategyConfig
    entry_batch: CandleBatch
    context_batches: Mapping[str, CandleBatch]
    mapping_entry_interval: str
    mapping_context_intervals: tuple[str, ...]
    entry_candles: tuple[Candle, ...]
    entry_indicators: tuple[IndicatorSnapshot, ...]
    context_indicators: Mapping[str, IndicatorSnapshot]
    regime: RegimeAssessment

    @property
    def current(self) -> IndicatorSnapshot:
        return self.entry_indicators[-1]

    @property
    def previous(self) -> IndicatorSnapshot:
        return self.entry_indicators[-2]

    @property
    def latest_close_time_ms(self) -> int:
        return self.entry_candles[-1].close_time_ms

    @property
    def decision_time_ms(self) -> int:
        # Timestamp the proposal no earlier than any input snapshot it used.
        candidate_times = [self.latest_close_time_ms + 1, self.entry_batch.as_of_time_ms]
        candidate_times.extend(
            self.context_batches[interval].as_of_time_ms
            for interval in self.mapping_context_intervals
            if interval in self.context_batches and isinstance(self.context_batches[interval], CandleBatch)
        )
        if self.regime.assessment_time_ms is not None:
            candidate_times.append(self.regime.assessment_time_ms)
        return max(candidate_times)

    @property
    def timeframes_used(self) -> tuple[str, ...]:
        return (self.mapping_entry_interval, *self.mapping_context_intervals)


def _build_signal(
    *,
    style: str,
    action: StrategyAction,
    reasons: Sequence[str],
    config: StrategyConfig,
    entry_batch: CandleBatch | None = None,
    entry_interval: str | None = None,
    timeframes_used: tuple[str, ...] = (),
    reference_price: float | None = None,
    stop_price: float | None = None,
    target_price: float | None = None,
    gross_target_bps: float | None = None,
    estimated_cost_bps: float | None = None,
    estimated_net_reward_bps: float | None = None,
    decision_time_ms: int | None = None,
) -> StrategySignal:
    candles = entry_batch.candles if isinstance(entry_batch, CandleBatch) else ()
    latest = candles[-1] if candles else None
    symbol = entry_batch.symbol if isinstance(entry_batch, CandleBatch) else None
    interval = entry_interval or (entry_batch.interval if isinstance(entry_batch, CandleBatch) else None)
    close_time = latest.close_time_ms if latest is not None else None
    decision = decision_time_ms
    if decision is None and isinstance(entry_batch, CandleBatch):
        close_plus_one = close_time + 1 if close_time is not None else 0
        decision = max(close_plus_one, entry_batch.as_of_time_ms)
    dedupe = None
    if action is not StrategyAction.NO_ACTION and symbol and interval and close_time is not None:
        dedupe = f"{style}:{symbol}:{interval}:{close_time}:{action.value}:v{config.version if hasattr(config, 'version') else '1.0.0'}"
    return StrategySignal(
        style=style,
        symbol=symbol,
        entry_interval=interval,
        action=action,
        decision_time_ms=decision,
        latest_candle_close_time_ms=close_time,
        reference_price=reference_price,
        stop_price=stop_price,
        target_price=target_price,
        reward_risk_ratio=config.reward_risk_multiple if action is StrategyAction.BUY_CANDIDATE else None,
        gross_target_bps=gross_target_bps,
        estimated_round_trip_cost_bps=estimated_cost_bps,
        estimated_net_reward_bps=estimated_net_reward_bps,
        strategy_version=f"{style}-baseline-1.0.0",
        timeframes_used=timeframes_used,
        deduplication_key=dedupe,
        reasons=tuple(str(reason) for reason in reasons),
    )


def no_action(
    style: str,
    config: StrategyConfig,
    reasons: Sequence[str],
    entry_batch: CandleBatch | None = None,
    entry_interval: str | None = None,
    timeframes_used: tuple[str, ...] = (),
) -> StrategySignal:
    """Create a fail-closed no-action result with explicit reasons."""
    return _build_signal(
        style=style,
        action=StrategyAction.NO_ACTION,
        reasons=reasons,
        config=config,
        entry_batch=entry_batch,
        entry_interval=entry_interval,
        timeframes_used=timeframes_used,
        reference_price=(float(entry_batch.candles[-1].close) if isinstance(entry_batch, CandleBatch) and entry_batch.candles else None),
    )


def _timeframe_mapping(style: str, config: StrategyConfig) -> tuple[str, tuple[str, ...]]:
    if config.entry_interval is not None and config.context_intervals is not None:
        entry = config.entry_interval
        contexts = config.context_intervals
    else:
        configured = configured_strategy_timeframes()[style]
        entry = config.entry_interval or configured.entry_interval
        contexts = config.context_intervals if config.context_intervals is not None else configured.context_intervals
    validate_single_interval(entry, field_name="entry_interval")
    checked_contexts = tuple(validate_single_interval(value, field_name="context_intervals") for value in contexts)
    if not checked_contexts or len(set(checked_contexts)) != len(checked_contexts):
        raise ValueError("At least one unique context interval is required.")
    if entry in checked_contexts:
        raise ValueError("Entry interval must be different from every context interval.")
    return entry, checked_contexts


def _validate_batch(batch: CandleBatch, *, style: str, expected_symbol: str, expected_interval: str, minimum_count: int, latest_not_after_ms: int | None = None) -> tuple[Candle, ...]:
    if not isinstance(batch, CandleBatch):
        raise ValueError("Every input must be a normalized CandleBatch.")
    if batch.symbol != expected_symbol or batch.interval != expected_interval:
        raise ValueError("CandleBatch symbol/interval does not match the strategy mapping.")
    if not batch.is_usable or batch.is_stale or batch.gaps:
        raise ValueError("CandleBatch is stale, gapped, empty, or otherwise unusable.")
    candles = tuple(batch.candles)
    if len(candles) < minimum_count:
        raise ValueError(f"Insufficient closed candles: {len(candles)} supplied, {minimum_count} required.")
    duration = INTERVAL_MILLISECONDS.get(expected_interval)
    if duration is None:
        raise ValueError("Candle interval has no known fixed duration.")
    previous: Candle | None = None
    for candle in candles:
        if not isinstance(candle, Candle):
            raise ValueError("CandleBatch must contain normalized Candle objects only.")
        if candle.symbol != expected_symbol or candle.interval != expected_interval:
            raise ValueError("Candle identity does not match the enclosing batch.")
        if candle.close_time_ms >= batch.as_of_time_ms:
            raise ValueError("CandleBatch contains a candle not confirmed closed as of its snapshot time.")
        if candle.open_time_ms % duration != 0:
            raise ValueError("Candle open timestamp is not aligned to its interval boundary.")
        if candle.close_time_ms != candle.open_time_ms + duration - 1:
            raise ValueError("Candle close timestamp does not match its interval duration.")
        if previous is not None and candle.open_time_ms != previous.open_time_ms + duration:
            raise ValueError("Candle sequence contains non-consecutive timestamps.")
        if latest_not_after_ms is not None and candle.close_time_ms > latest_not_after_ms:
            raise ValueError("Context candle would introduce future data relative to the entry decision.")
        previous = candle
    return candles


def prepare_inputs(
    style: str,
    entry_batch: CandleBatch,
    context_batches: Mapping[str, CandleBatch] | None,
    regime: RegimeAssessment | None,
    config: StrategyConfig,
) -> tuple[PreparedStrategyInputs | None, tuple[str, ...], str | None, tuple[str, ...]]:
    """Validate timestamps, freshness, timeframe alignment and shared features."""
    if not config.enabled:
        return None, ("Strategy is disabled by default; enable it explicitly for research/backtesting.",), None, ()
    if not isinstance(entry_batch, CandleBatch):
        return None, ("Entry data must be a normalized CandleBatch.",), None, ()
    try:
        entry_interval, context_intervals = _timeframe_mapping(style, config)
    except (KeyError, ValueError, TypeError) as exc:
        return None, (f"Strategy timeframe configuration is invalid: {exc}",), entry_batch.interval, ()
    symbol = entry_batch.symbol
    try:
        entry_candles = _validate_batch(
            entry_batch,
            style=style,
            expected_symbol=symbol,
            expected_interval=entry_interval,
            minimum_count=max(
                required_warmup_candles() + 1,
                config.stop_lookback + (1 if style == "swing" else 0),
            ),
        )
    except (ValueError, TypeError) as exc:
        return None, (f"Entry data gate failed: {exc}",), entry_interval, (entry_interval, *context_intervals)

    if not isinstance(regime, RegimeAssessment):
        return None, ("A matching market-regime assessment is required; no new strategy action is permitted without it.",), entry_interval, (entry_interval, *context_intervals)
    if regime.data_quality is not DataQuality.USABLE:
        return None, (f"Regime data quality is {regime.data_quality.value}; expected usable. No strategy action is permitted.",), entry_interval, (entry_interval, *context_intervals)
    if regime.symbol != symbol or regime.interval != entry_interval:
        return None, ("Regime assessment symbol/interval does not match the entry batch.",), entry_interval, (entry_interval, *context_intervals)
    if regime.latest_candle_close_time_ms != entry_candles[-1].close_time_ms:
        return None, ("Regime assessment is not aligned to the latest entry-candle close.",), entry_interval, (entry_interval, *context_intervals)

    if context_batches is None:
        context_batches = {}
    elif not isinstance(context_batches, Mapping):
        return None, ("Context batches must be a mapping keyed by interval.",), entry_interval, (entry_interval, *context_intervals)
    context_series: dict[str, IndicatorSnapshot] = {}
    for interval in context_intervals:
        batch = context_batches.get(interval)
        try:
            if not isinstance(batch, CandleBatch):
                raise ValueError("required context CandleBatch is missing.")
            context_candles = _validate_batch(
                batch,
                style=style,
                expected_symbol=symbol,
                expected_interval=interval,
                minimum_count=required_warmup_candles(),
                latest_not_after_ms=entry_candles[-1].close_time_ms,
            )
            series = calculate_indicator_series(context_candles, IndicatorConfig())
            snapshot = series[-1] if series else None
            if snapshot is None or not snapshot.is_warmed_up:
                raise ValueError("context indicators have not warmed up.")
            context_series[interval] = snapshot
        except (ValueError, TypeError, IndicatorInputError) as exc:
            return None, (f"Context {interval} data gate failed: {exc}",), entry_interval, (entry_interval, *context_intervals)

    try:
        entry_series = calculate_indicator_series(entry_candles, IndicatorConfig())
    except (ValueError, TypeError, IndicatorInputError) as exc:
        return None, (f"Entry indicator calculation failed: {exc}",), entry_interval, (entry_interval, *context_intervals)
    if len(entry_series) < 2 or not entry_series[-1].is_warmed_up or not entry_series[-2].is_warmed_up:
        return None, ("Entry indicators are not warmed up for a closed-candle signal.",), entry_interval, (entry_interval, *context_intervals)

    return PreparedStrategyInputs(
        style=style,
        config=config,
        entry_batch=entry_batch,
        context_batches=context_batches,
        mapping_entry_interval=entry_interval,
        mapping_context_intervals=context_intervals,
        entry_candles=entry_candles,
        entry_indicators=entry_series,
        context_indicators=context_series,
        regime=regime,
    ), (), entry_interval, (entry_interval, *context_intervals)


def bullish_context_agrees(prepared: PreparedStrategyInputs, *, require_macd: bool = False) -> bool:
    for snapshot in prepared.context_indicators.values():
        if snapshot.ema_short is None or snapshot.ema_long is None or snapshot.ema_short <= snapshot.ema_long:
            return False
        if require_macd and (snapshot.macd is None or snapshot.macd_signal is None or snapshot.macd <= snapshot.macd_signal):
            return False
    return True


def build_entry_candidate(
    prepared: PreparedStrategyInputs,
    cost_estimate: MarketCostEstimate | None,
    rule_reasons: Sequence[str] = (),
) -> StrategySignal:
    """Build a proposed long entry only when costs are explicit, fresh and justified."""
    config = prepared.config
    current = prepared.current
    batch = prepared.entry_batch
    timeframes = prepared.timeframes_used
    entry_price = float(prepared.entry_candles[-1].close)
    if cost_estimate is None:
        return no_action(prepared.style, config, ("Entry blocked: no explicit spread, round-trip fee and slippage estimate was supplied; zero costs are never assumed.",), batch, prepared.mapping_entry_interval, timeframes)
    if not isinstance(cost_estimate, MarketCostEstimate):
        return no_action(prepared.style, config, ("Entry blocked: cost estimate has the wrong data type.",), batch, prepared.mapping_entry_interval, timeframes)
    age_ms = batch.as_of_time_ms - cost_estimate.observed_at_ms
    if age_ms < 0 or age_ms > config.max_cost_age_ms:
        return no_action(prepared.style, config, (f"Entry blocked: cost estimate is future-dated or stale (age {age_ms} ms; limit {config.max_cost_age_ms} ms).",), batch, prepared.mapping_entry_interval, timeframes)

    stop_lookback = min(config.stop_lookback, len(prepared.entry_candles))
    stop_price = min(float(candle.low) for candle in prepared.entry_candles[-stop_lookback:])
    if not (math.isfinite(entry_price) and math.isfinite(stop_price) and 0 < stop_price < entry_price):
        return no_action(prepared.style, config, ("Entry blocked: structural stop is not below the reference close; no safe long setup can be defined.",), batch, prepared.mapping_entry_interval, timeframes)
    target_price = entry_price + (entry_price - stop_price) * config.reward_risk_multiple
    gross_target_bps = (target_price - entry_price) / entry_price * 10_000
    estimated_cost_bps = cost_estimate.estimated_round_trip_cost_bps
    net_reward_bps = gross_target_bps - estimated_cost_bps
    required_gross_bps = estimated_cost_bps * config.min_gross_reward_to_cost_ratio
    if not math.isfinite(gross_target_bps) or gross_target_bps <= 0 or gross_target_bps < required_gross_bps or net_reward_bps <= 0:
        return no_action(
            prepared.style,
            config,
            (f"Entry blocked by cost gate: gross target movement {gross_target_bps:.3f} bps must exceed estimated round-trip cost {estimated_cost_bps:.3f} bps by factor {config.min_gross_reward_to_cost_ratio:.2f}.",),
            batch,
            prepared.mapping_entry_interval,
            timeframes,
        )
    return _build_signal(
        style=prepared.style,
        action=StrategyAction.BUY_CANDIDATE,
        reasons=(
            "Closed-candle entry rule and required context filters are satisfied.",
            *(f"Entry condition met: {description}." for description in rule_reasons),
            f"Gross target estimate {gross_target_bps:.3f} bps; estimated round-trip cost {estimated_cost_bps:.3f} bps; estimated net reward {net_reward_bps:.3f} bps.",
            "Structural stop and target are reference levels only; the central risk manager and execution validation must independently approve any later order.",
        ),
        config=config,
        entry_batch=batch,
        entry_interval=prepared.mapping_entry_interval,
        timeframes_used=timeframes,
        reference_price=entry_price,
        stop_price=stop_price,
        target_price=target_price,
        gross_target_bps=gross_target_bps,
        estimated_cost_bps=estimated_cost_bps,
        estimated_net_reward_bps=net_reward_bps,
        decision_time_ms=prepared.decision_time_ms,
    )


def build_exit_candidate(prepared: PreparedStrategyInputs, reasons: Sequence[str]) -> StrategySignal:
    """Build an advisory spot-position exit candidate without inventing a short."""
    return _build_signal(
        style=prepared.style,
        action=StrategyAction.EXIT_CANDIDATE,
        reasons=tuple(reasons) + ("Exit is advisory and may only reduce an existing eligible spot position; no short position may be opened.",),
        config=prepared.config,
        entry_batch=prepared.entry_batch,
        entry_interval=prepared.mapping_entry_interval,
        timeframes_used=prepared.timeframes_used,
        reference_price=float(prepared.entry_candles[-1].close),
        decision_time_ms=prepared.decision_time_ms,
    )