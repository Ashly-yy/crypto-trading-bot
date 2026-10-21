from decimal import Decimal

from crypto_bot.backtesting.engine import EntryIntent, replay_single_position
from crypto_bot.market_data.candles import Candle


def candle(open_ms, o, h, l, c, interval_ms=60_000):
    return Candle(
        symbol="BTCUSDT", interval="1m", open_time_ms=open_ms,
        open=Decimal(str(o)), high=Decimal(str(h)), low=Decimal(str(l)),
        close=Decimal(str(c)), volume=Decimal("10"),
        close_time_ms=open_ms + interval_ms - 1,
        quote_asset_volume=Decimal("1000"), number_of_trades=1,
        taker_buy_base_volume=Decimal("5"), taker_buy_quote_volume=Decimal("500"),
    )


def intent(decision_ms, stop="90", target="110", qty="1"):
    return EntryIntent(
        symbol="BTCUSDT", decision_time_ms=decision_ms, quantity=Decimal(qty),
        stop_price=Decimal(stop), target_price=Decimal(target),
        fee_rate=Decimal("0.001"), slippage_bps=Decimal("10"),
    )


def test_entry_uses_first_open_at_or_after_decision_time_and_stop_exit():
    candles = [
        candle(0, 100, 105, 95, 101),
        candle(60_000, 102, 105, 101, 103),
        candle(120_000, 103, 104, 89, 92),
    ]
    result = replay_single_position(
        candles=candles, symbol="BTCUSDT", interval="1m",
        entry_intents=[intent(60_000)], initial_cash=Decimal("1000"),
    )
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry.timestamp_ms == 60_000
    assert trade.entry.reference_price == Decimal("102")
    assert trade.exit_reason.value == "STOP"
    assert trade.net_pnl_quote < 0
    assert not result.ending_position_open


def test_stop_first_when_both_levels_touched():
    candles = [
        candle(0, 100, 105, 95, 101),
        candle(60_000, 102, 105, 101, 103),
        candle(120_000, 103, 111, 89, 100),
    ]
    result = replay_single_position(
        candles=candles, symbol="BTCUSDT", interval="1m",
        entry_intents=[intent(60_000)], initial_cash=Decimal("1000"),
    )
    assert result.trades[0].exit_reason.value == "STOP"


def test_insufficient_cash_rejects_entry():
    candles = [candle(0, 100, 105, 95, 101)]
    result = replay_single_position(
        candles=candles, symbol="BTCUSDT", interval="1m",
        entry_intents=[intent(0, qty="100")], initial_cash=Decimal("10"),
    )
    assert result.rejected_intents == ("BTCUSDT: insufficient simulated cash",)
    assert not result.ending_position_open


def test_wrong_symbol_intent_is_rejected():
    candles = [candle(0, 100, 105, 95, 101)]
    bad = EntryIntent("ETHUSDT", 0, Decimal("1"), Decimal("90"), Decimal("110"),
                      Decimal("0.001"), Decimal("10"))
    result = replay_single_position(
        candles=candles, symbol="BTCUSDT", interval="1m",
        entry_intents=[bad], initial_cash=Decimal("1000"),
    )
    assert "symbol does not match" in result.rejected_intents[0]


def test_no_entry_before_decision_time():
    candles = [candle(0, 100, 105, 95, 101)]
    result = replay_single_position(
        candles=candles, symbol="BTCUSDT", interval="1m",
        entry_intents=[intent(1)], initial_cash=Decimal("1000"),
    )
    assert not result.trades
    assert not result.ending_position_open



def test_replay_emits_chronological_entry_and_exit_events_with_state():
    candles = [
        candle(0, 100, 105, 95, 101),
        candle(60_000, 102, 105, 101, 103),
        candle(120_000, 103, 104, 89, 92),
    ]
    events = []

    result = replay_single_position(
        candles=candles,
        symbol="BTCUSDT",
        interval="1m",
        entry_intents=[intent(60_000)],
        initial_cash=Decimal("1000"),
        event_callback=events.append,
    )

    assert [event.kind for event in events] == ["ENTRY", "EXIT"]
    assert events[0].timestamp_ms == 60_000
    assert events[1].timestamp_ms == candles[2].close_time_ms
    assert events[0].fill.side.value == "BUY"
    assert events[1].fill.side.value == "SELL"
    assert events[0].position_quantity_after == Decimal("1")
    assert events[1].position_quantity_after == Decimal("0")
    assert events[0].cash_after < Decimal("1000")
    assert events[1].cash_after > events[0].cash_after
    assert events[1].trade == result.trades[0]
    assert result.ending_position_open is False


def test_replay_without_event_callback_preserves_existing_api_behavior():
    candles = [candle(0, 100, 105, 95, 101)]

    result = replay_single_position(
        candles=candles,
        symbol="BTCUSDT",
        interval="1m",
        entry_intents=[intent(0)],
        initial_cash=Decimal("1000"),
    )

    assert result.ending_position_open is True
    assert result.trades == ()


