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
