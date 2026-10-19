"""Fail-closed adapter for simulated historical entry authorization."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from crypto_bot.risk.models import TradeProposal


class BacktestRiskGateError(ValueError):
    """Invalid strategy signal or transaction-cost estimate."""


@dataclass(frozen=True)
class EntryAuthorization:
    approved: bool
    reasons: tuple[str, ...]
    proposal: TradeProposal | None
    decision: Any | None


def _decimal(value: Any, name: str, allow_zero: bool = False) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BacktestRiskGateError(f"Invalid {name}") from exc

    if not result.is_finite():
        raise BacktestRiskGateError(f"Invalid {name}")

    if result < 0 if allow_zero else result <= 0:
        raise BacktestRiskGateError(f"Invalid {name}")

    return result


def authorize_strategy_entry(
    signal: Any,
    *,
    portfolio: Any,
    risk_manager: Any,
    risk_context: Any,
    cost_estimate: Any,
) -> EntryAuthorization:
    """Submit BUY_CANDIDATE signals to the existing central risk manager."""
    try:
        # Accept the enum name and value, as well as compatible string inputs.
        raw_action = getattr(
            signal.action,
            "name",
            getattr(signal.action, "value", signal.action),
        )
        action = str(raw_action).strip().upper()

        if action != "BUY_CANDIDATE":
            return EntryAuthorization(
                False, ("Signal is not a BUY_CANDIDATE",), None, None
            )

        asset = str(signal.symbol).strip()
        if not asset:
            raise BacktestRiskGateError("Missing symbol")

        entry = _decimal(signal.reference_price, "entry price")
        stop = _decimal(signal.stop_price, "stop price")

        if stop >= entry:
            raise BacktestRiskGateError(
                "Stop price must be below entry price for a long trade"
            )

        max_quote = _decimal(
            risk_manager.limits.max_order_quote, "maximum order value"
        )
        quantity = max_quote / entry
        quote_value = quantity * entry

        fee_bps = _decimal(
            cost_estimate.round_trip_fee_bps, "fee estimate", allow_zero=True
        )
        slippage_bps = _decimal(
            cost_estimate.round_trip_slippage_bps,
            "slippage estimate",
            allow_zero=True,
        )
        spread_bps = _decimal(
            cost_estimate.spread_bps, "spread estimate", allow_zero=True
        )

        fees = quote_value * fee_bps / Decimal("10000")
        slippage = quote_value * (
            slippage_bps + spread_bps
        ) / Decimal("10000")

        proposal = TradeProposal(
            asset=asset,
            quantity=quantity,
            entry_price=entry,
            stop_price=stop,
            estimated_fees_quote=fees,
            estimated_slippage_quote=slippage,
        )

        # Use the actual RiskManager positional interface.
        decision = risk_manager.evaluate(proposal, portfolio, risk_context)

        return EntryAuthorization(
            approved=bool(decision.approved),
            reasons=tuple(decision.reasons),
            proposal=proposal,
            decision=decision,
        )

    except BacktestRiskGateError as exc:
        return EntryAuthorization(False, (str(exc),), None, None)
    except (AttributeError, TypeError, ValueError, InvalidOperation) as exc:
        return EntryAuthorization(
            False,
            (f"Invalid risk-gate input: {type(exc).__name__}",),
            None,
            None,
        )
