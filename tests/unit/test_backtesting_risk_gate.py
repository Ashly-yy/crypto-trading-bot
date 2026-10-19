from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from crypto_bot.backtesting.risk_gate import authorize_strategy_entry


class Action(Enum):
    BUY_CANDIDATE = "BUY_CANDIDATE"
    NO_ACTION = "NO_ACTION"


@dataclass
class Signal:
    action: Action = Action.BUY_CANDIDATE
    symbol: str = "BTCUSDT"
    reference_price: Decimal = Decimal("100")
    stop_price: Decimal = Decimal("95")


@dataclass
class Costs:
    round_trip_fee_bps: Decimal = Decimal("10")
    round_trip_slippage_bps: Decimal = Decimal("5")
    spread_bps: Decimal = Decimal("2")


@dataclass
class Limits:
    max_order_quote: Decimal = Decimal("100")


@dataclass
class Decision:
    approved: bool
    reasons: tuple[str, ...] = ()


class FakeRiskManager:
    def __init__(self, decision=None):
        self.limits = Limits()
        self.decision = decision or Decision(True)
        self.received = None

    def evaluate(self, proposal, portfolio, context):
        self.received = (proposal, portfolio, context)
        return self.decision


def test_buy_signal_reaches_central_risk_manager_with_costs():
    portfolio, context = object(), object()
    manager = FakeRiskManager()

    result = authorize_strategy_entry(
        Signal(),
        portfolio=portfolio,
        risk_manager=manager,
        risk_context=context,
        cost_estimate=Costs(),
    )

    assert result.approved is True
    proposal, passed_portfolio, passed_context = manager.received
    assert proposal.asset == "BTCUSDT"
    assert proposal.quantity == Decimal("1")
    assert proposal.estimated_fees_quote == Decimal("0.1")
    assert proposal.estimated_slippage_quote == Decimal("0.07")
    assert passed_portfolio is portfolio
    assert passed_context is context


def test_central_risk_rejection_is_preserved():
    manager = FakeRiskManager(
        Decision(False, ("daily loss limit reached",))
    )

    result = authorize_strategy_entry(
        Signal(),
        portfolio=object(),
        risk_manager=manager,
        risk_context=object(),
        cost_estimate=Costs(),
    )

    assert result.approved is False
    assert result.reasons == ("daily loss limit reached",)


def test_non_buy_signal_never_reaches_risk_manager():
    manager = FakeRiskManager()

    result = authorize_strategy_entry(
        Signal(action=Action.NO_ACTION),
        portfolio=object(),
        risk_manager=manager,
        risk_context=object(),
        cost_estimate=Costs(),
    )

    assert result.approved is False
    assert manager.received is None


def test_invalid_stop_fails_closed():
    manager = FakeRiskManager()

    result = authorize_strategy_entry(
        Signal(stop_price=Decimal("100")),
        portfolio=object(),
        risk_manager=manager,
        risk_context=object(),
        cost_estimate=Costs(),
    )

    assert result.approved is False
    assert "Stop price must be below" in result.reasons[0]
    assert manager.received is None


def test_missing_cost_estimate_fails_closed():
    manager = FakeRiskManager()

    result = authorize_strategy_entry(
        Signal(),
        portfolio=object(),
        risk_manager=manager,
        risk_context=object(),
        cost_estimate=None,
    )

    assert result.approved is False
    assert result.proposal is None
    assert manager.received is None


def test_negative_cost_estimate_fails_closed():
    manager = FakeRiskManager()
    costs = Costs(round_trip_fee_bps=Decimal("-1"))

    result = authorize_strategy_entry(
        Signal(),
        portfolio=object(),
        risk_manager=manager,
        risk_context=object(),
        cost_estimate=costs,
    )

    assert result.approved is False
    assert manager.received is None
