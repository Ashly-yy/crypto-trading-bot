"""Fail-closed central risk checks. This module never places orders."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from crypto_bot.control import KillSwitch

from .models import RiskDecision, RiskLimits, TradeProposal


@dataclass(frozen=True)
class RiskContext:
    """Fresh, validated context supplied by the caller for each decision."""
    current_daily_loss_quote: Decimal
    open_position_count: int
    data_is_fresh: bool = True
    account_state_is_valid: bool = True
    exchange_filters_are_valid: bool = True


class RiskManager:
    def __init__(self, limits: RiskLimits, *, kill_switch: "KillSwitch") -> None:
        if kill_switch is None or not hasattr(kill_switch, "can_open_entries"):
            raise ValueError("a persistent kill switch is required")
        self._limits = limits
        self._kill_switch = kill_switch

    @property
    def limits(self) -> RiskLimits:
        """Expose the configured limits through a read-only public interface."""
        return self._limits
    def evaluate(self, proposal: TradeProposal, portfolio: Any, context: RiskContext) -> RiskDecision:
        """Approve/reject a proposed spot buy; require persistent switch RUNNING state."""
        reasons: list[str] = []
        try:
            if not isinstance(proposal, TradeProposal):
                raise TypeError("proposal must be TradeProposal")
            if not isinstance(context, RiskContext):
                raise TypeError("context must be RiskContext")
            # Query the persistent switch on every evaluation; errors fail closed.
            if not self._kill_switch.can_open_entries:
                reasons.append(f"kill switch blocks new entries (state: {self._kill_switch.state.value})")
            cash = portfolio.cash
            equity = portfolio.equity
            exposure_by_asset = portfolio.exposure_by_asset
            total_exposure = portfolio.total_exposure
            daily_loss = context.current_daily_loss_quote
            if not isinstance(cash, Decimal) or not cash.is_finite() or cash < 0:
                raise ValueError("portfolio cash is missing or invalid")
            if not isinstance(equity, Decimal) or not equity.is_finite() or equity <= 0:
                raise ValueError("portfolio equity is missing or invalid")
            if not isinstance(total_exposure, Decimal) or not total_exposure.is_finite() or total_exposure < 0:
                raise ValueError("portfolio exposure is missing or invalid")
            if not isinstance(exposure_by_asset, dict):
                raise ValueError("asset exposure map is missing or invalid")
            if not isinstance(daily_loss, Decimal) or not daily_loss.is_finite() or daily_loss < 0:
                raise ValueError("current daily loss is missing or invalid")
            if isinstance(context.open_position_count, bool) or not isinstance(context.open_position_count, int) or context.open_position_count < 0:
                raise ValueError("open position count is missing or invalid")
            for flag_name in (
                "data_is_fresh",
                "account_state_is_valid",
                "exchange_filters_are_valid",
            ):
                if not isinstance(getattr(context, flag_name), bool):
                    raise ValueError(f"{flag_name} must be a bool")
        except (AttributeError, TypeError, ValueError, OSError) as exc:
            return RiskDecision(False, (f"invalid or missing risk input: {exc}",))

        if not context.data_is_fresh:
            reasons.append("market data is stale")
        if not context.account_state_is_valid:
            reasons.append("account state is invalid")
        if not context.exchange_filters_are_valid:
            reasons.append("exchange filters are invalid")

        order_quote = proposal.quantity * proposal.entry_price
        trade_loss = (
            proposal.quantity * (proposal.entry_price - proposal.stop_price)
            + proposal.estimated_fees_quote
            + proposal.estimated_slippage_quote
        )
        existing_asset_exposure = exposure_by_asset.get(proposal.asset, Decimal("0"))
        if not isinstance(existing_asset_exposure, Decimal) or not existing_asset_exposure.is_finite() or existing_asset_exposure < 0:
            reasons.append("existing asset exposure is invalid")
            existing_asset_exposure = Decimal("0")

        if order_quote + proposal.estimated_fees_quote > cash:
            reasons.append("insufficient available cash")
        if order_quote > self._limits.max_order_quote:
            reasons.append("order exceeds maximum order size")
        if existing_asset_exposure + order_quote > self._limits.max_asset_exposure_quote:
            reasons.append("asset exposure limit would be exceeded")
        if total_exposure + order_quote > self._limits.max_total_exposure_quote:
            reasons.append("total exposure limit would be exceeded")
        is_new_position = existing_asset_exposure == 0
        if is_new_position and context.open_position_count >= self._limits.max_open_positions:
            reasons.append("maximum open positions would be exceeded")
        if trade_loss > self._limits.max_trade_loss_quote:
            reasons.append("estimated trade loss exceeds configured limit")
        if daily_loss >= self._limits.max_daily_loss_quote:
            reasons.append("daily loss limit has been reached")
        elif daily_loss + trade_loss > self._limits.max_daily_loss_quote:
            reasons.append("trade could breach the remaining daily loss budget")

        if reasons:
            return RiskDecision(False, tuple(reasons), estimated_trade_loss_quote=trade_loss)
        return RiskDecision(
            True,
            ("all configured risk checks passed",),
            permitted_quantity=proposal.quantity,
            order_quote=order_quote,
            estimated_trade_loss_quote=trade_loss,
        )
