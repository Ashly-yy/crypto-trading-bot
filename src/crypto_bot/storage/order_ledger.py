"""Persistent, fail-closed ledger for Binance Spot Testnet order intents."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path


class OrderLedgerError(RuntimeError):
    """Base exception for order-ledger failures."""


class DuplicateClientOrderId(OrderLedgerError):
    """A client order ID has already been recorded."""


class InvalidOrderTransition(OrderLedgerError):
    """An order state transition is not permitted."""


class OrderState(str, Enum):
    INTENT_RECORDED = "INTENT_RECORDED"
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class OrderRecord:
    client_order_id: str
    symbol: str
    side: str
    order_type: str
    quantity: str
    price: str
    state: OrderState
    exchange_order_id: str | None
    rejection_reason: str | None
    created_at: str
    updated_at: str


def _positive_decimal(value: str | Decimal, field: str) -> str:
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{field} must be a valid decimal") from None
    if not number.is_finite() or number <= 0:
        raise ValueError(f"{field} must be finite and greater than zero")
    return format(number, "f")


class OrderLedger:
    """SQLite ledger. Never interprets an uncertain submission as a rejection."""

    def __init__(
        self, database_path: str | Path, *, initialize: bool = True
    ) -> None:
        self.database_path = Path(database_path)
        self._read_only = not initialize

        if not initialize:
            if not self.database_path.is_file():
                raise FileNotFoundError("Order-ledger database does not exist.")
            return

        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS testnet_order_ledger (
                    client_order_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL CHECK (side = 'BUY'),
                    order_type TEXT NOT NULL CHECK (order_type = 'LIMIT'),
                    quantity TEXT NOT NULL,
                    price TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (
                        state IN ('INTENT_RECORDED', 'SUBMISSION_UNKNOWN',
                                  'ACKNOWLEDGED', 'PARTIALLY_FILLED', 'FILLED',
                                  'CANCELED', 'EXPIRED', 'REJECTED')
                    ),
                    exchange_order_id TEXT,
                    rejection_reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        if self._read_only:
            uri = self.database_path.resolve().as_uri() + "?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=5)
        else:
            connection = sqlite3.connect(str(self.database_path), timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    @staticmethod
    def _record(row: sqlite3.Row | None) -> OrderRecord | None:
        if row is None:
            return None
        return OrderRecord(
            client_order_id=row["client_order_id"],
            symbol=row["symbol"],
            side=row["side"],
            order_type=row["order_type"],
            quantity=row["quantity"],
            price=row["price"],
            state=OrderState(row["state"]),
            exchange_order_id=row["exchange_order_id"],
            rejection_reason=row["rejection_reason"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def record_intent(
        self, *, client_order_id: str, symbol: str, side: str,
        order_type: str, quantity: str | Decimal, price: str | Decimal,
        exclusive: bool = False,
    ) -> OrderRecord:
        if not isinstance(client_order_id, str):
            raise ValueError("client_order_id must be a string")
        client_order_id = client_order_id.strip()
        if (not 1 <= len(client_order_id) <= 36
                or not client_order_id.isascii()
                or any(ord(char) < 33 or ord(char) > 126 for char in client_order_id)):
            raise ValueError("client_order_id must be 1-36 printable ASCII characters")
        if (not isinstance(symbol, str) or not symbol
                or not symbol.isascii() or not symbol.isalnum()):
            raise ValueError("symbol must be ASCII alphanumeric")
        if side != "BUY" or order_type != "LIMIT":
            raise ValueError("the Testnet ledger only accepts BUY LIMIT intents")
        if not isinstance(exclusive, bool):
            raise ValueError("exclusive must be a bool")
        quantity_text = _positive_decimal(quantity, "quantity")
        price_text = _positive_decimal(price, "price")
        now = self._now()
        try:
            with self._connect() as connection:
                if exclusive:
                    # Serialize the outstanding-order check and intent insert.
                    connection.execute("BEGIN IMMEDIATE")
                    outstanding = connection.execute("""
                        SELECT client_order_id, state
                        FROM testnet_order_ledger
                        WHERE state NOT IN ('REJECTED', 'FILLED', 'CANCELED', 'EXPIRED')
                        LIMIT 1
                    """).fetchone()
                    if outstanding is not None:
                        raise OrderLedgerError(
                            "another Testnet order is outstanding; "
                            "reconcile it or confirm rejection before submitting"
                        )
                connection.execute("""
                    INSERT INTO testnet_order_ledger
                    (client_order_id, symbol, side, order_type, quantity, price,
                     state, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (client_order_id, symbol, side, order_type, quantity_text,
                      price_text, OrderState.INTENT_RECORDED.value, now, now))
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc).upper() or "PRIMARY KEY" in str(exc).upper():
                raise DuplicateClientOrderId(
                    "client_order_id is already recorded; refusing duplicate intent"
                ) from None
            raise OrderLedgerError("database rejected the order intent") from None
        result = self.get(client_order_id)
        if result is None:
            raise OrderLedgerError("recorded order intent could not be read back")
        return result

    def get(self, client_order_id: str) -> OrderRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM testnet_order_ledger WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone()
        return self._record(row)

    def list_unresolved(self) -> tuple[OrderRecord, ...]:
        """Return intents and uncertain submissions that require investigation."""
        with self._connect() as connection:
            rows = connection.execute("""
                SELECT * FROM testnet_order_ledger
                WHERE state IN (
                    'INTENT_RECORDED', 'SUBMISSION_UNKNOWN',
                    'ACKNOWLEDGED', 'PARTIALLY_FILLED'
                )
                ORDER BY created_at, client_order_id
            """).fetchall()
        return tuple(self._record(row) for row in rows)

    def _transition(
        self, client_order_id: str, target: OrderState, *,
        exchange_order_id: str | None = None,
        rejection_reason: str | None = None,
    ) -> OrderRecord:
        allowed = {
            OrderState.INTENT_RECORDED: {
                OrderState.SUBMISSION_UNKNOWN, OrderState.ACKNOWLEDGED,
                OrderState.PARTIALLY_FILLED, OrderState.FILLED,
                OrderState.CANCELED, OrderState.EXPIRED, OrderState.REJECTED,
            },
            OrderState.SUBMISSION_UNKNOWN: {
                OrderState.ACKNOWLEDGED, OrderState.PARTIALLY_FILLED,
                OrderState.FILLED, OrderState.CANCELED, OrderState.EXPIRED,
                OrderState.REJECTED,
            },
            OrderState.ACKNOWLEDGED: {
                OrderState.ACKNOWLEDGED, OrderState.PARTIALLY_FILLED,
                OrderState.FILLED, OrderState.CANCELED,
                OrderState.EXPIRED, OrderState.REJECTED,
            },
            OrderState.PARTIALLY_FILLED: {
                OrderState.PARTIALLY_FILLED, OrderState.FILLED,
                OrderState.CANCELED, OrderState.EXPIRED, OrderState.REJECTED,
            },
            OrderState.FILLED: set(),
            OrderState.CANCELED: set(),
            OrderState.EXPIRED: set(),
            OrderState.REJECTED: set(),
        }
        now = self._now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state, exchange_order_id FROM testnet_order_ledger "
                "WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone()
            if row is None:
                raise OrderLedgerError("order intent does not exist")

            existing_exchange_id = row["exchange_order_id"]
            if (
                exchange_order_id is not None
                and existing_exchange_id is not None
                and str(exchange_order_id) != existing_exchange_id
            ):
                raise OrderLedgerError(
                    "exchange order ID conflicts with the previously recorded ID"
                )

            current = OrderState(row["state"])
            if target not in allowed[current]:
                raise InvalidOrderTransition(
                    f"transition {current.value} -> {target.value} is not permitted"
                )
            connection.execute("""
                UPDATE testnet_order_ledger
                SET state = ?,
                    exchange_order_id = COALESCE(?, exchange_order_id),
                    rejection_reason = COALESCE(?, rejection_reason),
                    updated_at = ?
                WHERE client_order_id = ? AND state = ?
            """, (target.value, exchange_order_id, rejection_reason, now,
                  client_order_id, current.value))
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise InvalidOrderTransition("order state changed concurrently; re-read it")
        result = self.get(client_order_id)
        if result is None:
            raise OrderLedgerError("updated order could not be read back")
        return result

    def mark_submission_unknown(self, client_order_id: str) -> OrderRecord:
        """Use after a submission attempt whose exchange outcome is uncertain."""
        return self._transition(client_order_id, OrderState.SUBMISSION_UNKNOWN)

    def mark_acknowledged(
        self, client_order_id: str, exchange_order_id: str | int,
    ) -> OrderRecord:
        if isinstance(exchange_order_id, bool) or not str(exchange_order_id).strip():
            raise ValueError("exchange_order_id must be non-empty")
        return self._transition(
            client_order_id, OrderState.ACKNOWLEDGED,
            exchange_order_id=str(exchange_order_id),
        )

    def mark_exchange_status(
        self, client_order_id: str, status: str, exchange_order_id: str | int,
    ) -> OrderRecord:
        """Record a validated exchange lifecycle status; never submits an order."""
        status_to_state = {
            'NEW': OrderState.ACKNOWLEDGED,
            'PENDING_CANCEL': OrderState.ACKNOWLEDGED,
            'PARTIALLY_FILLED': OrderState.PARTIALLY_FILLED,
            'FILLED': OrderState.FILLED,
            'CANCELED': OrderState.CANCELED,
            'EXPIRED': OrderState.EXPIRED,
            'EXPIRED_IN_MATCH': OrderState.EXPIRED,
            'REJECTED': OrderState.REJECTED,
        }
        if status not in status_to_state:
            raise ValueError('unsupported exchange order status')
        target = status_to_state[status]
        if target is OrderState.REJECTED:
            return self.mark_rejected(client_order_id, 'exchange status REJECTED')
        return self._transition(
            client_order_id, target, exchange_order_id=str(exchange_order_id),
        )

    def mark_rejected(self, client_order_id: str, reason: str) -> OrderRecord:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("rejection reason must be non-empty")
        return self._transition(
            client_order_id, OrderState.REJECTED,
            rejection_reason=reason.strip()[:500],
        )
