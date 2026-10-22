"""Explicit, fail-closed initializer for a simulated 100 USDT portfolio."""
from __future__ import annotations

import argparse
import gc
import os
from decimal import Decimal
from pathlib import Path
from tempfile import mkdtemp

from crypto_bot.config.settings import TradingMode, get_settings
from crypto_bot.control.kill_switch import KillSwitch
from crypto_bot.portfolio.accounting import Portfolio
from crypto_bot.portfolio.persistence import PortfolioStore
from crypto_bot.storage.order_ledger import OrderLedger


def initialize_state(directory: Path) -> None:
    """Create a fresh simulated state directory without overwriting existing state."""
    directory = Path(directory).resolve()

    settings = get_settings()
    if settings.default_trading_mode is not TradingMode.BACKTEST:
        raise RuntimeError("Initialization requires BACKTEST mode.")
    if settings.live_trading_enabled:
        raise RuntimeError("Initialization requires live trading to be disabled.")

    # Refuse an existing target, even if it is empty or only partially initialized.
    if directory.exists():
        raise FileExistsError(
            f"Refusing to initialize an existing state directory: {directory}"
        )

    directory.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(mkdtemp(prefix=".crypto-bot-state-staging-", dir=directory.parent))
    try:
        PortfolioStore(staging / "portfolio.sqlite3").save(
            Portfolio(initial_cash=Decimal("100"), quote_currency="USDT")
        )
        KillSwitch(staging / "kill_switch.sqlite3", initialize=True)
        OrderLedger(staging / "order_ledger.sqlite3", initialize=True)

        # Validate all three databases before publishing the directory.
        restored = PortfolioStore(staging / "portfolio.sqlite3").load()
        if (
            restored.initial_cash != Decimal("100")
            or restored.cash != Decimal("100")
            or restored.quote_currency != "USDT"
            or restored.positions
            or restored.realized_pnl != Decimal("0")
            or restored.fees_paid != Decimal("0")
        ):
            raise RuntimeError("Staged portfolio validation failed.")

        kill_switch = KillSwitch(
            staging / "kill_switch.sqlite3", initialize=False
        )
        if kill_switch.state.value != "STOPPED" or kill_switch.can_open_entries:
            raise RuntimeError("Staged kill-switch validation failed.")

        ledger = OrderLedger(staging / "order_ledger.sqlite3", initialize=False)
        if ledger.list_unresolved():
            raise RuntimeError("Staged order-ledger validation failed.")

        # Release SQLite-backed objects before publishing on Windows.
        del kill_switch, ledger, restored
        gc.collect()

        # Never merge with or overwrite an existing state directory.
        if directory.exists():
            raise FileExistsError(
                f"Refusing to publish over existing state: {directory}"
            )
        os.rename(staging, directory)

    except Exception:
        # Preserve staging data for inspection rather than deleting possibly useful state.
        raise


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Initialize a fresh 100 USDT simulated portfolio."
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Explicitly confirm creation of the configured state directory.",
    )
    args = parser.parse_args()

    if not args.confirm:
        parser.error("No changes made. Re-run with --confirm only after reviewing the target.")

    initialize_state(settings.state_directory)
    print("Initialized simulated state: 100 USDT; no positions; no orders.")
    print("Kill switch: STOPPED. Entry authorization remains disabled.")
    print("No exchange connection or order submission was performed.")


if __name__ == "__main__":
    main()
