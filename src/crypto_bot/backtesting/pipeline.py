"""Historical strategy-to-risk authorization bridge; never submits orders."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from crypto_bot.backtesting.engine import EntryIntent
from crypto_bot.backtesting.orchestrator import (
    StrategyOrchestrationError,
    evaluate_strategy,
)
from crypto_bot.backtesting.risk_gate import (
    EntryAuthorization,
    authorize_strategy_entry,
)
from crypto_bot.strategies.common import MarketCostEstimate, StrategySignal


@dataclass(frozen=True)
class BacktestEntryEvaluation:
    """One point-in-time strategy result and its optional risk-approved replay intent."""

    signal: StrategySignal | None
    authorization: EntryAuthorization
    intent: EntryIntent | None


def _reject(reason: str, signal: StrategySignal | None = None) -> BacktestEntryEvaluation:
    return BacktestEntryEvaluation(
        signal=signal,
        authorization=EntryAuthorization(False, (reason,), None, None),
        intent=None,
    )


def _positive_decimal(value: Any, label: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{label} is invalid") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError(f"{label} must be positive and finite")
    return result


def evaluate_authorized_entry(
    style: str,
    entry_batch: Any,
    context_batches: Any,
    regime: Any,
    *,
    config: Any,
    cost_estimate: MarketCostEstimate | None,
    portfolio: Any,
    risk_manager: Any,
    risk_context: Any,
    position_open: bool = False,
) -> BacktestEntryEvaluation:
    """Evaluate one historical decision point and authorize it before replay.

    Call once per historical decision point using only data available at that point.
    The returned EntryIntent is simulation input only. It cannot place exchange orders.
    The caller remains responsible for maintaining chronological portfolio/risk state
    between decision points and for passing approved intents to replay_single_position.
    """
    try:
        signal = evaluate_strategy(
            style,
            entry_batch,
            context_batches,
            regime,
            config=config,
            cost_estimate=cost_estimate,
            position_open=position_open,
        )
    except (StrategyOrchestrationError, TypeError, ValueError, AttributeError, ArithmeticError) as exc:
        return _reject(f"Strategy evaluation failed closed: {type(exc).__name__}")

    # Only BUY_CANDIDATE can become a new long spot entry. Other signals remain advisory.
    action = getattr(
        signal.action,
        "name",
        getattr(signal.action, "value", signal.action),
    )
    if str(action).strip().upper() != "BUY_CANDIDATE":
        authorization = authorize_strategy_entry(
            signal,
            portfolio=portfolio,
            risk_manager=risk_manager,
            risk_context=risk_context,
            cost_estimate=cost_estimate,
        )
        return BacktestEntryEvaluation(signal, authorization, None)

    # Validate every field needed by the replay intent before invoking the risk manager.
    try:
        symbol = signal.symbol
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("signal symbol is missing")

        decision_time = signal.decision_time_ms
        if isinstance(decision_time, bool) or not isinstance(decision_time, int) or decision_time < 0:
            raise ValueError("signal decision timestamp is missing or invalid")

        entry_price = _positive_decimal(signal.reference_price, "entry price")
        stop_price = _positive_decimal(signal.stop_price, "stop price")
        target_price = _positive_decimal(signal.target_price, "target price")
        if not stop_price < entry_price < target_price:
            raise ValueError("long entry levels must satisfy stop < entry < target")

        if not isinstance(cost_estimate, MarketCostEstimate):
            raise ValueError("an explicit MarketCostEstimate is required")
        # The strategy layer checks freshness against the data snapshot. This bridge
        # also rejects future-dated costs relative to the decision timestamp.
        if cost_estimate.observed_at_ms > decision_time:
            raise ValueError("cost estimate is newer than the strategy decision timestamp")

        fee_bps = _positive_decimal(cost_estimate.round_trip_fee_bps, "round-trip fee estimate") \
            if cost_estimate.round_trip_fee_bps > 0 else Decimal("0")
        slip_bps = _positive_decimal(cost_estimate.round_trip_slippage_bps, "round-trip slippage estimate") \
            if cost_estimate.round_trip_slippage_bps > 0 else Decimal("0")
        spread_bps = _positive_decimal(cost_estimate.spread_bps, "spread estimate") \
            if cost_estimate.spread_bps > 0 else Decimal("0")
    except (AttributeError, TypeError, ValueError, InvalidOperation, ArithmeticError) as exc:
        return _reject(f"Entry intent validation failed closed: {exc}", signal)

    authorization = authorize_strategy_entry(
        signal,
        portfolio=portfolio,
        risk_manager=risk_manager,
        risk_context=risk_context,
        cost_estimate=cost_estimate,
    )
    if not authorization.approved:
        return BacktestEntryEvaluation(signal, authorization, None)

    try:
        decision = authorization.decision
        quantity = _positive_decimal(
            getattr(decision, "permitted_quantity", None),
            "risk-approved quantity",
        )
        # Cost estimates are round-trip estimates. Convert them to per-fill inputs:
        # fee rate = half round-trip fee bps; slippage = half round-trip slippage
        # plus half the full spread width. The fill model applies these per side.
        fee_rate = fee_bps / Decimal("20000")
        per_fill_slippage_bps = (slip_bps + spread_bps) / Decimal("2")
        intent = EntryIntent(
            symbol=symbol,
            decision_time_ms=decision_time,
            quantity=quantity,
            stop_price=stop_price,
            target_price=target_price,
            fee_rate=fee_rate,
            slippage_bps=per_fill_slippage_bps,
        )
    except (AttributeError, TypeError, ValueError, InvalidOperation, ArithmeticError) as exc:
        return _reject(f"Approved decision could not form a valid replay intent: {exc}", signal)

    return BacktestEntryEvaluation(signal, authorization, intent)
