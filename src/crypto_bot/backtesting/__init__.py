"""Historical backtesting components; simulation only, no exchange order access."""
from .engine import BacktestEngineError, EntryIntent, ReplayResult, SimulatedTrade, replay_single_position
from .fills import (
    FillModelError, IntrabarExit, OrderSide, SimulatedFill,
    resolve_intrabar_exit, simulate_market_fill,
)
__all__ = [
    "BacktestEngineError", "EntryIntent", "ReplayResult", "SimulatedTrade",
    "replay_single_position", "FillModelError", "IntrabarExit", "OrderSide",
    "SimulatedFill", "resolve_intrabar_exit", "simulate_market_fill",
    "BacktestEntryEvaluation", "evaluate_authorized_entry",
]

from .risk_gate import (
    BacktestRiskGateError,
    EntryAuthorization,
    authorize_strategy_entry,
)

from .pipeline import BacktestEntryEvaluation, evaluate_authorized_entry
