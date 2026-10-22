"""Read-only local API for inspecting persisted trading-bot state."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException

from crypto_bot.config.settings import Settings, get_settings
from crypto_bot.control.kill_switch import KillSwitch
from crypto_bot.main import recovery_databases_ready
from crypto_bot.portfolio.persistence import PortfolioStore
from crypto_bot.recovery_coordinator import RecoveryCoordinator
from crypto_bot.storage.order_ledger import OrderLedger


def _build_recovery(settings: Settings) -> tuple[bool, str, Any | None]:
    """Inspect existing state only. Never initialize missing databases."""
    ready, message = recovery_databases_ready(settings.state_directory)
    if not ready:
        return False, message, None

    directory: Path = settings.state_directory
    try:
        coordinator = RecoveryCoordinator(
            portfolio_store=PortfolioStore(directory / "portfolio.sqlite3"),
            kill_switch=KillSwitch(
                directory / "kill_switch.sqlite3",
                initialize=False,
            ),
            order_ledger=OrderLedger(
                directory / "order_ledger.sqlite3",
                initialize=False,
            ),
        )
        return True, message, coordinator.recover()
    except Exception as exc:
        # Do not expose database paths, credentials, or exception details.
        return False, f"Recovery inspection failed: {type(exc).__name__}", None


def create_app(
    settings: Settings | None = None,
    recovery_provider: Callable[[Settings], tuple[bool, str, Any | None]] | None = None,
) -> FastAPI:
    """Create the API; the provider argument enables isolated tests."""
    configured = settings or get_settings()
    provider = recovery_provider or _build_recovery

    app = FastAPI(
        title="Crypto Trading Bot Local API",
        version="0.1.0",
        description="Read-only status and persisted portfolio inspection.",
    )
    app.state.settings = configured
    app.state.recovery_provider = provider

    @app.get("/health")
    def health() -> dict[str, str]:
        # This only confirms that the HTTP process is responding.
        return {"status": "ok", "scope": "api_process_only"}

    @app.get("/api/v1/status")
    def status() -> dict[str, Any]:
        ok, message, report = provider(configured)
        result: dict[str, Any] = {
            "api_process": "ok",
            "configured_mode": configured.default_trading_mode.value,
            "live_trading_enabled": configured.live_trading_enabled,
            "recovery_preflight_passed": ok,
            "recovery_message": message,
            "recovery_checks_passed": False,
            "kill_switch_state": "UNKNOWN",
            "unresolved_order_count": None,
            "portfolio_available": False,
            "portfolio_error": None,
            "ledger_error": None,
            "entries_authorized": False,
        }

        if report is None:
            result["recovery_message"] = message
            return result

        result.update({
            "recovery_checks_passed": report.recovery_checks_passed,
            "kill_switch_state": report.kill_switch_state,
            "unresolved_order_count": len(report.unresolved_orders),
            "portfolio_available": report.portfolio is not None,
            "portfolio_error": report.portfolio_error,
            "ledger_error": report.ledger_error,
            # Recovery must never authorize entry.
            "entries_authorized": False,
        })
        return result

    @app.get("/api/v1/portfolio")
    def portfolio() -> dict[str, Any]:
        ok, message, report = provider(configured)
        if (
            not ok
            or report is None
            or not report.recovery_checks_passed
            or report.portfolio is None
        ):
            raise HTTPException(
                status_code=503,
                detail="Persisted portfolio is unavailable; inspect the status endpoint.",
            )

        saved = report.portfolio
        return {
            "quote_currency": saved.quote_currency,
            "initial_cash": str(saved.initial_cash),
            "cash": str(saved.cash),
            "realized_pnl": str(saved.realized_pnl),
            "fees_paid": str(saved.fees_paid),
            "positions": [
                {
                    "asset": position.asset,
                    "quantity": str(position.quantity),
                    "cost_basis": str(position.cost_basis),
                    "average_entry_price": str(position.average_entry_price),
                }
                for _, position in sorted(saved.positions.items())
            ],
            "valuation": {
                "available": False,
                "reason": "No live market marks are calculated by this read-only endpoint.",
            },
        }

    return app


app = create_app()
