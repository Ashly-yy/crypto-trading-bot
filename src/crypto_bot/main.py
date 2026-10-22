"""Fail-closed application startup and read-only recovery inspection."""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path

from crypto_bot.config.settings import get_settings
from crypto_bot.control.kill_switch import KillSwitch
from crypto_bot.logging_config import configure_logging
from crypto_bot.portfolio.persistence import PortfolioStore
from crypto_bot.recovery_coordinator import RecoveryCoordinator
from crypto_bot.storage.order_ledger import OrderLedger
from crypto_bot.telegram.events import TelegramEventNotifier
from crypto_bot.telegram.notifier import TelegramNotifier


_REQUIRED_TABLES = {
    "portfolio.sqlite3": {"portfolio_snapshot"},
    "kill_switch.sqlite3": {"kill_switch_state", "kill_switch_events"},
    "order_ledger.sqlite3": {"testnet_order_ledger"},
}

_REQUIRED_COLUMNS = {
    "portfolio.sqlite3": {
        "portfolio_snapshot": {"singleton", "payload", "checksum"},
    },
    "kill_switch.sqlite3": {
        "kill_switch_state": {"singleton_id", "state"},
        "kill_switch_events": {
            "id", "previous_state", "new_state", "actor", "reason", "created_at"
        },
    },
    "order_ledger.sqlite3": {
        "testnet_order_ledger": {
            "client_order_id", "symbol", "side", "order_type", "quantity",
            "price", "state", "exchange_order_id", "rejection_reason",
            "created_at", "updated_at",
        },
    },
}


def recovery_databases_ready(directory: Path) -> tuple[bool, str]:
    """Check existing database files and schemas without creating or changing them."""
    for filename, required_tables in _REQUIRED_TABLES.items():
        path = directory / filename
        if not path.is_file():
            return False, f"Required recovery database is missing: {filename}"

        try:
            uri = path.resolve().as_uri() + "?mode=ro"
            with sqlite3.connect(uri, uri=True) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                missing = required_tables - tables
                if missing:
                    return False, (
                        f"{filename} is missing required tables: "
                        + ", ".join(sorted(missing))
                    )
                for table_name, required_columns in _REQUIRED_COLUMNS[filename].items():
                    rows = connection.execute(
                        f"PRAGMA table_info({table_name})"
                    ).fetchall()
                    actual_columns = {row[1] for row in rows}
                    missing_columns = required_columns - actual_columns
                    if missing_columns:
                        return False, (
                            f"{filename}.{table_name} is missing required columns: "
                            + ", ".join(sorted(missing_columns))
                        )
                if filename == "kill_switch.sqlite3":
                    row = connection.execute(
                        "SELECT state FROM kill_switch_state WHERE singleton_id = 1"
                    ).fetchone()
                    if row is None:
                        return False, "Persisted kill-switch state is missing"

            if filename == "portfolio.sqlite3":
                try:
                    PortfolioStore(path).load()
                except (ValueError, OSError, sqlite3.Error) as exc:
                    return False, (
                        "Portfolio snapshot validation failed: "
                        f"{type(exc).__name__}"
                    )

            if filename == "order_ledger.sqlite3":
                try:
                    OrderLedger(path, initialize=False).list_unresolved()
                except (ValueError, OSError, sqlite3.Error) as exc:
                    return False, (
                        "Order-ledger validation failed: "
                        f"{type(exc).__name__}"
                    )
        except (sqlite3.Error, OSError, ValueError) as exc:
            return False, f"Cannot validate {filename}: {type(exc).__name__}"

    return True, "Existing recovery databases and schemas found"



def _notify_startup(event_notifier, event_name: str, *args: str) -> None:
    """Run a best-effort startup notification without affecting safety logic."""
    try:
        event = getattr(event_notifier, event_name)
        asyncio.run(event(*args))
    except Exception:
        logging.getLogger("crypto_bot.startup").warning(
            "Startup notification failed; continuing safety logic."
        )


def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    logger = logging.getLogger("crypto_bot.startup")

    logger.info("Configuration validated successfully.")
    logger.info("Application environment: %s", settings.app_env)
    logger.info("Configured default mode: %s", settings.default_trading_mode.value)
    logger.info("Live trading enabled flag: %s", settings.live_trading_enabled)

    event_notifier = TelegramEventNotifier(TelegramNotifier(settings))
    _notify_startup(
        event_notifier, "startup_started", settings.default_trading_mode.value
    )

    ready, message = recovery_databases_ready(settings.state_directory)
    if not ready:
        logger.error("Recovery preflight failed: %s", message)
        logger.error("Startup remains non-executing; no orders were submitted.")
        _notify_startup(event_notifier, "startup_blocked", message)
        return

    try:
        coordinator = RecoveryCoordinator(
            portfolio_store=PortfolioStore(
                settings.state_directory / "portfolio.sqlite3"
            ),
            kill_switch=KillSwitch(
                settings.state_directory / "kill_switch.sqlite3",
                initialize=False,
            ),
            order_ledger=OrderLedger(
                settings.state_directory / "order_ledger.sqlite3",
                initialize=False,
            ),
        )
        report = coordinator.recover()
    except Exception:
        logger.exception("Recovery inspection failed; startup remains non-executing.")
        _notify_startup(
            event_notifier,
            "startup_blocked",
            "Recovery inspection raised an unexpected error",
        )
        return

    logger.info("Recovery kill-switch state: %s", report.kill_switch_state)
    logger.info("Unresolved order count: %d", len(report.unresolved_orders))
    if report.portfolio_error:
        logger.error("Portfolio recovery failed: %s", report.portfolio_error)
    if report.ledger_error:
        logger.error("Order-ledger inspection failed: %s", report.ledger_error)
    if not report.recovery_checks_passed:
        logger.error("Recovery checks failed; no trading is authorized.")
    else:
        logger.info(
            "Persisted state inspected successfully. Entry authorization remains disabled."
        )

    _notify_startup(event_notifier, "recovery_completed", report)
    logger.info("Startup is foundation-only; no exchange connection or order execution started.")


if __name__ == "__main__":
    main()
