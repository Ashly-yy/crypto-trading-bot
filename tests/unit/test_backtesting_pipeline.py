from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from crypto_bot.backtesting.engine import EntryIntent
from crypto_bot.backtesting.pipeline import evaluate_authorized_entry
from crypto_bot.backtesting.risk_gate import EntryAuthorization
from crypto_bot.control.kill_switch import KillSwitch, KillSwitchState
from crypto_bot.risk.manager import RiskContext, RiskManager
from crypto_bot.risk.models import RiskLimits
from crypto_bot.strategies.common import MarketCostEstimate, StrategyAction, StrategySignal


def make_signal(
    action=StrategyAction.BUY_CANDIDATE,
    *,
    symbol="BTCUSDT",
    decision_time_ms=60_000,
    reference_price=100.0,
    stop_price=95.0,
    target_price=110.0,
):
    return StrategySignal(
        style="scalp",
        symbol=symbol,
        entry_interval="1m",
        action=action,
        decision_time_ms=decision_time_ms,
        latest_candle_close_time_ms=59_999,
        reference_price=reference_price,
        stop_price=stop_price,
        target_price=target_price,
        reward_risk_ratio=2.0,
        gross_target_bps=1000.0,
        estimated_round_trip_cost_bps=6.0,
        estimated_net_reward_bps=994.0,
        strategy_version="test",
        timeframes_used=("1m", "3m", "5m"),
        deduplication_key="test-key",
        reasons=("test signal",),
    )


def make_costs(*, spread=1.0, fee=2.0, slippage=3.0, observed_at_ms=60_000):
    return MarketCostEstimate(
        spread_bps=spread,
        round_trip_fee_bps=fee,
        round_trip_slippage_bps=slippage,
        observed_at_ms=observed_at_ms,
        source="unit_test",
    )


def make_manager(*, approved=True, quantity=Decimal("1")):
    decision = SimpleNamespace(
        approved=approved,
        reasons=("test approved" if approved else "test rejected",),
        permitted_quantity=quantity if approved else Decimal("0"),
    )
    manager = SimpleNamespace(
        limits=SimpleNamespace(max_order_quote=Decimal("100")),
        evaluate=Mock(return_value=decision),
    )
    return manager, decision


def run_pipeline(signal, manager, costs=None):
    with patch(
        "crypto_bot.backtesting.pipeline.evaluate_strategy",
        return_value=signal,
    ) as evaluator:
        result = evaluate_authorized_entry(
            "scalp",
            object(),
            {},
            object(),
            config=object(),
            cost_estimate=costs,
            portfolio=object(),
            risk_manager=manager,
            risk_context=object(),
        )
    evaluator.assert_called_once()
    return result


def test_buy_signal_runs_central_risk_gate_and_returns_replay_intent():
    manager, decision = make_manager()
    result = run_pipeline(make_signal(), manager, make_costs())

    assert result.signal is not None
    assert result.authorization.approved is True
    assert result.authorization.decision is decision
    assert isinstance(result.intent, EntryIntent)
    assert result.intent.symbol == "BTCUSDT"
    assert result.intent.decision_time_ms == 60_000
    assert result.intent.quantity == Decimal("1")
    assert result.intent.stop_price == Decimal("95.0")
    assert result.intent.target_price == Decimal("110.0")
    assert result.intent.fee_rate == Decimal("0.0001")
    assert result.intent.slippage_bps == Decimal("2.0")
    manager.evaluate.assert_called_once()


def test_central_risk_rejection_never_returns_intent():
    manager, _ = make_manager(approved=False)
    result = run_pipeline(make_signal(), manager, make_costs())

    assert result.authorization.approved is False
    assert result.intent is None
    manager.evaluate.assert_called_once()


@pytest.mark.parametrize(
    "signal",
    [
        make_signal(action=StrategyAction.NO_ACTION),
        make_signal(action=StrategyAction.EXIT_CANDIDATE),
    ],
)
def test_non_entry_signal_never_returns_intent_or_calls_risk_manager(signal):
    manager, _ = make_manager()
    result = run_pipeline(signal, manager, make_costs())

    assert result.intent is None
    assert result.authorization.approved is False
    manager.evaluate.assert_not_called()


