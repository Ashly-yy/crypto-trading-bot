"""Read-only recovery coordination; never authorizes trading."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from crypto_bot.control.kill_switch import KillSwitch
from crypto_bot.portfolio.persistence import PortfolioStore
from crypto_bot.storage.order_ledger import OrderLedger


@dataclass(frozen=True)
class RecoveryReport:
    portfolio: Any | None
    kill_switch_state: str
    unresolved_orders: tuple[Any, ...]
    portfolio_error: str | None
    ledger_error: str | None

    @property
    def state_inspection_passed(self) -> bool:
        """Whether persisted portfolio and ledger state were inspected successfully."""
        return (
            self.portfolio is not None
            and self.portfolio_error is None
            and self.ledger_error is None
        )

    @property
    def recovery_checks_passed(self) -> bool:
        """Whether inspection succeeded and there are no unresolved orders."""
        return self.state_inspection_passed and not self.unresolved_orders

    @property
    def entries_authorized(self) -> bool:
        """Recovery itself must never authorize order entry."""
        return False


class RecoveryCoordinator:
    """Inspect persisted state without changing it or contacting an exchange."""

    def __init__(
        self,
        *,
        portfolio_store: PortfolioStore,
        kill_switch: KillSwitch,
        order_ledger: OrderLedger,
    ) -> None:
        self._portfolio_store = portfolio_store
        self._kill_switch = kill_switch
        self._order_ledger = order_ledger

    def recover(self) -> RecoveryReport:
        portfolio = None
        portfolio_error = None
        unresolved_orders: tuple[Any, ...] = ()
        ledger_error = None

        try:
            portfolio = self._portfolio_store.load()
        except Exception as exc:
            portfolio_error = f"{type(exc).__name__}: {exc}"

        try:
            unresolved_orders = self._order_ledger.list_unresolved()
        except Exception as exc:
            ledger_error = f"{type(exc).__name__}: {exc}"

        # The kill switch's state accessor is fail-closed on persisted-state
        # errors. This coordinator deliberately does not transition it.
        try:
            state = self._kill_switch.state.value
        except Exception:
            state = "EMERGENCY_HALTED"

        return RecoveryReport(
            portfolio=portfolio,
            kill_switch_state=state,
            unresolved_orders=unresolved_orders,
            portfolio_error=portfolio_error,
            ledger_error=ledger_error,
        )
