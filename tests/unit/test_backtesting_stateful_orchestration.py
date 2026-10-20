from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from crypto_bot.backtesting.engine import EntryIntent, ReplayEvent, SimulatedTrade
from crypto_bot.backtesting.fills import IntrabarExit, OrderSide, SimulatedFill
from crypto_bot.backtesting.orchestration import DecisionPoint
from crypto_bot.backtesting import stateful_orchestration as stateful
from crypto_bot.portfolio.accounting import Portfolio
from crypto_bot.risk.manager import RiskContext


def candle(timestamp, price=100):
    return SimpleNamespace(
        symbol="BTCUSDT",
        interval="1m",
        open_time_ms=timestamp,
        close_time_ms=timestamp + 59_999,
        open=Decimal(str(price)),
        high=Decimal(str(price + 2)),
        low=Decimal(str(price - 2)),
        close=Decimal(str(price + 1)),
    )


def make_point(timestamp, portfolio, risk_manager):
    return DecisionPoint(
        decision_time_ms=timestamp,
        style="day",
        entry_batch=object(),
        context_batches={},
        regime=object(),
        config=object(),
        cost_estimate=object(),
        portfolio=portfolio,
        risk_manager=risk_manager,
        risk_context=RiskContext(
            current_daily_loss_quote=Decimal("0"),
            open_position_count=0,
        ),
    )


def test_stateful_orchestration_updates_portfolio_from_simulated_fills(monkeypatch):
    portfolio = Portfolio(Decimal("1000"))
    risk_manager = SimpleNamespace(evaluate=lambda *args: None)
    candles = [candle(1_000)]
    point = make_point(1_000, portfolio, risk_manager)

    intent = EntryIntent(
        symbol="BTCUSDT",
        decision_time_ms=1_000,
        quantity=Decimal("1"),
        stop_price=Decimal("90"),
        target_price=Decimal("120"),
        fee_rate=Decimal("0.01"),
        slippage_bps=Decimal("0"),
    )

    captured = {}

    def fake_evaluate(*args, **kwargs):
        captured["snapshot"] = kwargs["portfolio"]
        captured["risk_context"] = kwargs["risk_context"]
        return SimpleNamespace(
            signal=SimpleNamespace(action="BUY_CANDIDATE"),
            authorization=SimpleNamespace(approved=True),
            intent=intent,
        )

    monkeypatch.setattr(stateful, "evaluate_authorized_entry", fake_evaluate)
    monkeypatch.setattr(
        stateful,
        "validate_historical_candles",
        lambda values, **kwargs: tuple(values),
    )

    entry = SimulatedFill(
        "BTCUSDT", OrderSide.BUY, 1_000, Decimal("100"), Decimal("100"),
        Decimal("1"), Decimal("1"), Decimal("0"),
    )
    exit_fill = SimulatedFill(
        "BTCUSDT", OrderSide.SELL, 2_000, Decimal("110"), Decimal("110"),
        Decimal("1"), Decimal("1"), Decimal("0"),
    )
    trade = SimulatedTrade(
        "BTCUSDT", entry, exit_fill, IntrabarExit.TARGET,
        Decimal("10"), Decimal("8"),
    )

    def fake_replay(**kwargs):
        supplied = kwargs["decision_callback"](
            candles[0], Decimal("1000"), Decimal("0")
        )
        assert tuple(supplied) == (intent,)
        kwargs["event_callback"](
            ReplayEvent("ENTRY", 1_000, Decimal("899"), Decimal("1"), entry)
        )
        kwargs["event_callback"](
            ReplayEvent("EXIT", 2_000, Decimal("1008"), Decimal("0"), exit_fill, trade)
        )
        return SimpleNamespace(trades=(trade,), rejected_intents=())

    report = stateful.run_stateful_historical_orchestration(
        candles=candles,
        decision_points=[point],
        initial_cash=Decimal("1000"),
        replay_kwargs={"symbol": "BTCUSDT", "interval": "1m"},
        replay_callable=fake_replay,
    )

    assert report.approved_entries == 1
    assert report.rejected_entries == 0
    assert report.approved_intents == (intent,)
    assert captured["snapshot"].cash == Decimal("1000")
    assert portfolio.cash == Decimal("1008")
    assert portfolio.snapshot({}).positions == {}


def test_stateful_orchestration_rejects_non_boundary_decision(monkeypatch):
    portfolio = Portfolio(Decimal("1000"))
    risk_manager = SimpleNamespace(evaluate=lambda *args: None)
    monkeypatch.setattr(
        stateful,
        "validate_historical_candles",
        lambda values, **kwargs: tuple(values),
    )

    with pytest.raises(stateful.StatefulOrchestrationError, match="candle open boundary"):
        stateful.run_stateful_historical_orchestration(
            candles=[candle(1_000)],
            decision_points=[make_point(1_001, portfolio, risk_manager)],
            initial_cash=Decimal("1000"),
            replay_kwargs={"symbol": "BTCUSDT", "interval": "1m"},
            replay_callable=lambda **kwargs: None,
        )


