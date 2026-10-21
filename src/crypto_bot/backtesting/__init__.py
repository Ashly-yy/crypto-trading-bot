"""Historical backtesting components; simulation only, no exchange order access."""
from .engine import (
    BacktestEngineError,
    EntryIntent,
    ExitIntent,
    ReplayResult,
    SimulatedTrade,
    replay_single_position,
)
from .fills import (
    FillModelError,
    IntrabarExit,
    OrderSide,
    SimulatedFill,
    resolve_intrabar_exit,
    simulate_market_fill,
)
from .metrics import (
    PerformanceMetricsError,
    PerformanceReport,
    calculate_performance,
)
from .risk_gate import (
    BacktestRiskGateError,
    EntryAuthorization,
    authorize_strategy_entry,
)

__all__ = [
    "BacktestEngineError",
    "EntryIntent",
    "ExitIntent",
    "ReplayResult",
    "SimulatedTrade",
    "replay_single_position",
    "FillModelError",
    "IntrabarExit",
    "OrderSide",
    "SimulatedFill",
    "resolve_intrabar_exit",
    "simulate_market_fill",
    "BacktestRiskGateError",
    "EntryAuthorization",
    "authorize_strategy_entry",
    "PerformanceMetricsError",
    "PerformanceReport",
    "calculate_performance",
]

# Step 18 historical orchestration exports
from .orchestration import (
    BacktestOrchestrationError,
    DecisionPoint,
    OrchestrationReport,
    run_historical_orchestration,
)

# Step 19 strategy-driven historical replay exports
from .strategy_driven import (
    StrategyDrivenBacktestError,
    build_strategy_decision_points,
    run_strategy_historical_backtest,
)