def test_decision_callback_sees_position_before_same_candle_exit():
    candles = [
        candle(0, 100, 105, 95, 101),
        candle(60_000, 102, 105, 89, 92),
    ]
    observations = []

    def decide(current_candle, cash, position_quantity):
        observations.append(
            (current_candle.open_time_ms, cash, position_quantity)
        )
        # Try to enter at the first boundary and again at the boundary
        # where the existing position will later hit its stop.
        return [intent(current_candle.open_time_ms)]

    result = replay_single_position(
        candles=candles,
        symbol="BTCUSDT",
        interval="1m",
        entry_intents=[],
        initial_cash=Decimal("1000"),
        decision_callback=decide,
    )

    # At the second candle's open, the position must still be visible:
    # its stop is only discovered from that candle's later low.
    assert observations[1][0] == 60_000
    assert observations[1][2] == Decimal("1")
    assert observations[1][1] < Decimal("1000")

    # The new intent cannot re-enter while the position is still open
    # at the decision boundary, even though the stop is hit later.
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason.value == "STOP"
    assert result.skipped_busy_intents == 1
    assert result.ending_position_open is False


def test_decision_callback_can_supply_intent_at_historical_candle_boundary():
    candles = [
        candle(0, 100, 105, 95, 101),
        candle(60_000, 102, 105, 101, 103),
        candle(120_000, 103, 109, 102, 108),
    ]
    callback_calls = []

    def decide(current_candle, cash, position_quantity):
        callback_calls.append(
            (current_candle.open_time_ms, cash, position_quantity)
        )
        if current_candle.open_time_ms == 60_000:
            return [intent(60_000)]
        return []

    result = replay_single_position(
        candles=candles,
        symbol="BTCUSDT",
        interval="1m",
        entry_intents=[],
        initial_cash=Decimal("1000"),
        decision_callback=decide,
    )

    assert len(callback_calls) == 3
    assert callback_calls[0][0] == 0
    assert callback_calls[1][0] == 60_000
    assert callback_calls[2][0] == 120_000
    assert callback_calls[1][1] == Decimal("1000")
    assert callback_calls[2][2] == Decimal("1")
    assert result.ending_position_open is True
    assert result.trades == ()


def test_decision_callback_rejects_future_dated_intent():
    candles = [candle(0, 100, 105, 95, 101)]

    def decide(current_candle, cash, position_quantity):
        return [intent(60_000)]

    from crypto_bot.backtesting.engine import BacktestEngineError

    import pytest
    with pytest.raises(BacktestEngineError, match="after the current candle boundary"):
        replay_single_position(
            candles=candles,
            symbol="BTCUSDT",
            interval="1m",
            entry_intents=[],
            initial_cash=Decimal("1000"),
            decision_callback=decide,
        )


def test_decision_callback_rejects_non_intent_results():
    candles = [candle(0, 100, 105, 95, 101)]

    from crypto_bot.backtesting.engine import BacktestEngineError

    import pytest
    with pytest.raises(BacktestEngineError, match="only EntryIntent"):
        replay_single_position(
            candles=candles,
            symbol="BTCUSDT",
            interval="1m",
            entry_intents=[],
            initial_cash=Decimal("1000"),
            decision_callback=lambda *args: ["not-an-intent"],
        )


def test_strategy_exit_closes_position_at_boundary_and_emits_one_exit():
    from crypto_bot.backtesting.engine import ExitIntent
    from crypto_bot.backtesting.fills import IntrabarExit, OrderSide
    candles = [candle(0, 100, 105, 95, 101), candle(60_000, 100, 120, 80, 110), candle(120_000, 110, 111, 109, 110)]
    events = []
    def decide(current_candle, cash, position_quantity):
        if current_candle.open_time_ms == 60_000:
            assert position_quantity == Decimal("1")
            return [ExitIntent("BTCUSDT", 60_000)]
        return []
    result = replay_single_position(candles=candles, symbol="BTCUSDT", interval="1m", entry_intents=[intent(0)], initial_cash=Decimal("1000"), event_callback=events.append, decision_callback=decide)
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason is IntrabarExit.STRATEGY
    assert result.trades[0].exit.timestamp_ms == 60_000
    assert result.trades[0].exit.side is OrderSide.SELL
    assert [event.kind for event in events] == ["ENTRY", "EXIT"]
    assert result.ending_position_open is False


def test_strategy_exit_without_position_is_rejected_not_short_opened():
    from crypto_bot.backtesting.engine import ExitIntent
    result = replay_single_position(candles=[candle(0, 100, 105, 95, 101)], symbol="BTCUSDT", interval="1m", entry_intents=[], initial_cash=Decimal("1000"), decision_callback=lambda c, cash, qty: [ExitIntent("BTCUSDT", c.open_time_ms)])
    assert not result.trades
    assert not result.ending_position_open
    assert any("no spot position is open" in item for item in result.rejected_intents)


def test_strategy_exit_wrong_symbol_is_rejected_and_position_remains_open():
    from crypto_bot.backtesting.engine import ExitIntent
    def decide(current_candle, cash, position_quantity):
        if current_candle.open_time_ms == 60_000: return [ExitIntent("ETHUSDT", 60_000)]
        return []
    result = replay_single_position(candles=[candle(0, 100, 105, 95, 101), candle(60_000, 102, 105, 101, 103)], symbol="BTCUSDT", interval="1m", entry_intents=[intent(0)], initial_cash=Decimal("1000"), decision_callback=decide)
    assert not result.trades
    assert result.ending_position_open is True
    assert any("exit symbol does not match" in item for item in result.rejected_intents)
