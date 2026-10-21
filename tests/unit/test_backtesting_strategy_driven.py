from decimal import Decimal

import pytest

from crypto_bot.backtesting.strategy_driven import (
    StrategyDrivenBacktestError,
    build_strategy_decision_points,
)
from crypto_bot.market_data.candles import Candle, INTERVAL_MILLISECONDS
from crypto_bot.risk.manager import RiskContext
from crypto_bot.strategies import ScalpStrategyConfig, MarketCostEstimate


def candles(symbol, interval, count=80, start=1_780_000_000_000):
    duration = INTERVAL_MILLISECONDS[interval]
    assert duration is not None
    start = (start // duration) * duration
    output = []
    for i in range(count):
        op = start + i * duration
        close = Decimal("100") + Decimal(i) / Decimal("10")
        output.append(Candle(
            symbol, interval, op, close, close + Decimal("1"), close - Decimal("1"), close,
            Decimal("10"), op + duration - 1, Decimal("1000"), 10, Decimal("5"), Decimal("500")
        ))
    return tuple(output)


def test_builder_uses_only_closed_entry_candles_before_boundary():
    entry = candles("BTCUSDT", "1m", 80)
    context3 = candles("BTCUSDT", "3m", 40)
    context5 = candles("BTCUSDT", "5m", 30)
    class Portfolio: pass
    class RiskManager: pass
    risk = RiskContext(Decimal("0"), 0, data_is_fresh=True, account_state_is_valid=True, exchange_filters_are_valid=True)
    costs = lambda symbol, timestamp: MarketCostEstimate(2, 2, 1, timestamp - 1, "test")
    replay, points = build_strategy_decision_points(
        style="scalp", config=ScalpStrategyConfig(enabled=True, entry_interval="1m", context_intervals=("3m", "5m")),
        historical_candles={"1m": entry, "3m": context3, "5m": context5},
        symbol="BTCUSDT", portfolio=Portfolio(), risk_manager=RiskManager(),
        risk_context=risk, cost_provider=costs,
    )
    assert replay == entry
    assert points
    for point in points:
        assert point.decision_time_ms == next(c.open_time_ms for c in entry if c.open_time_ms == point.decision_time_ms)
        assert all(c.close_time_ms < point.decision_time_ms for c in point.entry_batch.candles)
        latest_entry_close = point.entry_batch.candles[-1].close_time_ms
        assert all(c.close_time_ms <= latest_entry_close for batch in point.context_batches.values() for c in batch.candles)
        assert point.cost_estimate.observed_at_ms <= point.decision_time_ms


def test_builder_requires_explicitly_enabled_strategy():
    with pytest.raises(StrategyDrivenBacktestError, match="explicitly enabled"):
        build_strategy_decision_points(
            style="scalp", config=ScalpStrategyConfig(), historical_candles={},
            symbol="BTCUSDT", portfolio=object(), risk_manager=object(),
            risk_context=RiskContext(Decimal("0"), 0), cost_provider=lambda *_: None,
        )


def test_builder_fails_closed_for_missing_context_history():
    entry = candles("BTCUSDT", "1m", 80)
    with pytest.raises(StrategyDrivenBacktestError, match="Missing historical candle series"):
        build_strategy_decision_points(
            style="scalp", config=ScalpStrategyConfig(enabled=True, entry_interval="1m", context_intervals=("3m", "5m")),
            historical_candles={"1m": entry, "3m": candles("BTCUSDT", "3m", 40)},
            symbol="BTCUSDT", portfolio=object(), risk_manager=object(),
            risk_context=RiskContext(Decimal("0"), 0), cost_provider=lambda *_: None,
        )


def test_builder_rejects_future_or_out_of_order_candle_data():
    entry = list(candles("BTCUSDT", "1m", 80))
    entry[2], entry[3] = entry[3], entry[2]
    with pytest.raises(StrategyDrivenBacktestError, match="strictly chronological"):
        build_strategy_decision_points(
            style="scalp", config=ScalpStrategyConfig(enabled=True, entry_interval="1m", context_intervals=("3m", "5m")),
            historical_candles={"1m": tuple(entry), "3m": candles("BTCUSDT", "3m", 40), "5m": candles("BTCUSDT", "5m", 30)},
            symbol="BTCUSDT", portfolio=object(), risk_manager=object(),
            risk_context=RiskContext(Decimal("0"), 0), cost_provider=lambda *_: None,
        )