@pytest.mark.parametrize(
    ("signal", "message"),
    [
        (make_signal(target_price=95.0), "stop < entry < target"),
        (make_signal(target_price=100.0), "stop < entry < target"),
        (make_signal(decision_time_ms=None), "timestamp"),
        (make_signal(symbol=""), "symbol"),
    ],
)
def test_invalid_signal_fields_fail_closed_before_risk_gate(signal, message):
    manager, _ = make_manager()
    result = run_pipeline(signal, manager, make_costs())

    assert result.intent is None
    assert result.authorization.approved is False
    assert message.lower() in result.authorization.reasons[0].lower()
    manager.evaluate.assert_not_called()


def test_missing_cost_estimate_fails_closed_before_risk_gate():
    manager, _ = make_manager()
    result = run_pipeline(make_signal(), manager, None)

    assert result.intent is None
    assert result.authorization.approved is False
    manager.evaluate.assert_not_called()


def test_future_dated_cost_estimate_fails_closed_before_risk_gate():
    manager, _ = make_manager()
    result = run_pipeline(
        make_signal(decision_time_ms=60_000),
        manager,
        make_costs(observed_at_ms=60_001),
    )

    assert result.intent is None
    assert result.authorization.approved is False
    manager.evaluate.assert_not_called()


def test_invalid_risk_approved_quantity_fails_closed_without_intent():
    manager, _ = make_manager(quantity=Decimal("0"))
    result = run_pipeline(make_signal(), manager, make_costs())

    assert result.intent is None
    assert result.authorization.approved is False
    assert "risk-approved quantity" in result.authorization.reasons[0]


def test_pipeline_with_real_risk_manager_and_running_kill_switch(tmp_path):
    kill_switch = KillSwitch(tmp_path / "kill-switch.sqlite3")
    kill_switch.transition(
        KillSwitchState.RUNNING,
        actor="unit-test",
        reason="explicit test setup",
        authorized=True,
    )
    limits = RiskLimits(
        max_order_quote=Decimal("100"),
        max_asset_exposure_quote=Decimal("500"),
        max_total_exposure_quote=Decimal("1000"),
        max_open_positions=3,
        max_trade_loss_quote=Decimal("10"),
        max_daily_loss_quote=Decimal("20"),
    )
    manager = RiskManager(limits, kill_switch=kill_switch)
    portfolio = SimpleNamespace(
        cash=Decimal("1000"),
        equity=Decimal("1000"),
        exposure_by_asset={},
        total_exposure=Decimal("0"),
    )
    costs = make_costs()

    with patch(
        "crypto_bot.backtesting.pipeline.evaluate_strategy",
        return_value=make_signal(),
    ):
        result = evaluate_authorized_entry(
            "scalp", object(), {}, object(),
            config=object(), cost_estimate=costs,
            portfolio=portfolio,
            risk_manager=manager,
            risk_context=RiskContext(
                current_daily_loss_quote=Decimal("0"),
                open_position_count=0,
            ),
        )

    assert result.authorization.approved is True
    assert result.intent is not None
    assert result.intent.quantity == Decimal("1")


def test_pipeline_real_risk_manager_rejects_when_kill_switch_stopped(tmp_path):
    kill_switch = KillSwitch(tmp_path / "kill-switch.sqlite3")
    limits = RiskLimits(
        max_order_quote=Decimal("100"),
        max_asset_exposure_quote=Decimal("500"),
        max_total_exposure_quote=Decimal("1000"),
        max_open_positions=3,
        max_trade_loss_quote=Decimal("10"),
        max_daily_loss_quote=Decimal("20"),
    )
    manager = RiskManager(limits, kill_switch=kill_switch)
    portfolio = SimpleNamespace(
        cash=Decimal("1000"),
        equity=Decimal("1000"),
        exposure_by_asset={},
        total_exposure=Decimal("0"),
    )

    with patch(
        "crypto_bot.backtesting.pipeline.evaluate_strategy",
        return_value=make_signal(),
    ):
        result = evaluate_authorized_entry(
            "scalp", object(), {}, object(),
            config=object(), cost_estimate=make_costs(),
            portfolio=portfolio,
            risk_manager=manager,
            risk_context=RiskContext(
                current_daily_loss_quote=Decimal("0"),
                open_position_count=0,
            ),
        )

    assert result.authorization.approved is False
    assert result.intent is None
    assert any("kill switch blocks new entries" in reason for reason in result.authorization.reasons)
