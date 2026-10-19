from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from crypto_bot.backtesting.risk_gate import authorize_strategy_entry
from crypto_bot.strategies.common import MarketCostEstimate, StrategyAction


@pytest.mark.parametrize(
    "action",
    [
        StrategyAction.BUY_CANDIDATE,
        "BUY_CANDIDATE",
        "buy_candidate",
    ],
)
def test_buy_candidate_enum_and_strings_reach_central_risk_manager(action):
    signal = SimpleNamespace(
        action=action,
        symbol="BTCUSDT",
        reference_price=Decimal("100"),
        stop_price=Decimal("95"),
    )
    portfolio = object()
    risk_context = object()
    decision = SimpleNamespace(approved=True, reasons=("Approved",))
    risk_manager = SimpleNamespace(
        limits=SimpleNamespace(max_order_quote=Decimal("100")),
        evaluate=Mock(return_value=decision),
    )
    costs = MarketCostEstimate(
        spread_bps=1.0,
        round_trip_fee_bps=2.0,
        round_trip_slippage_bps=1.0,
        observed_at_ms=60_000,
        source="unit_test",
    )

    result = authorize_strategy_entry(
        signal,
        portfolio=portfolio,
        risk_manager=risk_manager,
        risk_context=risk_context,
        cost_estimate=costs,
    )

    assert result.approved is True
    assert result.decision is decision
    assert result.proposal is not None
    risk_manager.evaluate.assert_called_once_with(
        result.proposal, portfolio, risk_context
    )
