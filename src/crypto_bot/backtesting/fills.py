# Deterministic historical-only fill and intrabar exit simulation primitives.
# No exchange connection or order submission exists in this module.
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

class FillModelError(ValueError):
    pass

class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

class IntrabarExit(str, Enum):
    NONE = "NONE"
    STOP = "STOP"
    TARGET = "TARGET"

def _decimal(value: Any, name: str) -> Decimal:
    if isinstance(value, bool):
        raise FillModelError(f"{name} must be a finite decimal number")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise FillModelError(f"{name} must be a finite decimal number") from None
    if not result.is_finite():
        raise FillModelError(f"{name} must be a finite decimal number")
    return result

@dataclass(frozen=True)
class SimulatedFill:
    symbol: str
    side: OrderSide
    timestamp_ms: int
    reference_price: Decimal
    fill_price: Decimal
    quantity: Decimal
    fee_quote: Decimal
    slippage_cost_quote: Decimal

def simulate_market_fill(*, symbol: str, side: OrderSide, timestamp_ms: int,
                          reference_price: Decimal, quantity: Decimal,
                          fee_rate: Decimal, slippage_bps: Decimal) -> SimulatedFill:
    # BUY fills pay above reference; SELL fills receive below reference.
    # Fee is calculated on resulting fill notional. Cost inputs are mandatory.
    if not isinstance(symbol, str) or not symbol.strip():
        raise FillModelError("symbol must be a non-empty string")
    if not isinstance(side, OrderSide):
        raise FillModelError("side must be an OrderSide")
    if isinstance(timestamp_ms, bool) or not isinstance(timestamp_ms, int) or timestamp_ms < 0:
        raise FillModelError("timestamp_ms must be a non-negative integer")
    reference = _decimal(reference_price, "reference_price")
    qty = _decimal(quantity, "quantity")
    rate = _decimal(fee_rate, "fee_rate")
    slip_bps = _decimal(slippage_bps, "slippage_bps")
    if reference <= 0: raise FillModelError("reference_price must be positive")
    if qty <= 0: raise FillModelError("quantity must be positive")
    if rate < 0 or rate >= 1: raise FillModelError("fee_rate must be at least zero and less than one")
    if slip_bps < 0 or slip_bps >= Decimal("10000"):
        raise FillModelError("slippage_bps must be at least zero and less than 10000")
    slip_fraction = slip_bps / Decimal("10000")
    fill_price = (reference * (Decimal("1") + slip_fraction)
                  if side is OrderSide.BUY else reference * (Decimal("1") - slip_fraction))
    notional = fill_price * qty
    return SimulatedFill(
        symbol=symbol.strip().upper(), side=side, timestamp_ms=timestamp_ms,
        reference_price=reference, fill_price=fill_price, quantity=qty,
        fee_quote=notional * rate,
        slippage_cost_quote=abs(fill_price - reference) * qty,
    )

def resolve_intrabar_exit(*, candle: Any, stop_price: Decimal, target_price: Decimal,
                          position_side: str = "LONG") -> IntrabarExit:
    # If both levels touch within one candle, the approved stop-first rule wins.
    if position_side != "LONG":
        raise FillModelError("only LONG positions are supported; spot-only mode forbids shorts")
    try:
        low = _decimal(candle.low, "candle.low")
        high = _decimal(candle.high, "candle.high")
        stop = _decimal(stop_price, "stop_price")
        target = _decimal(target_price, "target_price")
    except AttributeError:
        raise FillModelError("candle must expose low and high prices") from None
    if low <= 0 or high <= 0 or low > high:
        raise FillModelError("candle high/low values are invalid")
    if stop <= 0 or target <= 0:
        raise FillModelError("stop_price and target_price must be positive")
    if not stop < target:
        raise FillModelError("LONG position requires stop_price < target_price")
    if low <= stop: return IntrabarExit.STOP
    if high >= target: return IntrabarExit.TARGET
    return IntrabarExit.NONE
