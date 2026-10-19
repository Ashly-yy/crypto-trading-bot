"""Deterministic tests for the advisory scalp/day/swing strategy baselines."""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Mapping

import pytest

from crypto_bot.indicators.engine import IndicatorSnapshot
from crypto_bot.market_data.candles import Candle, CandleBatch, CandleGap, INTERVAL_MILLISECONDS
from crypto_bot.market_intelligence.regime import DataQuality, RegimeAssessment, RegimeLabel, VolatilityLabel, VolumeActivity
from crypto_bot.strategies import (
    DayStrategyConfig,
    MarketCostEstimate,
    ScalpStrategyConfig,
    StrategyAction,
    SwingStrategyConfig,
    evaluate_day_strategy,
    evaluate_scalp_strategy,
    evaluate_swing_strategy,
)
import crypto_bot.strategies.common as common

BASE_TIME_MS = 1_780_000_000_000


def make_batch(symbol: str, interval: str, count: int = 50, *, not_after_ms: int = BASE_TIME_MS, is_stale: bool = False, gaps: tuple[CandleGap, ...] = (), last_close_override: float | None = 101.0) -> CandleBatch:
    duration = INTERVAL_MILLISECONDS[interval]
    latest_open = ((not_after_ms - (duration - 1)) // duration) * duration
    first_open = latest_open - (count - 1) * duration
    candles = []
    for index in range(count):
        open_time = first_open + index * duration
        close_time = open_time + duration - 1
        close = 99.0 + index * 0.015
        if index == count - 1 and last_close_override is not None:
            close = last_close_override
        opening = close - 0.05
        high = max(opening, close) + 0.2
        low = min(opening, close) - 0.2
        volume = Decimal("125")
        candles.append(Candle(
            symbol=symbol,
            interval=interval,
            open_time_ms=open_time,
            open=Decimal(str(opening)),
            high=Decimal(str(high)),
            low=Decimal(str(low)),
            close=Decimal(str(close)),
            volume=volume,
            close_time_ms=close_time,
            quote_asset_volume=Decimal("12500"),
            number_of_trades=50,
            taker_buy_base_volume=Decimal("60"),
            taker_buy_quote_volume=Decimal("6000"),
        ))
    latest_close = candles[-1].close_time_ms
    return CandleBatch(
        symbol=symbol,
        interval=interval,
        requested_limit=count,
        received_count=count,
        candles=tuple(candles),
        incomplete_candles_dropped=0,
        gaps=gaps,
        is_stale=is_stale,
        latest_candle_age_ms=500,
        as_of_time_ms=latest_close + 1000,
    )


def make_snapshot(candle: Candle, *, ema_short: float, ema_long: float, macd: float, signal: float, rsi: float = 60.0, middle: float = 100.0, volume_above: bool | None = True) -> IndicatorSnapshot:
    close = float(candle.close)
    return IndicatorSnapshot(
        symbol=candle.symbol,
        interval=candle.interval,
        open_time_ms=candle.open_time_ms,
        close_time_ms=candle.close_time_ms,
        close=close,
        ema_short=ema_short,
        ema_long=ema_long,
        macd=macd,
        macd_signal=signal,
        macd_histogram=macd - signal,
        rsi=rsi,
        bollinger_upper=middle + 5,
        bollinger_middle=middle,
        bollinger_lower=middle - 5,
        volume_sma=100.0,
        volume_above_average=volume_above,
    )


def setup_synthetic_indicators(monkeypatch, entry_interval: str, contexts: tuple[str, ...], *, entry_previous=None, entry_current=None, context_bullish: bool = True, context_macd_bullish: bool = True):
    snapshots: dict[str, tuple[IndicatorSnapshot, IndicatorSnapshot]] = {}

    def fake_series(candles, config):
        interval = candles[0].interval
        if interval == entry_interval:
            previous = entry_previous or make_snapshot(candles[-2], ema_short=99, ema_long=100, macd=0.5, signal=1.0, rsi=55, middle=100, volume_above=False)
            current = entry_current or make_snapshot(candles[-1], ema_short=101, ema_long=100, macd=2.0, signal=1.0, rsi=60, middle=100, volume_above=True)
        elif interval in contexts:
            current = make_snapshot(candles[-1], ema_short=101 if context_bullish else 99, ema_long=100, macd=2 if context_macd_bullish else 0.2, signal=1.0, rsi=58, middle=100, volume_above=True)
            previous = make_snapshot(candles[-2], ema_short=100, ema_long=100, macd=1.2, signal=1.0, rsi=54, middle=100, volume_above=True)
        else:
            current = make_snapshot(candles[-1], ema_short=101, ema_long=100, macd=2, signal=1, rsi=60)
            previous = make_snapshot(candles[-2], ema_short=99, ema_long=100, macd=0.5, signal=1, rsi=55)
        values = [current for _ in candles]
        values[-2] = previous
        values[-1] = current
        return tuple(values)

    monkeypatch.setattr(common, "calculate_indicator_series", fake_series)


def make_regime(batch: CandleBatch, label: RegimeLabel = RegimeLabel.UPWARD_TREND, quality: DataQuality = DataQuality.USABLE):
    return RegimeAssessment(
        symbol=batch.symbol,
        interval=batch.interval,
        regime=label,
        volatility=VolatilityLabel.NORMAL,
        volume_activity=VolumeActivity.ABOVE_AVERAGE,
        data_quality=quality,
        assessment_time_ms=batch.as_of_time_ms,
        latest_candle_close_time_ms=batch.candles[-1].close_time_ms,
        closed_candle_count=len(batch.candles),
        trend_separation_pct=0.002,
        volatility_ratio=1.0,
        liquidity_assessment="not_assessed_for_strategy_unit_test",
        explanations=("synthetic test regime",),
    )


def costs(batch: CandleBatch, spread: float = 2.0, fees: float = 2.0, slippage: float = 1.0, age_ms: int = 500) -> MarketCostEstimate:
    return MarketCostEstimate(spread, fees, slippage, batch.as_of_time_ms - age_ms, "unit-test-estimate")


def scalp_inputs(monkeypatch, **kwargs):
    entry = make_batch("BTCUSDT", "1m", not_after_ms=BASE_TIME_MS, last_close_override=101.0)
    context = {
        "3m": make_batch("BTCUSDT", "3m", not_after_ms=entry.candles[-1].close_time_ms),
        "5m": make_batch("BTCUSDT", "5m", not_after_ms=entry.candles[-1].close_time_ms),
    }
    setup_synthetic_indicators(monkeypatch, "1m", ("3m", "5m"), **kwargs)
    return entry, context, make_regime(entry)


def day_inputs(monkeypatch, **kwargs):
    entry = make_batch("BTCUSDT", "5m", not_after_ms=BASE_TIME_MS, last_close_override=101.0)
    context = {
        "15m": make_batch("BTCUSDT", "15m", not_after_ms=entry.candles[-1].close_time_ms),
        "1h": make_batch("BTCUSDT", "1h", not_after_ms=entry.candles[-1].close_time_ms),
    }
    setup_synthetic_indicators(monkeypatch, "5m", ("15m", "1h"), **kwargs)
    return entry, context, make_regime(entry)


def swing_inputs(monkeypatch, **kwargs):
    entry = make_batch("BTCUSDT", "1h", not_after_ms=BASE_TIME_MS, last_close_override=101.0)
    # Make the current bar a real breakout relative to the previous 20 highs.
    candles = list(entry.candles)
    latest = candles[-1]
    prior_high = max(c.high for c in candles[-21:-1])
    candles[-1] = replace(latest, close=prior_high + Decimal("1"), open=prior_high + Decimal("0.8"), high=prior_high + Decimal("1.2"), low=prior_high + Decimal("0.6"))
    entry = replace(entry, candles=tuple(candles))
    context = {
        "4h": make_batch("BTCUSDT", "4h", not_after_ms=entry.candles[-1].close_time_ms),
        "1d": make_batch("BTCUSDT", "1d", not_after_ms=entry.candles[-1].close_time_ms),
    }
    setup_synthetic_indicators(monkeypatch, "1h", ("4h", "1d"), **kwargs)
    return entry, context, make_regime(entry)


def test_all_strategy_configs_are_disabled_by_default():
    assert ScalpStrategyConfig().enabled is False
    assert DayStrategyConfig().enabled is False
    assert SwingStrategyConfig().enabled is False


def test_strategy_configs_have_distinct_stop_and_reward_baselines():
    assert ScalpStrategyConfig().stop_lookback == 5
    assert DayStrategyConfig().stop_lookback == 10
    assert SwingStrategyConfig().stop_lookback == 20
    assert DayStrategyConfig().allow_overnight is None
    assert ScalpStrategyConfig().reward_risk_multiple < DayStrategyConfig().reward_risk_multiple < SwingStrategyConfig().reward_risk_multiple


def test_invalid_rsi_configuration_is_rejected():
    with pytest.raises(ValueError, match="RSI entry bounds"):
        ScalpStrategyConfig(rsi_entry_min=80, rsi_entry_max=70)


def test_context_cannot_duplicate_entry_interval():
    with pytest.raises(ValueError, match="must not also be a context"):
        ScalpStrategyConfig(entry_interval="1m", context_intervals=("1m", "3m"))


def test_disabled_strategy_returns_no_action(monkeypatch):
    entry, context, regime = scalp_inputs(monkeypatch)
    signal = evaluate_scalp_strategy(entry, context, regime)
    assert signal.action is StrategyAction.NO_ACTION
    assert "disabled by default" in signal.reasons[0]


def test_scalp_emits_buy_candidate_only_for_fresh_bullish_crossover(monkeypatch):
    entry, context, regime = scalp_inputs(monkeypatch)
    config = ScalpStrategyConfig(enabled=True, entry_interval="1m", context_intervals=("3m", "5m"))
    signal = evaluate_scalp_strategy(entry, context, regime, cost_estimate=costs(entry), config=config)
    assert signal.action is StrategyAction.BUY_CANDIDATE
    assert signal.stop_price is not None and signal.stop_price < signal.reference_price
    assert signal.target_price is not None and signal.target_price > signal.reference_price
    assert signal.estimated_round_trip_cost_bps == 5.0
    assert signal.estimated_net_reward_bps > 0
    assert signal.timeframes_used == ("1m", "3m", "5m")
    assert signal.decision_time_ms >= max(entry.as_of_time_ms, regime.assessment_time_ms)
    assert signal.deduplication_key is not None


def test_scalp_blocks_missing_cost_estimate(monkeypatch):
    entry, context, regime = scalp_inputs(monkeypatch)
    signal = evaluate_scalp_strategy(entry, context, regime, config=ScalpStrategyConfig(enabled=True, entry_interval="1m", context_intervals=("3m", "5m")))
    assert signal.action is StrategyAction.NO_ACTION
    assert "zero costs are never assumed" in signal.reasons[0]


def test_scalp_exit_candidate_requires_open_position(monkeypatch):
    previous = make_snapshot(Candle("BTCUSDT", "1m", 1_700_000_000_000, Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"), Decimal("100"), 1_700_000_059_999, Decimal("1000"), 10, Decimal("50"), Decimal("500")), ema_short=101, ema_long=100, macd=1.2, signal=1.0)
    entry, context, regime = scalp_inputs(monkeypatch, entry_previous=previous, entry_current=None)
    # Replace the mocked current values with a bearish cross and overbought flag.
    setup_synthetic_indicators(monkeypatch, "1m", ("3m", "5m"), entry_previous=previous, entry_current=make_snapshot(entry.candles[-1], ema_short=99, ema_long=100, macd=0.2, signal=1.0, rsi=76, middle=100, volume_above=True))
    config = ScalpStrategyConfig(enabled=True, entry_interval="1m", context_intervals=("3m", "5m"))
    signal = evaluate_scalp_strategy(entry, context, regime, position_open=True, config=config)
    assert signal.action is StrategyAction.EXIT_CANDIDATE
    assert all("open a short" not in reason.lower() for reason in signal.reasons)


def test_day_strategy_blocks_new_entry_until_overnight_policy_is_set(monkeypatch):
    entry, context, regime = day_inputs(monkeypatch)
    config = DayStrategyConfig(enabled=True, entry_interval="5m", context_intervals=("15m", "1h"))
    signal = evaluate_day_strategy(entry, context, regime, cost_estimate=costs(entry), config=config)
    assert signal.action is StrategyAction.NO_ACTION
    assert any("overnight-holding policy is unresolved" in reason for reason in signal.reasons)


def test_day_strategy_uses_bollinger_middle_cross(monkeypatch):
    entry, context, regime = day_inputs(monkeypatch)
    config = DayStrategyConfig(enabled=True, entry_interval="5m", context_intervals=("15m", "1h"), allow_overnight=False)
    signal = evaluate_day_strategy(entry, context, regime, cost_estimate=costs(entry), config=config)
    assert signal.action is StrategyAction.BUY_CANDIDATE
    assert any("crossed above the Bollinger middle" in reason for reason in signal.reasons)


def test_day_strategy_does_not_repeat_without_fresh_middle_cross(monkeypatch):
    entry, context, regime = day_inputs(monkeypatch)
    previous = make_snapshot(entry.candles[-2], ema_short=100, ema_long=99, macd=2, signal=1, rsi=60, middle=95, volume_above=True)
    current = make_snapshot(entry.candles[-1], ema_short=101, ema_long=100, macd=2, signal=1, rsi=60, middle=95, volume_above=True)
    setup_synthetic_indicators(monkeypatch, "5m", ("15m", "1h"), entry_previous=previous, entry_current=current)
    signal = evaluate_day_strategy(entry, context, regime, cost_estimate=costs(entry), config=DayStrategyConfig(enabled=True, entry_interval="5m", context_intervals=("15m", "1h"), allow_overnight=False))
    assert signal.action is StrategyAction.NO_ACTION
    assert any("crossed above the Bollinger middle" in reason for reason in signal.reasons)


def test_day_exit_triggers_on_close_cross_below_middle(monkeypatch):
    entry, context, regime = day_inputs(monkeypatch)
    previous = make_snapshot(entry.candles[-2], ema_short=101, ema_long=100, macd=2, signal=1, rsi=60, middle=99, volume_above=True)
    current = make_snapshot(entry.candles[-1], ema_short=100, ema_long=101, macd=0.2, signal=1, rsi=43, middle=102, volume_above=True)
    setup_synthetic_indicators(monkeypatch, "5m", ("15m", "1h"), entry_previous=previous, entry_current=current)
    config = DayStrategyConfig(enabled=True, entry_interval="5m", context_intervals=("15m", "1h"))
    signal = evaluate_day_strategy(entry, context, regime, position_open=True, config=config)
    assert signal.action is StrategyAction.EXIT_CANDIDATE


def test_swing_strategy_uses_prior_high_breakout(monkeypatch):
    entry, context, regime = swing_inputs(monkeypatch)
    config = SwingStrategyConfig(enabled=True, entry_interval="1h", context_intervals=("4h", "1d"))
    signal = evaluate_swing_strategy(entry, context, regime, cost_estimate=costs(entry), config=config)
    assert signal.action is StrategyAction.BUY_CANDIDATE
    assert any("broke above the prior 20-candle high" in reason for reason in signal.reasons)
    assert "1d" in signal.timeframes_used


def test_swing_exit_triggers_on_structural_breakdown(monkeypatch):
    entry, context, regime = swing_inputs(monkeypatch)
    candles = list(entry.candles)
    prior_low = min(c.low for c in candles[-21:-1])
    last = candles[-1]
    candles[-1] = replace(
        last,
        open=prior_low - Decimal("0.8"),
        close=prior_low - Decimal("1.0"),
        high=prior_low - Decimal("0.5"),
        low=prior_low - Decimal("1.2"),
    )
    entry = replace(entry, candles=tuple(candles))
    setup_synthetic_indicators(monkeypatch, "1h", ("4h", "1d"))
    config = SwingStrategyConfig(enabled=True, entry_interval="1h", context_intervals=("4h", "1d"))
    signal = evaluate_swing_strategy(entry, context, make_regime(entry), position_open=True, config=config)
    assert signal.action is StrategyAction.EXIT_CANDIDATE
    assert any("structural low" in reason for reason in signal.reasons)


def test_swing_requires_macd_confirmation_in_all_contexts(monkeypatch):
    entry, context, regime = swing_inputs(monkeypatch, context_macd_bullish=False)
    config = SwingStrategyConfig(enabled=True, entry_interval="1h", context_intervals=("4h", "1d"))
    signal = evaluate_swing_strategy(entry, context, regime, cost_estimate=costs(entry), config=config)
    assert signal.action is StrategyAction.NO_ACTION
    assert any("context EMA and MACD" in reason for reason in signal.reasons)


def test_stale_entry_batch_blocks_new_signal(monkeypatch):
    entry, context, regime = scalp_inputs(monkeypatch)
    entry = replace(entry, is_stale=True)
    config = ScalpStrategyConfig(enabled=True, entry_interval="1m", context_intervals=("3m", "5m"))
    signal = evaluate_scalp_strategy(entry, context, regime, cost_estimate=costs(entry), config=config)
    assert signal.action is StrategyAction.NO_ACTION
    assert any("stale" in reason.lower() for reason in signal.reasons)


def test_gapped_entry_batch_blocks_new_signal(monkeypatch):
    entry, context, regime = scalp_inputs(monkeypatch)
    gap = CandleGap(1, 2, 3, 1)
    entry = replace(entry, gaps=(gap,))
    config = ScalpStrategyConfig(enabled=True, entry_interval="1m", context_intervals=("3m", "5m"))
    signal = evaluate_scalp_strategy(entry, context, regime, cost_estimate=costs(entry), config=config)
    assert signal.action is StrategyAction.NO_ACTION
    assert any("gapped" in reason.lower() for reason in signal.reasons)


def test_wrong_symbol_context_fails_closed(monkeypatch):
    entry, context, regime = scalp_inputs(monkeypatch)
    context["3m"] = make_batch("ETHUSDT", "3m", not_after_ms=entry.candles[-1].close_time_ms)
    config = ScalpStrategyConfig(enabled=True, entry_interval="1m", context_intervals=("3m", "5m"))
    signal = evaluate_scalp_strategy(entry, context, regime, cost_estimate=costs(entry), config=config)
    assert signal.action is StrategyAction.NO_ACTION
    assert any("symbol/interval" in reason for reason in signal.reasons)


def test_future_context_data_is_rejected(monkeypatch):
    entry, context, regime = scalp_inputs(monkeypatch)
    context["3m"] = make_batch("BTCUSDT", "3m", not_after_ms=entry.candles[-1].close_time_ms + 180_000)
    config = ScalpStrategyConfig(enabled=True, entry_interval="1m", context_intervals=("3m", "5m"))
    signal = evaluate_scalp_strategy(entry, context, regime, cost_estimate=costs(entry), config=config)
    assert signal.action is StrategyAction.NO_ACTION
    assert any("future data" in reason for reason in signal.reasons)


def test_misaligned_regime_is_rejected(monkeypatch):
    entry, context, regime = scalp_inputs(monkeypatch)
    regime = replace(regime, latest_candle_close_time_ms=regime.latest_candle_close_time_ms - INTERVAL_MILLISECONDS["1m"])
    config = ScalpStrategyConfig(enabled=True, entry_interval="1m", context_intervals=("3m", "5m"))
    signal = evaluate_scalp_strategy(entry, context, regime, cost_estimate=costs(entry), config=config)
    assert signal.action is StrategyAction.NO_ACTION
    assert any("not aligned" in reason for reason in signal.reasons)


def test_missing_context_blocks_new_signal(monkeypatch):
    entry, context, regime = scalp_inputs(monkeypatch)
    context.pop("3m")
    config = ScalpStrategyConfig(enabled=True, entry_interval="1m", context_intervals=("3m", "5m"))
    signal = evaluate_scalp_strategy(entry, context, regime, cost_estimate=costs(entry), config=config)
    assert signal.action is StrategyAction.NO_ACTION


def test_stale_cost_estimate_blocks_entry(monkeypatch):
    entry, context, regime = scalp_inputs(monkeypatch)
    config = ScalpStrategyConfig(enabled=True, entry_interval="1m", context_intervals=("3m", "5m"))
    old_cost = costs(entry, age_ms=120_000)
    signal = evaluate_scalp_strategy(entry, context, regime, cost_estimate=old_cost, config=config)
    assert signal.action is StrategyAction.NO_ACTION
    assert any("stale" in reason.lower() for reason in signal.reasons)


def test_future_cost_estimate_blocks_entry(monkeypatch):
    entry, context, regime = scalp_inputs(monkeypatch)
    config = ScalpStrategyConfig(enabled=True, entry_interval="1m", context_intervals=("3m", "5m"))
    future_cost = MarketCostEstimate(2, 2, 1, entry.as_of_time_ms + 1, "unit-test")
    signal = evaluate_scalp_strategy(entry, context, regime, cost_estimate=future_cost, config=config)
    assert signal.action is StrategyAction.NO_ACTION
    assert any("future-dated" in reason for reason in signal.reasons)


def test_high_costs_reject_entry_with_explanation(monkeypatch):
    entry, context, regime = scalp_inputs(monkeypatch)
    config = ScalpStrategyConfig(enabled=True, entry_interval="1m", context_intervals=("3m", "5m"))
    signal = evaluate_scalp_strategy(entry, context, regime, cost_estimate=costs(entry, spread=200, fees=150, slippage=100), config=config)
    assert signal.action is StrategyAction.NO_ACTION
    assert any("cost gate" in reason for reason in signal.reasons)


def test_non_boolean_position_state_fails_closed(monkeypatch):
    entry, context, regime = scalp_inputs(monkeypatch)
    signal = evaluate_scalp_strategy(entry, context, regime, position_open="no", config=ScalpStrategyConfig(enabled=True, entry_interval="1m", context_intervals=("3m", "5m")))
    assert signal.action is StrategyAction.NO_ACTION
    assert "must be a boolean" in signal.reasons[0]


def test_cost_estimate_rejects_negative_components():
    with pytest.raises(ValueError, match="non-negative"):
        MarketCostEstimate(-1, 2, 1, BASE_TIME_MS)


def test_signal_actions_never_include_short_or_sell_to_open():
    assert {action.value for action in StrategyAction} == {"buy_candidate", "exit_candidate", "no_action"}


def test_same_closed_candle_uses_same_deduplication_key(monkeypatch):
    entry, context, regime = scalp_inputs(monkeypatch)
    config = ScalpStrategyConfig(enabled=True, entry_interval="1m", context_intervals=("3m", "5m"))
    first = evaluate_scalp_strategy(entry, context, regime, cost_estimate=costs(entry), config=config)
    second = evaluate_scalp_strategy(entry, context, regime, cost_estimate=costs(entry), config=config)
    assert first.action is StrategyAction.BUY_CANDIDATE
    assert first.deduplication_key == second.deduplication_key