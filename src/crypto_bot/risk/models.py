"""Typed inputs and outputs for the central risk gate."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping


@dataclass(frozen=True)
class RiskLimits:
    """Explicitly configured limits; this step does not choose policy percentages."""

    max_order_quote: Decimal
    max_asset_exposure_quote: Decimal
    max_total_exposure_quote: Decimal
    max_open_positions: int
    max_trade_loss_quote: Decimal
    max_daily_loss_quote: Decimal

    def __post_init__(self) -> None:
        decimal_fields = (
            "max_order_quote",
            "max_asset_exposure_quote",
            "max_total_exposure_quote",
            "max_trade_loss_quote",
            "max_daily_loss_quote",
        )
        for name in decimal_fields:
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ValueError(f"{name} must be a finite positive Decimal")
        if isinstance(self.max_open_positions, bool) or not isinstance(self.max_open_positions, int) or self.max_open_positions < 1:
            raise ValueError("max_open_positions must be a positive integer")


@dataclass(frozen=True)
class TradeProposal:
    """A proposed spot buy, expressed in quote-currency units."""

    asset: str
    quantity: Decimal
    entry_price: Decimal
    stop_price: Decimal
    estimated_fees_quote: Decimal = Decimal("0")
    estimated_slippage_quote: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if not isinstance(self.asset, str) or not self.asset.strip():
            raise ValueError("asset must be a non-empty string")
        for name in ("quantity", "entry_price", "stop_price", "estimated_fees_quote", "estimated_slippage_quote"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f"{name} must be a finite Decimal")
        if self.quantity <= 0 or self.entry_price <= 0 or self.stop_price <= 0:
            raise ValueError("quantity, entry_price, and stop_price must be positive")
        if self.stop_price >= self.entry_price:
            raise ValueError("spot buy stop_price must be below entry_price")
        if self.estimated_fees_quote < 0 or self.estimated_slippage_quote < 0:
            raise ValueError("estimated fees and slippage cannot be negative")


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reasons: tuple[str, ...]
    permitted_quantity: Decimal = Decimal("0")
    order_quote: Decimal = Decimal("0")
    estimated_trade_loss_quote: Decimal = Decimal("0")
