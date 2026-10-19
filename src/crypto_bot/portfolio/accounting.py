"""Deterministic, quote-currency portfolio accounting for spot-trading research."""
from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Mapping


def _decimal(value: Decimal | int | str, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, str)):
        raise TypeError(f"{name} must be Decimal, int, or str")
    try:
        result = value if isinstance(value, Decimal) else Decimal(value)
    except Exception as exc:
        raise ValueError(f"{name} must be a valid decimal number") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def _positive(value: Decimal | int | str, name: str) -> Decimal:
    result = _decimal(value, name)
    if result <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return result


def _non_negative(value: Decimal | int | str, name: str) -> Decimal:
    result = _decimal(value, name)
    if result < 0:
        raise ValueError(f"{name} cannot be negative")
    return result


@dataclass(frozen=True)
class Position:
    """Open long spot position; cost basis is denominated in quote currency."""
    asset: str
    quantity: Decimal
    cost_basis: Decimal

    def __post_init__(self) -> None:
        if not self.asset or self.asset.strip() != self.asset:
            raise ValueError("asset must be a non-empty, trimmed symbol")
        object.__setattr__(self, "quantity", _positive(self.quantity, "quantity"))
        object.__setattr__(self, "cost_basis", _positive(self.cost_basis, "cost_basis"))

    @property
    def average_entry_price(self) -> Decimal:
        return self.cost_basis / self.quantity


@dataclass(frozen=True)
class PortfolioSnapshot:
    quote_currency: str
    cash: Decimal
    positions: Mapping[str, Position]
    realized_pnl: Decimal
    fees_paid: Decimal
    market_values: Mapping[str, Decimal]
    unrealized_pnl: Decimal
    equity: Decimal
    exposure_by_asset: Mapping[str, Decimal]
    total_exposure: Decimal


@dataclass
class Portfolio:
    """In-memory spot ledger; buy fees enter cost basis, sell fees reduce proceeds.

    Fees are informational in snapshots and are never deducted a second time.
    This class does not execute orders, fetch prices, or persist portfolio state.
    """
    initial_cash: Decimal
    quote_currency: str = "USDT"
    cash: Decimal = field(init=False)
    _positions: dict[str, Position] = field(default_factory=dict, init=False, repr=False)
    realized_pnl: Decimal = field(default=Decimal("0"), init=False)
    fees_paid: Decimal = field(default=Decimal("0"), init=False)

    def __post_init__(self) -> None:
        self.initial_cash = _non_negative(self.initial_cash, "initial_cash")
        if not self.quote_currency or self.quote_currency.strip() != self.quote_currency:
            raise ValueError("quote_currency must be a non-empty, trimmed symbol")
        self.cash = self.initial_cash

    @property
    def positions(self) -> Mapping[str, Position]:
        return dict(self._positions)

    def buy(self, asset: str, quantity: Decimal | int | str, price: Decimal | int | str,
            fee: Decimal | int | str = Decimal("0")) -> Position:
        """Record a filled buy; quantity is base asset and price is quote/base."""
        self._validate_asset(asset)
        qty, unit_price = _positive(quantity, "quantity"), _positive(price, "price")
        fee_amount = _non_negative(fee, "fee")
        gross = qty * unit_price
        if gross + fee_amount > self.cash:
            raise ValueError("insufficient cash for buy including fee")
        old = self._positions.get(asset)
        position = Position(asset, qty + (old.quantity if old else Decimal("0")),
                            gross + fee_amount + (old.cost_basis if old else Decimal("0")))
        self.cash -= gross + fee_amount
        self.fees_paid += fee_amount
        self._positions[asset] = position
        return position

    def sell(self, asset: str, quantity: Decimal | int | str, price: Decimal | int | str,
             fee: Decimal | int | str = Decimal("0")) -> Decimal:
        """Record a filled sell and return this fill's realized P&L."""
        self._validate_asset(asset)
        qty, unit_price = _positive(quantity, "quantity"), _positive(price, "price")
        fee_amount = _non_negative(fee, "fee")
        position = self._positions.get(asset)
        if position is None or qty > position.quantity:
            raise ValueError("sell quantity exceeds the available spot position")
        proceeds = qty * unit_price
        allocated_cost = position.cost_basis * (qty / position.quantity)
        fill_pnl = proceeds - fee_amount - allocated_cost
        remaining_qty, remaining_cost = position.quantity - qty, position.cost_basis - allocated_cost
        self.cash += proceeds - fee_amount
        self.realized_pnl += fill_pnl
        self.fees_paid += fee_amount
        if remaining_qty == 0:
            del self._positions[asset]
        else:
            self._positions[asset] = Position(asset, remaining_qty, remaining_cost)
        return fill_pnl

    def snapshot(self, mark_prices: Mapping[str, Decimal | int | str]) -> PortfolioSnapshot:
        """Value open positions at caller-provided marks; every open asset needs a mark."""
        prices: dict[str, Decimal] = {}
        for asset in self._positions:
            if asset not in mark_prices:
                raise ValueError(f"missing mark price for open position: {asset}")
            prices[asset] = _positive(mark_prices[asset], f"mark_prices[{asset}]")
        market_values = {asset: self._positions[asset].quantity * prices[asset] for asset in self._positions}
        unrealized = sum((market_values[a] - p.cost_basis for a, p in self._positions.items()), Decimal("0"))
        exposure = sum(market_values.values(), Decimal("0"))
        return PortfolioSnapshot(self.quote_currency, self.cash, dict(self._positions), self.realized_pnl,
                                 self.fees_paid, market_values, unrealized, self.cash + exposure,
                                 dict(market_values), exposure)

    @staticmethod
    def _validate_asset(asset: str) -> None:
        if not isinstance(asset, str) or not asset or asset.strip() != asset:
            raise ValueError("asset must be a non-empty, trimmed symbol")
