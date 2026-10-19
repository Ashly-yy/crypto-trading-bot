"""Independent, configurable spot-strategy research modules.

All strategies return advisory candidates only. Order execution and risk approval
remain separate, shared downstream responsibilities.
"""
from crypto_bot.strategies.common import MarketCostEstimate, StrategyAction, StrategyConfig, StrategySignal
from crypto_bot.strategies.day.strategy import DayStrategyConfig, evaluate_day_strategy
from crypto_bot.strategies.scalp.strategy import ScalpStrategyConfig, evaluate_scalp_strategy
from crypto_bot.strategies.swing.strategy import SwingStrategyConfig, evaluate_swing_strategy

__all__ = [
    "MarketCostEstimate",
    "StrategyAction",
    "StrategyConfig",
    "StrategySignal",
    "ScalpStrategyConfig",
    "DayStrategyConfig",
    "SwingStrategyConfig",
    "evaluate_scalp_strategy",
    "evaluate_day_strategy",
    "evaluate_swing_strategy",
]