import asyncio

import pytest

from crypto_bot.execution.reconciliation import reconcile_unresolved
from crypto_bot.storage.order_ledger import OrderLedger, OrderState


class FakeLookupAdapter:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.lookups = []
        self.submissions = 0

    async def get_order(self, symbol, order_id=None, client_order_id=None):
        self.lookups.append((symbol, order_id, client_order_id))
        if self.error is not None:
            raise self.error
        return self.response

    async def submit_order(self, *args, **kwargs):
        self.submissions += 1
        raise AssertionError("Reconciliation must never submit orders")


def make_ledger(tmp_path, state=OrderState.INTENT_RECORDED):
    ledger = OrderLedger(tmp_path / "reconciliation.sqlite3")
    ledger.record_intent(
        client_order_id="reconcile-001",
        symbol="BTCUSDT",
        side="BUY",
        order_type="LIMIT",
        quantity="0.001",
        price="50000",
    )
    if state is OrderState.SUBMISSION_UNKNOWN:
        ledger.mark_submission_unknown("reconcile-001")
    return ledger


def valid_response():
    return {
        "orderId": 12345,
        "clientOrderId": "reconcile-001",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "type": "LIMIT",
        "origQty": "0.00100000",
        "price": "50000.00000000",
        "status": "NEW",
    }


@pytest.mark.parametrize(
    "initial_state",
    [OrderState.INTENT_RECORDED, OrderState.SUBMISSION_UNKNOWN],
)
def test_matching_exchange_order_resolves_unresolved_record(
    tmp_path, initial_state
):
    ledger = make_ledger(tmp_path, initial_state)
    adapter = FakeLookupAdapter(response=valid_response())

    report = asyncio.run(reconcile_unresolved(adapter, ledger))

    assert (report.checked, report.resolved, report.unresolved) == (1, 0, 1)
    assert ledger.get("reconcile-001").state is OrderState.ACKNOWLEDGED
    assert ledger.get("reconcile-001").exchange_order_id == "12345"
    assert adapter.lookups == [("BTCUSDT", None, "reconcile-001")]
    assert adapter.submissions == 0


def test_missing_order_response_stays_unresolved(tmp_path):
    ledger = make_ledger(tmp_path)
    adapter = FakeLookupAdapter(response=None)

    report = asyncio.run(reconcile_unresolved(adapter, ledger))

    assert report.resolved == 0
    assert report.unresolved == 1
    assert ledger.get("reconcile-001").state is OrderState.INTENT_RECORDED
    assert adapter.submissions == 0


def test_lookup_exception_stays_unresolved(tmp_path):
    ledger = make_ledger(tmp_path, OrderState.SUBMISSION_UNKNOWN)
    adapter = FakeLookupAdapter(error=TimeoutError("simulated timeout"))

    report = asyncio.run(reconcile_unresolved(adapter, ledger))

    assert report.unresolved == 1
    assert ledger.get("reconcile-001").state is OrderState.SUBMISSION_UNKNOWN
    assert adapter.submissions == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("clientOrderId", "different-id"),
        ("symbol", "ETHUSDT"),
        ("side", "SELL"),
        ("type", "MARKET"),
        ("origQty", "0.002"),
        ("price", "51000"),
        ("status", "UNKNOWN"),
    ],
)
def test_inconsistent_exchange_record_stays_unresolved(
    tmp_path, field, value
):
    ledger = make_ledger(tmp_path)
    response = valid_response()
    response[field] = value
    adapter = FakeLookupAdapter(response=response)

    report = asyncio.run(reconcile_unresolved(adapter, ledger))

    assert report.unresolved == 1
    assert ledger.get("reconcile-001").state is OrderState.INTENT_RECORDED
    assert adapter.submissions == 0


def test_acknowledged_order_is_checked_again(tmp_path):
    ledger = make_ledger(tmp_path)
    ledger.mark_acknowledged("reconcile-001", 12345)
    adapter = FakeLookupAdapter(response=valid_response())

    report = asyncio.run(reconcile_unresolved(adapter, ledger))

    assert (report.checked, report.resolved, report.unresolved) == (1, 0, 1)
    assert ledger.get("reconcile-001").state is OrderState.ACKNOWLEDGED
    assert len(adapter.lookups) == 1
    assert adapter.submissions == 0

@pytest.mark.parametrize(
    "order_id",
    [None, "", "abc", "12x", "0", "-1", 0, -1, True, 12.5, [], {}],
)
def test_malformed_exchange_order_id_stays_unresolved(
    tmp_path, order_id
):
    ledger = make_ledger(tmp_path)
    response = valid_response()
    response["orderId"] = order_id
    adapter = FakeLookupAdapter(response=response)

    report = asyncio.run(reconcile_unresolved(adapter, ledger))

    assert report.unresolved == 1
    assert ledger.get("reconcile-001").state is OrderState.INTENT_RECORDED
    assert adapter.submissions == 0


def test_repeated_reconciliation_checks_acknowledged_order(tmp_path):
    ledger = make_ledger(tmp_path)
    adapter = FakeLookupAdapter(response=valid_response())

    first = asyncio.run(reconcile_unresolved(adapter, ledger))
    second = asyncio.run(reconcile_unresolved(adapter, ledger))

    assert first.resolved == 0
    assert second.checked == 1
    assert second.resolved == 0
    assert ledger.get("reconcile-001").state is OrderState.ACKNOWLEDGED
    assert len(adapter.lookups) == 2
    assert adapter.submissions == 0


@pytest.mark.parametrize(
    ("status", "expected_state"),
    [
        ("FILLED", OrderState.FILLED),
        ("CANCELED", OrderState.CANCELED),
        ("EXPIRED", OrderState.EXPIRED),
        ("EXPIRED_IN_MATCH", OrderState.EXPIRED),
        ("REJECTED", OrderState.REJECTED),
    ],
)
def test_terminal_exchange_status_resolves_order(tmp_path, status, expected_state):
    ledger = make_ledger(tmp_path)
    response = valid_response()
    response["status"] = status
    adapter = FakeLookupAdapter(response=response)

    report = asyncio.run(reconcile_unresolved(adapter, ledger))

    assert (report.checked, report.resolved, report.unresolved) == (1, 0, 1)
    # Without durable fill/fee accounting, even terminal order status cannot
    # release the outstanding-order lock. The record remains unresolved.
    assert ledger.get("reconcile-001").state is OrderState.INTENT_RECORDED
    assert "fill/fee accounting is not implemented" in report.items[0].reason
    assert adapter.submissions == 0

def test_conflicting_stored_exchange_id_stays_unresolved(tmp_path):
    ledger = make_ledger(tmp_path)
    ledger.mark_acknowledged("reconcile-001", 99999)
    adapter = FakeLookupAdapter(response=valid_response())

    report = asyncio.run(reconcile_unresolved(adapter, ledger))

    assert report.unresolved == 1
    assert "exchange order ID" in report.items[0].reason
    stored = ledger.get("reconcile-001")
    assert stored.exchange_order_id == "99999"
    assert stored.state is OrderState.ACKNOWLEDGED
    assert adapter.submissions == 0