def test_stateful_orchestration_requires_one_shared_portfolio(monkeypatch):
    portfolio_a = Portfolio(Decimal("1000"))
    portfolio_b = Portfolio(Decimal("1000"))
    risk_manager = SimpleNamespace(evaluate=lambda *args: None)
    monkeypatch.setattr(
        stateful,
        "validate_historical_candles",
        lambda values, **kwargs: tuple(values),
    )

    points = [
        make_point(1_000, portfolio_a, risk_manager),
        make_point(61_000, portfolio_b, risk_manager),
    ]

    with pytest.raises(stateful.StatefulOrchestrationError, match="same mutable Portfolio"):
        stateful.run_stateful_historical_orchestration(
            candles=[candle(1_000), candle(61_000)],
            decision_points=points,
            initial_cash=Decimal("1000"),
            replay_kwargs={"symbol": "BTCUSDT", "interval": "1m"},
            replay_callable=lambda **kwargs: None,
        )

def test_daily_loss_resets_before_next_day_risk_evaluation(monkeypatch):
    portfolio = Portfolio(Decimal("1000"))
    risk_manager = SimpleNamespace(evaluate=lambda *args: None)

    day_one = 1_000
    day_two = 86_401_000
    candles = [candle(day_one), candle(day_two)]
    points = [
        make_point(day_one, portfolio, risk_manager),
        make_point(day_two, portfolio, risk_manager),
    ]

    intent = EntryIntent(
        symbol="BTCUSDT",
        decision_time_ms=day_one,
        quantity=Decimal("1"),
        stop_price=Decimal("90"),
        target_price=Decimal("120"),
        fee_rate=Decimal("0.01"),
        slippage_bps=Decimal("0"),
    )

    observed_daily_losses = []

    def fake_evaluate(*args, **kwargs):
        observed_daily_losses.append(
            kwargs["risk_context"].current_daily_loss_quote
        )
        if len(observed_daily_losses) == 1:
            return SimpleNamespace(
                signal=SimpleNamespace(action="BUY_CANDIDATE"),
                authorization=SimpleNamespace(approved=True),
                intent=intent,
            )
        return SimpleNamespace(signal=SimpleNamespace(action="HOLD"))

    monkeypatch.setattr(stateful, "evaluate_authorized_entry", fake_evaluate)
    monkeypatch.setattr(
        stateful,
        "validate_historical_candles",
        lambda values, **kwargs: tuple(values),
    )

    entry = SimulatedFill(
        "BTCUSDT", OrderSide.BUY, day_one,
        Decimal("100"), Decimal("100"),
        Decimal("1"), Decimal("1"), Decimal("0"),
    )
    exit_fill = SimulatedFill(
        "BTCUSDT", OrderSide.SELL, day_one + 1_000,
        Decimal("90"), Decimal("90"),
        Decimal("1"), Decimal("1"), Decimal("0"),
    )
    losing_trade = SimulatedTrade(
        "BTCUSDT", entry, exit_fill, IntrabarExit.STOP,
        Decimal("-10"), Decimal("-12"),
    )

    def fake_replay(**kwargs):
        decision_callback = kwargs["decision_callback"]
        event_callback = kwargs["event_callback"]

        first_result = decision_callback(
            candles[0], Decimal("1000"), Decimal("0")
        )
        assert tuple(first_result) == (intent,)

        event_callback(
            ReplayEvent(
                "ENTRY", day_one, Decimal("899"), Decimal("1"), entry
            )
        )
        event_callback(
            ReplayEvent(
                "EXIT", day_one + 1_000, Decimal("988"),
                Decimal("0"), exit_fill, losing_trade
            )
        )

        second_result = decision_callback(
            candles[1], Decimal("988"), Decimal("0")
        )
        assert tuple(second_result) == ()

        return SimpleNamespace(trades=(losing_trade,), rejected_intents=())

    stateful.run_stateful_historical_orchestration(
        candles=candles,
        decision_points=points,
        initial_cash=Decimal("1000"),
        replay_kwargs={"symbol": "BTCUSDT", "interval": "1m"},
        replay_callable=fake_replay,
    )

    assert observed_daily_losses == [Decimal("0"), Decimal("0")], (
        "The next UTC day's risk evaluation must not inherit the previous "
        "day's realized loss."
    )
