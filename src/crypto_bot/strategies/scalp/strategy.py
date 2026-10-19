"""Conservative, closed-candle scalp strategy research baseline."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import ClassVar, Mapping

from crypto_bot.market_data.candles import CandleBatch
from crypto_bot.market_intelligence.regime import RegimeLabel
from crypto_bot.strategies.common import (
    MarketCostEstimate,
    StrategyAction,
    StrategyConfig,
    StrategySignal,
    build_entry_candidate,
    build_exit_candidate,
    no_action,
    prepare_inputs,
)


@dataclass(frozen=True)
class ScalpStrategyConfig(StrategyConfig):
    """Initial research thresholds; disabled until explicitly enabled."""

    rsi_entry_min: float = 50.0
    rsi_entry_max: float = 68.0
    stop_lookback: int = 5
    reward_risk_multiple: float = 1.25
    min_gross_reward_to_cost_ratio: float = 2.0
    max_cost_age_ms: int = 60_000
    rsi_exit_threshold: float = 75.0

    style: ClassVar[str] = "scalp"
    version: ClassVar[str] = "1.0.0"

    def __post_init__(self) -> None:
        super().__post_init__()
        if isinstance(self.rsi_exit_threshold, bool) or not isinstance(self.rsi_exit_threshold, (int, float)) or not math.isfinite(float(self.rsi_exit_threshold)) or not 0 < self.rsi_exit_threshold <= 100:
            raise ValueError("rsi_exit_threshold must be finite and in (0, 100].")


def evaluate_scalp_strategy(
    entry_batch: CandleBatch,
    context_batches: Mapping[str, CandleBatch] | None = None,
    regime=None,
    *,
    cost_estimate: MarketCostEstimate | None = None,
    position_open: bool = False,
    config: ScalpStrategyConfig = ScalpStrategyConfig(),
) -> StrategySignal:
    """Return a scalp entry/exit candidate or NO_ACTION; never submit an order."""
    if not isinstance(position_open, bool):
        return no_action("scalp", config, ("position_open must be a boolean; evaluation failed closed.",), entry_batch if isinstance(entry_batch, CandleBatch) else None)
    prepared, blocked, interval, timeframes = prepare_inputs("scalp", entry_batch, context_batches, regime, config)
    if prepared is None:
        return no_action("scalp", config, blocked, entry_batch if isinstance(entry_batch, CandleBatch) else None, interval, timeframes)

    previous, current = prepared.previous, prepared.current
    bearish_cross = (
        previous.ema_short is not None and previous.ema_long is not None
        and current.ema_short is not None and current.ema_long is not None
        and previous.ema_short >= previous.ema_long and current.ema_short < current.ema_long
    )
    bearish_momentum = current.macd is not None and current.macd_signal is not None and current.macd < current.macd_signal and current.rsi is not None and current.rsi < 50
    if position_open:
        if bearish_cross or bearish_momentum or current.rsi is not None and current.rsi >= config.rsi_exit_threshold or prepared.regime.regime is RegimeLabel.DOWNWARD_TREND:
            reasons = []
            if bearish_cross:
                reasons.append("Scalp exit: fast EMA crossed below slow EMA on a closed entry candle.")
            if bearish_momentum:
                reasons.append("Scalp exit: MACD is below signal and RSI is below 50.")
            if current.rsi is not None and current.rsi >= config.rsi_exit_threshold:
                reasons.append(f"Scalp exit: RSI reached the configured {config.rsi_exit_threshold:.1f} exhaustion threshold.")
            if prepared.regime.regime is RegimeLabel.DOWNWARD_TREND:
                reasons.append("Scalp exit: the matching market-regime assessment is a downward trend.")
            return build_exit_candidate(prepared, reasons)
        return no_action("scalp", config, ("No configured scalp exit condition is currently met.",), prepared.entry_batch, prepared.mapping_entry_interval, prepared.timeframes_used)

    checks = (
        (previous.ema_short is not None and previous.ema_long is not None and current.ema_short is not None and current.ema_long is not None and previous.ema_short <= previous.ema_long and current.ema_short > current.ema_long, "fast EMA crossed above slow EMA"),
        (current.macd is not None and current.macd_signal is not None and current.macd > current.macd_signal, "MACD is above its signal line"),
        (current.rsi is not None and config.rsi_entry_min <= current.rsi <= config.rsi_entry_max, f"RSI is within {config.rsi_entry_min:.1f}–{config.rsi_entry_max:.1f}"),
        (current.volume_above_average is True, "closed-candle volume is above the shared indicator average"),
        (all(snapshot.ema_short is not None and snapshot.ema_long is not None and snapshot.ema_short > snapshot.ema_long for snapshot in prepared.context_indicators.values()), "all higher-timeframe EMA contexts are bullish"),
        (prepared.regime.regime is RegimeLabel.UPWARD_TREND, "matching market regime is upward_trend"),
    )
    failed = tuple(f"Scalp entry gate not met: {description}." for ok, description in checks if not ok)
    if failed:
        return no_action("scalp", config, failed, prepared.entry_batch, prepared.mapping_entry_interval, prepared.timeframes_used)
    return build_entry_candidate(prepared, cost_estimate, tuple(description for _, description in checks))