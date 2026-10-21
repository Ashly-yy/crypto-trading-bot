"""Chronological, historical-only single-position replay primitives."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Iterable, Sequence

from crypto_bot.backtesting.fills import (
    IntrabarExit, OrderSide, SimulatedFill, resolve_intrabar_exit, simulate_market_fill,
)
from crypto_bot.backtesting.history import validate_historical_candles


class BacktestEngineError(ValueError):
    pass


@dataclass(frozen=True)
class EntryIntent:
    """A strategy candidate emitted after its decision candle has closed."""
    symbol: str
    decision_time_ms: int
    quantity: Decimal
    stop_price: Decimal
    target_price: Decimal
    fee_rate: Decimal
    slippage_bps: Decimal


@dataclass(frozen=True)
class ExitIntent:
    """Close an existing simulated LONG spot position at a replay boundary."""
    symbol: str
    decision_time_ms: int
    reason: str = "strategy_exit"


@dataclass(frozen=True)
class SimulatedTrade:
    symbol: str
    entry: SimulatedFill
    exit: SimulatedFill
    exit_reason: IntrabarExit
    gross_pnl_quote: Decimal
    net_pnl_quote: Decimal


@dataclass(frozen=True)
class ReplayResult:
    trades: tuple[SimulatedTrade, ...]
    rejected_intents: tuple[str, ...]
    skipped_busy_intents: int
    ending_position_open: bool


@dataclass(frozen=True)
class ReplayEvent:
    """One chronological simulated fill and its immediate account state."""

    kind: str
    timestamp_ms: int
    cash_after: Decimal
    position_quantity_after: Decimal
    fill: SimulatedFill
    trade: SimulatedTrade | None = None


def replay_single_position(
    *,
    candles: Sequence[object],
    symbol: str,
    interval: str,
    entry_intents: Iterable[EntryIntent],
    initial_cash: Decimal,
    require_contiguous: bool = True,
    event_callback: Callable[[ReplayEvent], None] | None = None,
    decision_callback: Callable[
        [object, Decimal, Decimal], Iterable[EntryIntent | ExitIntent] | None
    ] | None = None,
) -> ReplayResult:
    """Replay precomputed intents without future-candle lookahead or exchange access.

    Intents are considered only at/after their decision time and enter at the
    first candle open whose timestamp is >= decision_time_ms. One position at a
    time is allowed. Stop/target checks use each later candle's high/low, with
    stop-first handling inherited from the fill component.

    This is a replay primitive, not yet a strategy orchestrator or a replacement
    for the central risk manager. Callers must supply intents already evaluated
    by their strategy and risk pipeline before using this function.
    """
    try:
        cash = Decimal(str(initial_cash))
    except Exception:
        raise BacktestEngineError("initial_cash must be a valid decimal") from None
    if not cash.is_finite() or cash <= 0:
        raise BacktestEngineError("initial_cash must be positive and finite")
    if not candles:
        raise BacktestEngineError("candles must not be empty")
    validate_historical_candles(
        candles, symbol=symbol, interval=interval, require_contiguous=require_contiguous
    )

    ordered_intents = sorted(tuple(entry_intents), key=lambda item: item.decision_time_ms)
    rejected: list[str] = []
    trades: list[SimulatedTrade] = []
    skipped_busy = 0
    active = None
    next_intent = 0

    for candle in candles:
        position_was_open_at_candle_open = active is not None

        if decision_callback is not None:
            try:
                position_quantity = (
                    active["entry"].quantity if active is not None else Decimal("0")
                )
                supplied_intents = decision_callback(
                    candle, cash, position_quantity
                )
                if supplied_intents is None:
                    supplied_intents = ()
                new_intents = tuple(supplied_intents)
            except Exception as exc:
                raise BacktestEngineError(
                    f"Historical decision callback failed ({type(exc).__name__})."
                ) from None

            if any(not isinstance(item, (EntryIntent, ExitIntent)) for item in new_intents):
                raise BacktestEngineError(
                    "Decision callback must return only EntryIntent or ExitIntent objects."
                )

            if any(item.decision_time_ms > candle.open_time_ms for item in new_intents):
                raise BacktestEngineError(
                    "Decision callback returned an intent from after the current candle boundary."
                )

            # Execute strategy exits at this candle open, before intrabar stop/target checks.
            for exit_intent in (item for item in new_intents if isinstance(item, ExitIntent)):
                if not isinstance(exit_intent.symbol, str) or exit_intent.symbol.strip().upper() != symbol.upper():
                    rejected.append(f"{getattr(exit_intent, 'symbol', '')}: exit symbol does not match replay symbol")
                    continue
                if not isinstance(exit_intent.reason, str) or not exit_intent.reason.strip():
                    rejected.append(f"{symbol}: exit reason must be a non-empty string")
                    continue
                if active is None:
                    rejected.append(f"{symbol}: strategy exit rejected because no spot position is open")
                    continue
                entry_intent = active["intent"]
                entry_fill = active["entry"]
                exit_fill = simulate_market_fill(
                    symbol=symbol, side=OrderSide.SELL,
                    timestamp_ms=candle.open_time_ms, reference_price=candle.open,
                    quantity=entry_fill.quantity, fee_rate=entry_intent.fee_rate,
                    slippage_bps=entry_intent.slippage_bps,
                )
                gross = (exit_fill.fill_price - entry_fill.fill_price) * entry_fill.quantity
                net = gross - entry_fill.fee_quote - exit_fill.fee_quote
                cash += exit_fill.fill_price * exit_fill.quantity - exit_fill.fee_quote
                trade = SimulatedTrade(symbol, entry_fill, exit_fill, IntrabarExit.STRATEGY, gross, net)
                trades.append(trade)
                active = None
                if event_callback is not None:
                    event_callback(ReplayEvent(
                        kind="EXIT", timestamp_ms=exit_fill.timestamp_ms,
                        cash_after=cash, position_quantity_after=Decimal("0"),
                        fill=exit_fill, trade=trade,
                    ))

            callback_entries = [item for item in new_intents if isinstance(item, EntryIntent)]
            if callback_entries:
                pending = ordered_intents[next_intent:] + callback_entries
                pending.sort(key=lambda item: item.decision_time_ms)
                ordered_intents = ordered_intents[:next_intent] + pending

        while next_intent < len(ordered_intents) and ordered_intents[next_intent].decision_time_ms <= candle.open_time_ms:
            intent = ordered_intents[next_intent]
            next_intent += 1
            if intent.symbol.upper() != symbol.upper():
                rejected.append(f"{intent.symbol}: symbol does not match replay symbol")
                continue
            if intent.quantity <= 0 or not intent.quantity.is_finite():
                rejected.append(f"{intent.symbol}: quantity must be positive and finite")
                continue
            if intent.stop_price <= 0 or intent.target_price <= intent.stop_price:
                rejected.append(f"{intent.symbol}: invalid LONG stop/target levels")
                continue
            if active is not None:
                skipped_busy += 1
                continue
            entry = simulate_market_fill(
                symbol=symbol, side=OrderSide.BUY, timestamp_ms=candle.open_time_ms,
                reference_price=candle.open, quantity=intent.quantity,
                fee_rate=intent.fee_rate, slippage_bps=intent.slippage_bps,
            )
            required_cash = entry.fill_price * entry.quantity + entry.fee_quote
            if required_cash > cash:
                rejected.append(f"{intent.symbol}: insufficient simulated cash")
                continue
            cash -= required_cash
            active = {
                "intent": intent,
                "entry": entry,
                "entry_candle_open_ms": candle.open_time_ms,
            }
            if event_callback is not None:
                event_callback(ReplayEvent(
                    kind="ENTRY",
                    timestamp_ms=entry.timestamp_ms,
                    cash_after=cash,
                    position_quantity_after=entry.quantity,
                    fill=entry,
                ))
            # Do not test stop/target on the same candle used for entry.

        # Resolve an existing position only after decisions at this candle boundary.
        if (
            position_was_open_at_candle_open
            and active is not None
            and candle.open_time_ms > active["entry_candle_open_ms"]
        ):
            exit_reason = resolve_intrabar_exit(
                candle=candle,
                stop_price=active["intent"].stop_price,
                target_price=active["intent"].target_price,
            )
            if exit_reason is not IntrabarExit.NONE:
                reference = (active["intent"].stop_price if exit_reason is IntrabarExit.STOP
                             else active["intent"].target_price)
                exit_fill = simulate_market_fill(
                    symbol=symbol, side=OrderSide.SELL, timestamp_ms=candle.close_time_ms,
                    reference_price=reference, quantity=active["entry"].quantity,
                    fee_rate=active["intent"].fee_rate,
                    slippage_bps=active["intent"].slippage_bps,
                )
                entry = active["entry"]
                gross = (exit_fill.fill_price - entry.fill_price) * entry.quantity
                net = gross - entry.fee_quote - exit_fill.fee_quote
                cash += exit_fill.fill_price * exit_fill.quantity - exit_fill.fee_quote
                trade = SimulatedTrade(symbol, entry, exit_fill, exit_reason, gross, net)
                trades.append(trade)
                active = None
                if event_callback is not None:
                    event_callback(ReplayEvent(
                        kind="EXIT",
                        timestamp_ms=exit_fill.timestamp_ms,
                        cash_after=cash,
                        position_quantity_after=Decimal("0"),
                        fill=exit_fill,
                        trade=trade,
                    ))

    return ReplayResult(tuple(trades), tuple(rejected), skipped_busy, active is not None)
