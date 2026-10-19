from unittest.mock import Mock, patch

import pytest

from crypto_bot.backtesting import orchestrator
from crypto_bot.backtesting.orchestrator import (
    StrategyOrchestrationError,
    evaluate_strategy,
)
from crypto_bot.market_data.candles import CandleBatch
from crypto_bot.market_intelligence.regime import RegimeAssessment
from crypto_bot.strategies.common import MarketCostEstimate
from crypto_bot.strategies.scalp import ScalpStrategyConfig
from crypto_bot.strategies.day import DayStrategyConfig
from crypto_bot.strategies.swing import SwingStrategyConfig


def valid_inputs():
    entry = CandleBatch(
        symbol="BTCUSDT", interval="1m", requested_limit=1, received_count=1,
        candles=(), incomplete_candles_dropped=0, gaps=(), is_stale=False,
        latest_candle_age_ms=0, as_of_time_ms=60_000,
    )
    regime = RegimeAssessment(
        symbol="BTCUSDT", interval="1m", regime=None, volatility=None,
        volume_activity=None, data_quality=None, assessment_time_ms=60_000,
        latest_candle_close_time_ms=59_999, closed_candle_count=1,
        trend_separation_pct=None, volatility_ratio=None,
        liquidity_assessment="test", explanations=(),
    )
    cost = MarketCostEstimate(
        spread_bps=1.0, round_trip_fee_bps=2.0,
        round_trip_slippage_bps=1.0, observed_at_ms=60_000,
        source="unit_test",
    )
    return entry, {}, regime, cost


@pytest.mark.parametrize(
    ("style", "config"),
    [
        ("scalp", ScalpStrategyConfig()),
        ("day", DayStrategyConfig()),
        ("swing", SwingStrategyConfig()),
    ],
)
def test_dispatches_to_matching_strategy(style, config):
    entry, contexts, regime, cost = valid_inputs()
    expected = object()
    evaluator = Mock(return_value=expected)

    with patch.dict(
        orchestrator._STRATEGIES,
        {style: (type(config), evaluator)},
    ):
        result = evaluate_strategy(
            style, entry, contexts, regime,
            config=config, cost_estimate=cost,
        )

    assert result is expected
    evaluator.assert_called_once_with(
        entry, contexts, regime,
        cost_estimate=cost, position_open=False, config=config,
    )


@pytest.mark.parametrize("style", ["unknown", "", None])
def test_unknown_style_is_rejected(style):
    with pytest.raises(StrategyOrchestrationError, match="style"):
        evaluate_strategy(
            style, None, {}, None,
            config=ScalpStrategyConfig(), cost_estimate=None,
        )


def test_wrong_config_for_style_is_rejected():
    with pytest.raises(StrategyOrchestrationError, match="requires"):
        evaluate_strategy(
            "day", None, {}, None,
            config=ScalpStrategyConfig(), cost_estimate=None,
        )


def test_invalid_position_flag_is_rejected_before_evaluation():
    entry, contexts, regime, cost = valid_inputs()
    with pytest.raises(StrategyOrchestrationError, match="position_open"):
        evaluate_strategy(
            "scalp", entry, contexts, regime,
            config=ScalpStrategyConfig(), cost_estimate=cost,
            position_open=1,
        )
