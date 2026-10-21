"""SQLite persistence and validated restoration for portfolio accounting."""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from crypto_bot.portfolio.accounting import Portfolio, Position


class PortfolioStore:
    """Persist and restore a portfolio without executing or authorizing trades.

    A missing snapshot is an error during load; it never creates a fresh
    portfolio implicitly. Saved values are serialized as decimal strings.
    """

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def save(self, portfolio: Portfolio) -> None:
        """Atomically replace the saved portfolio snapshot."""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._encode(portfolio)
        checksum = hashlib.sha256(payload.encode("utf-8")).hexdigest()

        with sqlite3.connect(self.database_path, timeout=5) as connection:
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS portfolio_snapshot (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    payload TEXT NOT NULL,
                    checksum TEXT NOT NULL
                )
                """
            )
            with connection:
                connection.execute(
                    """
                    INSERT INTO portfolio_snapshot (singleton, payload, checksum)
                    VALUES (1, ?, ?)
                    ON CONFLICT(singleton) DO UPDATE SET
                        payload = excluded.payload,
                        checksum = excluded.checksum
                    """,
                    (payload, checksum),
                )

    def load(self) -> Portfolio:
        """Restore a validated snapshot; fail rather than reset on any problem."""
        if not self.database_path.is_file():
            raise FileNotFoundError("No saved portfolio database exists.")

        uri = self.database_path.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=5) as connection:
            connection.execute("PRAGMA busy_timeout = 5000")
            try:
                row = connection.execute(
                    "SELECT payload, checksum FROM portfolio_snapshot WHERE singleton = 1"
                ).fetchone()
            except sqlite3.DatabaseError as exc:
                raise ValueError("Portfolio persistence schema is missing or unreadable.") from exc

        if row is None:
            raise ValueError("No saved portfolio snapshot exists.")

        payload, expected_checksum = row
        if not isinstance(payload, str) or not isinstance(expected_checksum, str):
            raise ValueError("Saved portfolio snapshot has invalid field types.")

        actual_checksum = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(actual_checksum, expected_checksum):
            raise ValueError("Saved portfolio checksum mismatch; refusing recovery.")

        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError("Saved portfolio is not valid JSON.") from exc

        return self._decode(data)

    @staticmethod
    def _encode(portfolio: Portfolio) -> str:
        positions: dict[str, dict[str, str]] = {}
        for asset, position in sorted(portfolio.positions.items()):
            if asset != position.asset:
                raise ValueError("Portfolio position key does not match its asset.")
            positions[asset] = {
                "quantity": str(position.quantity),
                "cost_basis": str(position.cost_basis),
            }

        data = {
            "schema_version": 1,
            "initial_cash": str(portfolio.initial_cash),
            "quote_currency": portfolio.quote_currency,
            "cash": str(portfolio.cash),
            "positions": positions,
            "realized_pnl": str(portfolio.realized_pnl),
            "fees_paid": str(portfolio.fees_paid),
        }
        return json.dumps(data, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _decimal(value: Any, field: str) -> Decimal:
        if not isinstance(value, str):
            raise ValueError(f"Saved {field} must be a decimal string.")
        try:
            result = Decimal(value)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"Saved {field} is not a valid decimal.") from exc
        if not result.is_finite():
            raise ValueError(f"Saved {field} must be finite.")
        return result

    @classmethod
    def _decode(cls, data: Any) -> Portfolio:
        expected = {
            "schema_version", "initial_cash", "quote_currency", "cash",
            "positions", "realized_pnl", "fees_paid",
        }
        if not isinstance(data, dict) or set(data) != expected:
            raise ValueError("Saved portfolio has an invalid schema.")
        if type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise ValueError("Unsupported portfolio snapshot version.")
        if not isinstance(data["quote_currency"], str):
            raise ValueError("Saved quote currency must be a string.")
        if not isinstance(data["positions"], dict):
            raise ValueError("Saved positions must be an object.")

        initial_cash = cls._decimal(data["initial_cash"], "initial_cash")
        cash = cls._decimal(data["cash"], "cash")
        realized_pnl = cls._decimal(data["realized_pnl"], "realized_pnl")
        fees_paid = cls._decimal(data["fees_paid"], "fees_paid")
        if initial_cash < 0 or cash < 0 or fees_paid < 0:
            raise ValueError("Saved cash and fees cannot be negative.")

        portfolio = Portfolio(initial_cash, quote_currency=data["quote_currency"])
        positions: dict[str, Position] = {}
        for asset, values in data["positions"].items():
            if not isinstance(asset, str) or not isinstance(values, dict):
                raise ValueError("Saved position has an invalid structure.")
            if set(values) != {"quantity", "cost_basis"}:
                raise ValueError("Saved position has unexpected or missing fields.")
            position = Position(
                asset,
                cls._decimal(values["quantity"], f"{asset}.quantity"),
                cls._decimal(values["cost_basis"], f"{asset}.cost_basis"),
            )
            if position.asset != asset:
                raise ValueError("Saved position key does not match its asset.")
            positions[asset] = position

        total_cost_basis = sum(
            (position.cost_basis for position in positions.values()),
            Decimal("0"),
        )
        accounted_value = cash + total_cost_basis
        expected_value = initial_cash + realized_pnl

        if accounted_value != expected_value:
            raise ValueError(
                "Saved portfolio accounting is inconsistent: "
                "cash plus open cost basis must equal initial cash "
                "plus realized P&L."
            )

        portfolio.cash = cash
        portfolio._positions = positions
        portfolio.realized_pnl = realized_pnl
        portfolio.fees_paid = fees_paid
        return portfolio
