"""Conservative, closed-candle day-trading research baseline."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import ClassVar, Mapping

from crypto_bot.market_data.candles import CandleBatch
from crypto_bot.market_intelligence.regime import RegimeLabel
from crypto_bot.strategies.common import (
    MarketCostEstimate,
    StrategyConfig,
    StrategySignal,
    build_entry_candidate,
    build_exit_candidate,
    no_action,
    prepare_inputs,
)


@dataclass(frozen=True)
class DayStrategyConfig(StrategyConfig):
    """Initial research thresholds; disabled until explicitly enabled."""

    rsi_entry_min: float = 52.0
    rsi_entry_max: float = 68.0
    stop_lookback: int = 10
    reward_risk_multiple: float = 1.75
    min_gross_reward_to_cost_ratio: float = 1.5
    max_cost_age_ms: int = 300_000
    rsi_exit_threshold: float = 45.0
    # None means user policy is unresolved; new entries fail closed until set.
    allow_overnight: bool | None = None

    style: ClassVar[str] = "day"
    version: ClassVar[str] = "1.0.0"

    def __post_init__(self) -> None:
        super().__post_init__()
        if isinstance(self.rsi_exit_threshold, bool) or not isinstance(self.rsi_exit_threshold, (int, float)) or not math.isfinite(float(self.rsi_exit_threshold)) or not 0 <= self.rsi_exit_threshold < self.rsi_entry_min:
            raise ValueError("rsi_exit_threshold must be finite, non-negative, and below rsi_entry_min.")
        if self.allow_overnight is not None and not isinstance(self.allow_overnight, bool):
            raise ValueError("allow_overnight must be True, False, or None while undecided.")


def evaluate_day_strategy(
    entry_batch: CandleBatch,
    context_batches: Mapping[str, CandleBatch] | None = None,
    regime=None,
    *,
    cost_estimate: MarketCostEstimate | None = None,
    position_open: bool = False,
    config: DayStrategyConfig = DayStrategyConfig(),
) -> StrategySignal:
    """Return a day-trading entry/exit candidate or NO_ACTION; no orders are sent."""
    if not isinstance(position_open, bool):
        return no_action("day", config, ("position_open must be a boolean; evaluation failed closed.",), entry_batch if isinstance(entry_batch, CandleBatch) else None)
    prepared, blocked, interval, timeframes = prepare_inputs("day", entry_batch, context_batches, regime, config)
    if prepared is None:
        return no_action("day", config, blocked, entry_batch if isinstance(entry_batch, CandleBatch) else None, interval, timeframes)

    previous, current = prepared.previous, prepared.current
    if not position_open and config.allow_overnight is None:
        return no_action(
            "day",
            config,
            ("Day-trading overnight-holding policy is unresolved; explicitly set allow_overnight before permitting new entries.",),
            prepared.entry_batch,
            prepared.mapping_entry_interval,
            prepared.timeframes_used,
        )
    close_crossed_below_middle = (
        previous.close is not None and previous.bollinger_middle is not None
        and current.close is not None and current.bollinger_middle is not None
        and previous.close >= previous.bollinger_middle and current.close < current.bollinger_middle
    )
    bearish_momentum = current.macd is not None and current.macd_signal is not None and current.macd < current.macd_signal and current.rsi is not None and current.rsi < config.rsi_exit_threshold
    if position_open:
        if close_crossed_below_middle or bearish_momentum or prepared.regime.regime is RegimeLabel.DOWNWARD_TREND:
            reasons = []
            if close_crossed_below_middle:
                reasons.append("Day-trading exit: close crossed below the Bollinger middle line.")
            if bearish_momentum:
                reasons.append(f"Day-trading exit: MACD is below signal and RSI is below {config.rsi_exit_threshold:.1f}.")
            if prepared.regime.regime is RegimeLabel.DOWNWARD_TREND:
                reasons.append("Day-trading exit: the matching market-regime assessment is a downward trend.")
            return build_exit_candidate(prepared, reasons)
        return no_action("day", config, ("No configured day-trading exit condition is currently met.",), prepared.entry_batch, prepared.mapping_entry_interval, prepared.timeframes_used)

    crossed_above_middle = (
        previous.close is not None and previous.bollinger_middle is not None
        and current.close is not None and current.bollinger_middle is not None
        and previous.close <= previous.bollinger_middle and current.close > current.bollinger_middle
    )
    checks = (
        (crossed_above_middle, "closed price crossed above the Bollinger middle line"),
        (current.ema_short is not None and current.ema_long is not None and current.ema_short > current.ema_long, "fast EMA is above slow EMA"),
        (current.macd is not None and current.macd_signal is not None and current.macd > current.macd_signal, "MACD is above its signal line"),
        (current.rsi is not None and config.rsi_entry_min <= current.rsi <= config.rsi_entry_max, f"RSI is within {config.rsi_entry_min:.1f}–{config.rsi_entry_max:.1f}"),
        (current.volume_above_average is True, "closed-candle volume is above the shared indicator average"),
        (all(snapshot.ema_short is not None and snapshot.ema_long is not None and snapshot.ema_short > snapshot.ema_long for snapshot in prepared.context_indicators.values()), "intraday context EMA filters are bullish"),
        (prepared.regime.regime is RegimeLabel.UPWARD_TREND, "matching market regime is upward_trend"),
    )
    failed = tuple(f"Day entry gate not met: {description}." for ok, description in checks if not ok)
    if failed:
        return no_action("day", config, failed, prepared.entry_batch, prepared.mapping_entry_interval, prepared.timeframes_used)
    return build_entry_candidate(prepared, cost_estimate, tuple(description for _, description in checks))