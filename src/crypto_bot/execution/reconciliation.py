"""Read-only-first reconciliation for unresolved Binance Spot Testnet orders."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from crypto_bot.storage.order_ledger import OrderLedger, OrderRecord


@dataclass(frozen=True)
class ReconciliationItem:
    client_order_id: str
    resolved: bool
    reason: str


@dataclass(frozen=True)
class ReconciliationReport:
    checked: int
    resolved: int
    unresolved: int
    items: tuple[ReconciliationItem, ...]


_VALID_STATUSES = {
    "NEW",
    "PARTIALLY_FILLED",
    "FILLED",
    "CANCELED",
    "PENDING_CANCEL",
    "REJECTED",
    "EXPIRED",
    "EXPIRED_IN_MATCH",
}


def _decimal_matches(value: Any, expected: str) -> bool:
    try:
        actual = Decimal(str(value))
        target = Decimal(expected)
    except (InvalidOperation, TypeError, ValueError):
        return False
    return actual.is_finite() and target.is_finite() and actual == target


def _validate_order(record: OrderRecord, response: Any) -> str | None:
    """Return a reason when an exchange lookup cannot safely identify the order."""
    if not isinstance(response, dict):
        return "lookup returned no usable order record"

    order_id = response.get("orderId")
    if isinstance(order_id, bool):
        return "exchange order ID is missing or invalid"
    if isinstance(order_id, int):
        if order_id <= 0:
            return "exchange order ID is missing or invalid"
    elif isinstance(order_id, str):
        if not order_id.isascii() or not order_id.isdigit():
            return "exchange order ID is missing or invalid"
        if int(order_id) <= 0:
            return "exchange order ID is missing or invalid"
    else:
        return "exchange order ID is missing or invalid"

    if (
        record.exchange_order_id is not None
        and str(order_id) != record.exchange_order_id
    ):
        return "exchange order ID does not match stored ID"

    if response.get("clientOrderId") != record.client_order_id:
        return "client order ID does not match"

    symbol = response.get("symbol")
    if not isinstance(symbol, str) or symbol.upper() != record.symbol.upper():
        return "symbol does not match"

    if response.get("side") != record.side:
        return "side does not match"

    if response.get("type") != record.order_type:
        return "order type does not match"

    if not _decimal_matches(response.get("origQty"), record.quantity):
        return "original quantity does not match"

    if not _decimal_matches(response.get("price"), record.price):
        return "price does not match"

    if response.get("status") not in _VALID_STATUSES:
        return "exchange order status is missing or unrecognized"

    return None


async def reconcile_unresolved(
    adapter: Any,
    ledger: OrderLedger,
) -> ReconciliationReport:
    """Look up unresolved orders only; never submit, cancel, or replace orders.

    A missing order, lookup exception, malformed response, or ledger error leaves
    the record unresolved. This function does not resume trading automatically.
    """
    items: list[ReconciliationItem] = []

    for record in ledger.list_unresolved():
        try:
            response = await adapter.get_order(
                record.symbol,
                client_order_id=record.client_order_id,
            )
        except Exception as exc:
            items.append(ReconciliationItem(
                record.client_order_id,
                False,
                f"order lookup failed ({type(exc).__name__})",
            ))
            continue

        reason = _validate_order(record, response)
        if reason is not None:
            items.append(ReconciliationItem(
                record.client_order_id, False, reason
            ))
            continue

        is_terminal = response["status"] in {
            "FILLED", "CANCELED", "EXPIRED", "EXPIRED_IN_MATCH", "REJECTED"
        }
        if is_terminal:
            # Order status is not fill accounting. Keep the ledger unresolved until
            # individual fills, commissions, and resulting balances are reconciled.
            items.append(ReconciliationItem(
                record.client_order_id,
                False,
                f"terminal status {response['status']} found; fill/fee accounting is not implemented, "
                "so the order remains unresolved and new entries stay blocked",
            ))
            continue

        try:
            ledger.mark_exchange_status(
                record.client_order_id,
                response['status'],
                response['orderId'],
            )
        except Exception as exc:
            items.append(ReconciliationItem(
                record.client_order_id,
                False,
                f"ledger update failed ({type(exc).__name__})",
            ))
            continue

        items.append(ReconciliationItem(
            record.client_order_id,
            False,
            f"matching exchange order found; status recorded as {response['status']}; "
            "order remains unresolved until fill and balance accounting is complete",
        ))

    resolved = sum(item.resolved for item in items)
    return ReconciliationReport(
        checked=len(items),
        resolved=resolved,
        unresolved=len(items) - resolved,
        items=tuple(items),
    )
