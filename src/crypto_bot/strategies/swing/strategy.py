"""Conservative, closed-candle swing-trading research baseline."""
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
class SwingStrategyConfig(StrategyConfig):
    """Initial research thresholds; disabled until explicitly enabled."""

    rsi_entry_min: float = 50.0
    rsi_entry_max: float = 72.0
    stop_lookback: int = 20
    reward_risk_multiple: float = 2.0
    min_gross_reward_to_cost_ratio: float = 1.25
    max_cost_age_ms: int = 3_600_000
    breakout_lookback: int = 20

    style: ClassVar[str] = "swing"
    version: ClassVar[str] = "1.0.0"

    def __post_init__(self) -> None:
        super().__post_init__()
        if isinstance(self.breakout_lookback, bool) or not isinstance(self.breakout_lookback, int) or self.breakout_lookback < 2:
            raise ValueError("breakout_lookback must be an integer >= 2.")
        if self.stop_lookback < self.breakout_lookback:
            raise ValueError("stop_lookback must be at least breakout_lookback for the swing baseline.")


def evaluate_swing_strategy(
    entry_batch: CandleBatch,
    context_batches: Mapping[str, CandleBatch] | None = None,
    regime=None,
    *,
    cost_estimate: MarketCostEstimate | None = None,
    position_open: bool = False,
    config: SwingStrategyConfig = SwingStrategyConfig(),
) -> StrategySignal:
    """Return a swing entry/exit candidate or NO_ACTION; never submit an order."""
    if not isinstance(position_open, bool):
        return no_action("swing", config, ("position_open must be a boolean; evaluation failed closed.",), entry_batch if isinstance(entry_batch, CandleBatch) else None)
    prepared, blocked, interval, timeframes = prepare_inputs("swing", entry_batch, context_batches, regime, config)
    if prepared is None:
        return no_action("swing", config, blocked, entry_batch if isinstance(entry_batch, CandleBatch) else None, interval, timeframes)

    current = prepared.current
    lookback = config.breakout_lookback
    prior_candles = prepared.entry_candles[-(lookback + 1):-1]
    prior_high = max(float(candle.high) for candle in prior_candles)
    prior_low = min(float(candle.low) for candle in prior_candles)
    current_close = float(prepared.entry_candles[-1].close)
    breakdown = current_close < prior_low
    bearish_ema = current.ema_short is not None and current.ema_long is not None and current.ema_short < current.ema_long
    bearish_macd = current.macd is not None and current.macd_signal is not None and current.macd < current.macd_signal

    if position_open:
        if breakdown or bearish_ema and bearish_macd or prepared.regime.regime is RegimeLabel.DOWNWARD_TREND:
            reasons = []
            if breakdown:
                reasons.append(f"Swing exit: close fell below the prior {lookback}-candle structural low.")
            if bearish_ema and bearish_macd:
                reasons.append("Swing exit: fast EMA and MACD both confirm bearish momentum.")
            if prepared.regime.regime is RegimeLabel.DOWNWARD_TREND:
                reasons.append("Swing exit: the matching market-regime assessment is a downward trend.")
            return build_exit_candidate(prepared, reasons)
        return no_action("swing", config, ("No configured swing exit condition is currently met.",), prepared.entry_batch, prepared.mapping_entry_interval, prepared.timeframes_used)

    checks = (
        (current_close > prior_high, f"close broke above the prior {lookback}-candle high"),
        (current.close is not None and current.bollinger_middle is not None and current.close > current.bollinger_middle, "close is above the Bollinger middle line"),
        (current.ema_short is not None and current.ema_long is not None and current.ema_short > current.ema_long, "fast EMA is above slow EMA"),
        (current.macd is not None and current.macd_signal is not None and current.macd > current.macd_signal, "MACD is above its signal line"),
        (current.rsi is not None and config.rsi_entry_min <= current.rsi <= config.rsi_entry_max, f"RSI is within {config.rsi_entry_min:.1f}–{config.rsi_entry_max:.1f}"),
        (all(snapshot.ema_short is not None and snapshot.ema_long is not None and snapshot.ema_short > snapshot.ema_long and snapshot.macd is not None and snapshot.macd_signal is not None and snapshot.macd > snapshot.macd_signal for snapshot in prepared.context_indicators.values()), "4h/daily context EMA and MACD filters are bullish"),
        (prepared.regime.regime is RegimeLabel.UPWARD_TREND, "matching market regime is upward_trend"),
    )
    failed = tuple(f"Swing entry gate not met: {description}." for ok, description in checks if not ok)
    if failed:
        return no_action("swing", config, failed, prepared.entry_batch, prepared.mapping_entry_interval, prepared.timeframes_used)
    return build_entry_candidate(prepared, cost_estimate, tuple(description for _, description in checks))