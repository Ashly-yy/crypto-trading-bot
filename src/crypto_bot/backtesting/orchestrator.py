"""Strategy dispatch for historical research; never submits orders."""
from collections.abc import Mapping
from typing import Any

from crypto_bot.market_data.candles import CandleBatch
from crypto_bot.market_intelligence.regime import RegimeAssessment
from crypto_bot.strategies.common import MarketCostEstimate, StrategySignal
from crypto_bot.strategies.scalp import ScalpStrategyConfig, evaluate_scalp_strategy
from crypto_bot.strategies.day import DayStrategyConfig, evaluate_day_strategy
from crypto_bot.strategies.swing import SwingStrategyConfig, evaluate_swing_strategy


class StrategyOrchestrationError(ValueError):
    """Raised when orchestration inputs are invalid or inconsistent."""


_STRATEGIES = {
    "scalp": (ScalpStrategyConfig, evaluate_scalp_strategy),
    "day": (DayStrategyConfig, evaluate_day_strategy),
    "swing": (SwingStrategyConfig, evaluate_swing_strategy),
}


def evaluate_strategy(
    style: str,
    entry_batch: CandleBatch,
    context_batches: Mapping[str, CandleBatch],
    regime: RegimeAssessment,
    *,
    config: Any,
    cost_estimate: MarketCostEstimate | None,
    position_open: bool = False,
) -> StrategySignal:
    """Dispatch one strategy evaluation over caller-supplied historical batches.

    The caller must construct each batch from data available at that replay point.
    Existing strategy validators remain responsible for candle alignment,
    closed-candle checks, configuration gates, and cost freshness.
    This function only returns a signal; it cannot submit exchange orders.
    """
    if not isinstance(style, str) or style not in _STRATEGIES:
        raise StrategyOrchestrationError("style must be scalp, day, or swing.")

    expected_config, evaluator = _STRATEGIES[style]
    if not isinstance(config, expected_config):
        raise StrategyOrchestrationError(
            f"{style} requires {expected_config.__name__}."
        )
    if not isinstance(entry_batch, CandleBatch):
        raise StrategyOrchestrationError("entry_batch must be a CandleBatch.")
    if not isinstance(context_batches, Mapping):
        raise StrategyOrchestrationError("context_batches must be a mapping.")
    if any(not isinstance(batch, CandleBatch) for batch in context_batches.values()):
        raise StrategyOrchestrationError(
            "Every context batch must be a CandleBatch."
        )
    if not isinstance(regime, RegimeAssessment):
        raise StrategyOrchestrationError("regime must be a RegimeAssessment.")
    if cost_estimate is not None and not isinstance(cost_estimate, MarketCostEstimate):
        raise StrategyOrchestrationError(
            "cost_estimate must be a MarketCostEstimate or None."
        )
    if not isinstance(position_open, bool):
        raise StrategyOrchestrationError("position_open must be a boolean.")

    return evaluator(
        entry_batch,
        context_batches,
        regime,
        cost_estimate=cost_estimate,
        position_open=position_open,
        config=config,
    )
