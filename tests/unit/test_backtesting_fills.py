from decimal import Decimal
import pytest
from crypto_bot.backtesting.fills import (
    FillModelError, IntrabarExit, OrderSide, resolve_intrabar_exit, simulate_market_fill,
)

class CandleStub:
    def __init__(self, low, high):
        self.low = Decimal(str(low))
        self.high = Decimal(str(high))

def test_buy_fill_applies_adverse_slippage_and_fee():
    fill = simulate_market_fill(symbol="btcusdt", side=OrderSide.BUY, timestamp_ms=1000,
        reference_price=Decimal("100"), quantity=Decimal("2"),
        fee_rate=Decimal("0.001"), slippage_bps=Decimal("10"))
    assert fill.symbol == "BTCUSDT"
    assert fill.fill_price == Decimal("100.10")
    assert fill.slippage_cost_quote == Decimal("0.20")
    assert fill.fee_quote == Decimal("0.20020")

def test_sell_fill_applies_adverse_slippage_and_fee():
    fill = simulate_market_fill(symbol="ETHUSDT", side=OrderSide.SELL, timestamp_ms=1000,
        reference_price=Decimal("100"), quantity=Decimal("2"),
        fee_rate=Decimal("0.001"), slippage_bps=Decimal("10"))
    assert fill.fill_price == Decimal("99.90")
    assert fill.slippage_cost_quote == Decimal("0.20")
    assert fill.fee_quote == Decimal("0.19980")

@pytest.mark.parametrize("kwargs", [
    {"symbol": "", "side": OrderSide.BUY, "timestamp_ms": 1, "reference_price": "10", "quantity": "1", "fee_rate": "0.001", "slippage_bps": "1"},
    {"symbol": "BTCUSDT", "side": OrderSide.BUY, "timestamp_ms": -1, "reference_price": "10", "quantity": "1", "fee_rate": "0.001", "slippage_bps": "1"},
    {"symbol": "BTCUSDT", "side": OrderSide.BUY, "timestamp_ms": 1, "reference_price": "0", "quantity": "1", "fee_rate": "0.001", "slippage_bps": "1"},
    {"symbol": "BTCUSDT", "side": OrderSide.BUY, "timestamp_ms": 1, "reference_price": "10", "quantity": "0", "fee_rate": "0.001", "slippage_bps": "1"},
    {"symbol": "BTCUSDT", "side": OrderSide.BUY, "timestamp_ms": 1, "reference_price": "10", "quantity": "1", "fee_rate": "-0.1", "slippage_bps": "1"},
    {"symbol": "BTCUSDT", "side": OrderSide.BUY, "timestamp_ms": 1, "reference_price": "10", "quantity": "1", "fee_rate": "1", "slippage_bps": "1"},
    {"symbol": "BTCUSDT", "side": OrderSide.BUY, "timestamp_ms": 1, "reference_price": "10", "quantity": "1", "fee_rate": "0.001", "slippage_bps": "-1"},
    {"symbol": "BTCUSDT", "side": OrderSide.BUY, "timestamp_ms": 1, "reference_price": "10", "quantity": "1", "fee_rate": "0.001", "slippage_bps": "10000"},
])
def test_invalid_fill_inputs_are_rejected(kwargs):
    for key in ("reference_price", "quantity", "fee_rate", "slippage_bps"):
        kwargs[key] = Decimal(kwargs[key])
    with pytest.raises(FillModelError):
        simulate_market_fill(**kwargs)

def test_stop_wins_when_stop_and_target_are_both_touched():
    assert resolve_intrabar_exit(candle=CandleStub("89", "111"),
        stop_price=Decimal("90"), target_price=Decimal("110")) is IntrabarExit.STOP

def test_stop_only_touch():
    assert resolve_intrabar_exit(candle=CandleStub("89", "105"),
        stop_price=Decimal("90"), target_price=Decimal("110")) is IntrabarExit.STOP

def test_target_only_touch():
    assert resolve_intrabar_exit(candle=CandleStub("95", "111"),
        stop_price=Decimal("90"), target_price=Decimal("110")) is IntrabarExit.TARGET

def test_no_exit_when_neither_level_touched():
    assert resolve_intrabar_exit(candle=CandleStub("95", "105"),
        stop_price=Decimal("90"), target_price=Decimal("110")) is IntrabarExit.NONE

@pytest.mark.parametrize("stop,target", [("0", "110"), ("90", "0"), ("110", "90"), ("NaN", "110")])
def test_invalid_exit_levels_fail_closed(stop, target):
    with pytest.raises(FillModelError):
        resolve_intrabar_exit(candle=CandleStub("95", "105"),
            stop_price=Decimal(stop), target_price=Decimal(target))

def test_short_positions_are_rejected_for_spot_only_bot():
    with pytest.raises(FillModelError, match="spot-only"):
        resolve_intrabar_exit(candle=CandleStub("95", "105"),
            stop_price=Decimal("90"), target_price=Decimal("110"), position_side="SHORT")

def test_invalid_candle_range_is_rejected():
    with pytest.raises(FillModelError):
        resolve_intrabar_exit(candle=CandleStub("110", "90"),
            stop_price=Decimal("90"), target_price=Decimal("110"))
