from dataclasses import dataclass
from decimal import Decimal

from crypto_bot.backtesting.engine import EntryIntent
from types import SimpleNamespace

import pytest

from crypto_bot.backtesting.orchestration import (
    BacktestOrchestrationError,
    DecisionPoint,
    run_historical_orchestration,
)


@dataclass
class FakeCandle:
    symbol: str = "BTCUSDT"
    interval: str = "1m"
    open_time_ms: int = 0
    close_time_ms: int = 59_999


def candles():
    # The validator expects actual project Candle objects; these tests replace
    # its validation boundary so orchestration behavior can be tested alone.
    return [FakeCandle(), FakeCandle(open_time_ms=60_000, close_time_ms=119_999)]


def point(timestamp=60_000):
    return DecisionPoint(
        decision_time_ms=timestamp,
        style="scalp",
        entry_batch=object(),
        context_batches={},
        regime=object(),
        config=object(),
        cost_estimate=object(),
        portfolio=object(),
        risk_manager=object(),
        risk_context=object(),
    )


def test_requires_replay_callable(monkeypatch):
    import crypto_bot.backtesting.history as history
    monkeypatch.setattr(history, "validate_historical_candles", lambda *a, **k: a[0])
    with pytest.raises(BacktestOrchestrationError, match="replay callable"):
        run_historical_orchestration(
            candles=candles(),
            decision_points=[],
            replay_callable=None,
        )


def test_rejects_non_chronological_decisions(monkeypatch):
    import crypto_bot.backtesting.history as history
    monkeypatch.setattr(history, "validate_historical_candles", lambda *a, **k: a[0])
    with pytest.raises(BacktestOrchestrationError, match="strictly chronological"):
        run_historical_orchestration(
            candles=candles(),
            decision_points=[point(60_000), point(60_000)],
            replay_callable=lambda **kwargs: None,
        )


def test_only_approved_intents_reach_replay(monkeypatch):
    import crypto_bot.backtesting.history as history
    import crypto_bot.backtesting.orchestration as orchestration

    monkeypatch.setattr(history, "validate_historical_candles", lambda *a, **k: a[0])

    intent = EntryIntent(
        symbol="BTCUSDT",
        decision_time_ms=60_000,
        quantity=Decimal("1"),
        stop_price=Decimal("90"),
        target_price=Decimal("120"),
        fee_rate=Decimal("0"),
        slippage_bps=Decimal("0"),
    )


    def fake_evaluate(*args, **kwargs):
        return SimpleNamespace(
            signal=SimpleNamespace(action=SimpleNamespace(name="BUY_CANDIDATE")),
            authorization=SimpleNamespace(approved=True),
            intent=intent,
        )

    monkeypatch.setattr(orchestration, "evaluate_authorized_entry", fake_evaluate)
    received = {}

    def replay_callable(**kwargs):
        received.update(kwargs)
        return "simulated-result"

    report = run_historical_orchestration(
        candles=candles(),
        decision_points=[point()],
        replay_callable=replay_callable,
    )

    assert report.replay_result == "simulated-result"
    assert report.approved_entries == 1
    assert report.approved_intents == (intent,)
    assert received["entry_intents"] == (intent,)
    assert len(received["candles"]) == 2



def test_approved_non_entryintent_is_rejected(monkeypatch):
    import crypto_bot.backtesting.history as history
    import crypto_bot.backtesting.orchestration as orchestration

    monkeypatch.setattr(history, "validate_historical_candles", lambda *a, **k: a[0])
    monkeypatch.setattr(
        orchestration,
        "evaluate_authorized_entry",
        lambda *args, **kwargs: SimpleNamespace(
            signal=SimpleNamespace(action=SimpleNamespace(name="BUY_CANDIDATE")),
            authorization=SimpleNamespace(approved=True),
            intent="not-an-entry-intent",
        ),
    )

    received = {}
    report = run_historical_orchestration(
        candles=candles(),
        decision_points=[point()],
        replay_callable=lambda **kwargs: received.update(kwargs) or "ok",
    )

    assert report.approved_entries == 0
    assert report.rejected_entries == 1
    assert report.approved_intents == ()
    assert received["entry_intents"] == ()


def test_approved_intent_with_mismatched_timestamp_is_rejected(monkeypatch):
    import crypto_bot.backtesting.history as history
    import crypto_bot.backtesting.orchestration as orchestration

    monkeypatch.setattr(history, "validate_historical_candles", lambda *a, **k: a[0])
    mismatched = EntryIntent(
        symbol="BTCUSDT",
        decision_time_ms=0,
        quantity=Decimal("1"),
        stop_price=Decimal("90"),
        target_price=Decimal("120"),
        fee_rate=Decimal("0"),
        slippage_bps=Decimal("0"),
    )
    monkeypatch.setattr(
        orchestration,
        "evaluate_authorized_entry",
        lambda *args, **kwargs: SimpleNamespace(
            signal=SimpleNamespace(action=SimpleNamespace(name="BUY_CANDIDATE")),
            authorization=SimpleNamespace(approved=True),
            intent=mismatched,
        ),
    )

    received = {}
    report = run_historical_orchestration(
        candles=candles(),
        decision_points=[point()],
        replay_callable=lambda **kwargs: received.update(kwargs) or "ok",
    )

    assert report.approved_entries == 0
    assert report.rejected_entries == 1
    assert received["entry_intents"] == ()

def test_rejected_entry_never_reaches_replay(monkeypatch):
    import crypto_bot.backtesting.history as history
    import crypto_bot.backtesting.orchestration as orchestration

    monkeypatch.setattr(history, "validate_historical_candles", lambda *a, **k: a[0])
    monkeypatch.setattr(
        orchestration,
        "evaluate_authorized_entry",
        lambda *args, **kwargs: SimpleNamespace(
            signal=SimpleNamespace(action=SimpleNamespace(name="BUY_CANDIDATE")),
            authorization=SimpleNamespace(approved=False),
            intent=None,
        ),
    )

    received = {}
    report = run_historical_orchestration(
        candles=candles(),
        decision_points=[point()],
        replay_callable=lambda **kwargs: received.update(kwargs) or "ok",
    )

    assert report.rejected_entries == 1
    assert report.approved_entries == 0
    assert received["entry_intents"] == ()


def test_non_buy_signal_is_skipped(monkeypatch):
    import crypto_bot.backtesting.history as history
    import crypto_bot.backtesting.orchestration as orchestration

    monkeypatch.setattr(history, "validate_historical_candles", lambda *a, **k: a[0])
    monkeypatch.setattr(
        orchestration,
        "evaluate_authorized_entry",
        lambda *args, **kwargs: SimpleNamespace(
            signal=SimpleNamespace(action=SimpleNamespace(name="NO_ACTION")),
            authorization=None,
            intent=None,
        ),
    )

    report = run_historical_orchestration(
        candles=candles(),
        decision_points=[point()],
        replay_callable=lambda **kwargs: "ok",
    )
    assert report.skipped_non_entry_signals == 1
    assert report.approved_intents == ()


def test_replay_kwargs_cannot_override_core_inputs(monkeypatch):
    import crypto_bot.backtesting.history as history
    monkeypatch.setattr(history, "validate_historical_candles", lambda *a, **k: a[0])
    with pytest.raises(BacktestOrchestrationError, match="must not override"):
        run_historical_orchestration(
            candles=candles(),
            decision_points=[],
            replay_callable=lambda **kwargs: None,
            replay_kwargs={"entry_intents": ["unapproved"]},
        )
