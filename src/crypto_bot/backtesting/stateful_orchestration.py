"""Stateful, historical-only strategy orchestration.

This module never creates exchange clients or submits exchange orders.
All entries are simulated and must pass the existing strategy/risk pipeline.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any, Callable, Iterable, Mapping, Sequence

from .engine import (
    EntryIntent,
    ReplayEvent,
    replay_single_position,
)
from .orchestration import (
    BacktestOrchestrationError,
    DecisionPoint,
    OrchestrationReport,
)
from .pipeline import evaluate_authorized_entry
from .fills import OrderSide
from .history import validate_historical_candles


class StatefulOrchestrationError(BacktestOrchestrationError):
    """Raised when stateful historical orchestration cannot proceed safely."""


def run_stateful_historical_orchestration(
    *,
    candles: Sequence[Any],
    decision_points: Iterable[DecisionPoint],
    initial_cash: Decimal,
    replay_kwargs: Mapping[str, Any],
    replay_callable: Callable[..., Any] = replay_single_position,
) -> OrchestrationReport:
    """Evaluate decisions at candle boundaries against evolving account state.

    Decision timestamps must exactly match candle open boundaries. Prepared
    strategy inputs must contain no information unavailable at that boundary.

    The supplied mutable Portfolio is updated from simulated fill events.
    Every BUY_CANDIDATE passes through evaluate_authorized_entry and the central
    RiskManager. The RiskManager's persistent kill switch remains authoritative.
    """
    if not callable(replay_callable):
        raise StatefulOrchestrationError("A simulation replay callable is required.")

    if not isinstance(candles, (list, tuple)) or not candles:
        raise StatefulOrchestrationError(
            "Historical candles must be a non-empty list or tuple."
        )

    if not isinstance(replay_kwargs, Mapping):
        raise StatefulOrchestrationError("replay_kwargs must be a mapping.")

    forbidden = {
        "candles", "entry_intents", "initial_cash",
        "event_callback", "decision_callback",
    }
    if forbidden.intersection(replay_kwargs):
        raise StatefulOrchestrationError(
            "replay_kwargs cannot override candles, intents, cash, or callbacks."
        )

    try:
        cash = Decimal(str(initial_cash))
    except Exception:
        raise StatefulOrchestrationError("initial_cash must be a valid decimal.") from None

    if not cash.is_finite() or cash <= 0:
        raise StatefulOrchestrationError("initial_cash must be positive and finite.")

    first = candles[0]
    symbol = getattr(first, "symbol", None)
    interval = getattr(first, "interval", None)

    if not isinstance(symbol, str) or not symbol.strip():
        raise StatefulOrchestrationError("Historical candle symbol is invalid.")
    if not isinstance(interval, str) or not interval.strip():
        raise StatefulOrchestrationError("Historical candle interval is invalid.")

    try:
        validated = validate_historical_candles(
            candles,
            symbol=symbol,
            interval=interval,
            require_contiguous=True,
        )
    except Exception as exc:
        raise StatefulOrchestrationError(
            f"Historical candle validation failed: {type(exc).__name__}"
        ) from exc

    points = list(decision_points)
    if not points:
        raise StatefulOrchestrationError(
            "At least one DecisionPoint is required to provide the account and risk manager."
        )

    candle_by_open: dict[int, Any] = {}
    for candle in validated:
        timestamp = getattr(candle, "open_time_ms", None)
        if isinstance(timestamp, bool) or not isinstance(timestamp, int):
            raise StatefulOrchestrationError("Invalid candle open timestamp.")
        if timestamp in candle_by_open:
            raise StatefulOrchestrationError("Duplicate candle open timestamp.")
        candle_by_open[timestamp] = candle

    points_by_time: dict[int, DecisionPoint] = {}
    previous = -1
    for point in points:
        if not isinstance(point, DecisionPoint):
            raise StatefulOrchestrationError(
                "Every decision point must be a DecisionPoint."
            )
        timestamp = point.decision_time_ms
        if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
            raise StatefulOrchestrationError("Invalid decision timestamp.")
        if timestamp <= previous:
            raise StatefulOrchestrationError(
                "Decision points must be strictly chronological."
            )
        if timestamp not in candle_by_open:
            raise StatefulOrchestrationError(
                f"Decision time {timestamp} must exactly match a candle open boundary."
            )
        if not isinstance(point.context_batches, Mapping):
            raise StatefulOrchestrationError("Context batches must be a mapping.")
        previous = timestamp
        points_by_time[timestamp] = point

    account = points[0].portfolio
    risk_manager = points[0].risk_manager

    if not callable(getattr(account, "snapshot", None)):
        raise StatefulOrchestrationError(
            "DecisionPoint.portfolio must be a mutable Portfolio with snapshot()."
        )
    if not callable(getattr(account, "buy", None)) or not callable(
        getattr(account, "sell", None)
    ):
        raise StatefulOrchestrationError(
            "Portfolio must provide buy() and sell() accounting methods."
        )
    if not callable(getattr(risk_manager, "evaluate", None)):
        raise StatefulOrchestrationError("A valid central RiskManager is required.")

    for point in points:
        if point.portfolio is not account:
            raise StatefulOrchestrationError(
                "All decision points must share the same mutable Portfolio instance."
            )
        if point.risk_manager is not risk_manager:
            raise StatefulOrchestrationError(
                "All decision points must share the same RiskManager instance."
            )
        if point.risk_context is None:
            raise StatefulOrchestrationError("Risk context is required.")

    try:
        initial_snapshot = account.snapshot({})
        if initial_snapshot.positions:
            raise StatefulOrchestrationError(
                "Stateful replay must begin with no open positions."
            )
        if Decimal(str(account.cash)) != cash:
            raise StatefulOrchestrationError(
                "initial_cash must equal the Portfolio's starting cash."
            )
    except StatefulOrchestrationError:
        raise
    except Exception as exc:
        raise StatefulOrchestrationError(
            f"Could not validate starting portfolio: {type(exc).__name__}"
        ) from exc

    approved_intents: list[EntryIntent] = []
    approved_count = 0
    rejected_count = 0
    skipped_count = 0
    daily_loss = Decimal("0")
    active_day: int | None = None
    position_asset: str | None = None

    def roll_day(timestamp_ms: int) -> None:
        nonlocal active_day, daily_loss
        day = timestamp_ms // 86_400_000
        if active_day is None or day != active_day:
            active_day = day
            daily_loss = Decimal("0")

    def account_event(event: ReplayEvent) -> None:
        nonlocal position_asset, daily_loss

        fill = event.fill
        if fill.symbol.upper() != symbol.upper():
            raise StatefulOrchestrationError(
                "Replay emitted a fill for a symbol outside this single-symbol replay."
            )

        if event.kind == "ENTRY":
            if position_asset is not None or fill.side is not OrderSide.BUY:
                raise StatefulOrchestrationError("Invalid or duplicate simulated entry event.")

            account.buy(
                fill.symbol,
                fill.quantity,
                fill.fill_price,
                fee=fill.fee_quote,
            )
            position_asset = fill.symbol.upper()

        elif event.kind == "EXIT":
            if position_asset is None or fill.side is not OrderSide.SELL:
                raise StatefulOrchestrationError("Exit event has no matching open position.")

            account.sell(
                fill.symbol,
                fill.quantity,
                fill.fill_price,
                fee=fill.fee_quote,
            )
            position_asset = None

            trade = event.trade
            if trade is not None and trade.net_pnl_quote < 0:
                roll_day(event.timestamp_ms)
                daily_loss += abs(trade.net_pnl_quote)

        else:
            raise StatefulOrchestrationError(
                f"Unknown replay event type: {event.kind!r}"
            )

        # The replay engine and accounting ledger must never drift silently.
        if Decimal(str(account.cash)) != event.cash_after:
            raise StatefulOrchestrationError(
                "Portfolio cash diverged from replay cash; stopping fail-closed."
            )

    def make_decision(
        candle: Any,
        _replay_cash: Decimal,
        replay_quantity: Decimal,
    ) -> Iterable[EntryIntent]:
        nonlocal approved_count, rejected_count, skipped_count

        timestamp = candle.open_time_ms
        roll_day(timestamp)
        point = points_by_time.get(timestamp)
        if point is None:
            return ()

        try:
            if position_asset is not None:
                marks = {position_asset: Decimal(str(candle.open))}
            else:
                marks = {}

            snapshot = account.snapshot(marks)
            open_count = len(snapshot.positions)

            # Refresh state-dependent risk values without weakening any other
            # caller-provided validity/freshness flags.
            context = replace(
                point.risk_context,
                current_daily_loss_quote=daily_loss,
                open_position_count=open_count,
            )

            evaluation = evaluate_authorized_entry(
                point.style,
                point.entry_batch,
                point.context_batches,
                point.regime,
                config=point.config,
                cost_estimate=point.cost_estimate,
                portfolio=snapshot,
                risk_manager=point.risk_manager,
                risk_context=context,
                position_open=(replay_quantity > 0 or open_count > 0),
            )
        except Exception as exc:
            raise StatefulOrchestrationError(
                f"Decision at {timestamp} failed closed ({type(exc).__name__})."
            ) from exc

        signal = getattr(evaluation, "signal", None)
        if signal is None:
            rejected_count += 1
            return ()

        action = getattr(
            signal.action,
            "name",
            getattr(signal.action, "value", signal.action),
        )
        action = str(action).strip().upper()

        if action != "BUY_CANDIDATE":
            skipped_count += 1
            return ()

        authorization = getattr(evaluation, "authorization", None)
        intent = getattr(evaluation, "intent", None)

        if (
            authorization is None
            or getattr(authorization, "approved", False) is not True
            or not isinstance(intent, EntryIntent)
        ):
            rejected_count += 1
            return ()

        if intent.decision_time_ms != timestamp:
            rejected_count += 1
            return ()

        if intent.symbol.upper() != symbol.upper():
            rejected_count += 1
            return ()

        approved_intents.append(intent)
        approved_count += 1
        return (intent,)

    try:
        replay_result = replay_callable(
            candles=validated,
            entry_intents=(),
            initial_cash=cash,
            event_callback=account_event,
            decision_callback=make_decision,
            **dict(replay_kwargs),
        )
    except Exception as exc:
        raise StatefulOrchestrationError(
            f"Stateful simulation stopped safely ({type(exc).__name__}): {exc}"
        ) from exc

    # Preserve the exact intents approved by the central risk pipeline.
    # Approval does not guarantee that the replay will fill every intent.
    return OrchestrationReport(
        replay_result=replay_result,
        evaluated_points=len(points),
        approved_entries=approved_count,
        rejected_entries=rejected_count,
        skipped_non_entry_signals=skipped_count,
        approved_intents=tuple(approved_intents),
    )
