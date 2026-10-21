"""Local-only tests for the persistent Testnet order ledger."""
import pytest

from crypto_bot.storage.order_ledger import (
    DuplicateClientOrderId,
    InvalidOrderTransition,
    OrderLedger,
    OrderState,
)


def make_ledger(tmp_path):
    return OrderLedger(tmp_path / "orders.sqlite3")


def record_intent(ledger, client_id="test-order-001"):
    return ledger.record_intent(
        client_order_id=client_id,
        symbol="BTCUSDT",
        side="BUY",
        order_type="LIMIT",
        quantity="0.00100",
        price="60000.00",
    )


def test_intent_persists_after_reopening_database(tmp_path):
    path = tmp_path / "orders.sqlite3"
    first = OrderLedger(path)
    created = record_intent(first)
    reopened = OrderLedger(path)
    loaded = reopened.get(created.client_order_id)
    assert loaded == created
    assert loaded.state is OrderState.INTENT_RECORDED
    assert loaded.exchange_order_id is None


def test_duplicate_client_id_is_rejected_even_after_reopening(tmp_path):
    ledger = make_ledger(tmp_path)
    record_intent(ledger)
    reopened = OrderLedger(tmp_path / "orders.sqlite3")
    with pytest.raises(DuplicateClientOrderId):
        record_intent(reopened)


def test_uncertain_submission_remains_unresolved(tmp_path):
    ledger = make_ledger(tmp_path)
    record_intent(ledger)
    record = ledger.mark_submission_unknown("test-order-001")
    assert record.state is OrderState.SUBMISSION_UNKNOWN
    assert [r.client_order_id for r in ledger.list_unresolved()] == ["test-order-001"]


def test_acknowledged_order_records_exchange_id_and_remains_reconcilable(tmp_path):
    ledger = make_ledger(tmp_path)
    record_intent(ledger)
    record = ledger.mark_acknowledged("test-order-001", 123456)
    assert record.state is OrderState.ACKNOWLEDGED
    assert record.exchange_order_id == "123456"
    assert [r.client_order_id for r in ledger.list_unresolved()] == [
        "test-order-001"
    ]


def test_rejected_order_records_reason_and_is_resolved(tmp_path):
    ledger = make_ledger(tmp_path)
    record_intent(ledger)
    record = ledger.mark_rejected("test-order-001", "exchange rejected")
    assert record.state is OrderState.REJECTED
    assert record.rejection_reason == "exchange rejected"
    assert ledger.list_unresolved() == ()


@pytest.mark.parametrize("quantity,price", [
    ("0", "1"), ("-1", "1"), ("NaN", "1"), ("1", "Infinity"), ("1", "0"),
])
def test_non_positive_or_non_finite_amounts_are_rejected(tmp_path, quantity, price):
    ledger = make_ledger(tmp_path)
    with pytest.raises(ValueError):
        ledger.record_intent(
            client_order_id="invalid-amount",
            symbol="BTCUSDT",
            side="BUY",
            order_type="LIMIT",
            quantity=quantity,
            price=price,
        )


def test_only_buy_limit_intents_are_supported(tmp_path):
    ledger = make_ledger(tmp_path)
    with pytest.raises(ValueError):
        ledger.record_intent(
            client_order_id="sell-order",
            symbol="BTCUSDT",
            side="SELL",
            order_type="LIMIT",
            quantity="1",
            price="100",
        )


def test_terminal_state_cannot_be_rewritten(tmp_path):
    ledger = make_ledger(tmp_path)
    record_intent(ledger)
    ledger.mark_acknowledged("test-order-001", "123")
    with pytest.raises(InvalidOrderTransition):
        ledger.mark_submission_unknown("test-order-001")


def test_missing_intent_cannot_be_updated(tmp_path):
    ledger = make_ledger(tmp_path)
    with pytest.raises(Exception, match="does not exist"):
        ledger.mark_submission_unknown("missing-order")

def test_exclusive_intent_blocks_an_existing_acknowledged_order(tmp_path):
    ledger = make_ledger(tmp_path)
    record_intent(ledger)
    ledger.mark_acknowledged("test-order-001", "123456")

    with pytest.raises(Exception, match="another Testnet order is outstanding"):
        ledger.record_intent(
            client_order_id="test-order-002",
            symbol="ETHUSDT",
            side="BUY",
            order_type="LIMIT",
            quantity="1",
            price="100",
            exclusive=True,
        )


def test_exclusive_intent_allows_new_order_after_confirmed_rejection(tmp_path):
    ledger = make_ledger(tmp_path)
    record_intent(ledger)
    ledger.mark_rejected("test-order-001", "confirmed rejection")

    created = ledger.record_intent(
        client_order_id="test-order-002",
        symbol="ETHUSDT",
        side="BUY",
        order_type="LIMIT",
        quantity="1",
        price="100",
        exclusive=True,
    )
    assert created.client_order_id == "test-order-002"


def test_exclusive_intent_serializes_concurrent_attempts(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from crypto_bot.storage.order_ledger import OrderLedgerError

    database = tmp_path / "orders.sqlite3"
    ledgers = [OrderLedger(database), OrderLedger(database)]
    barrier = Barrier(2)

    def attempt(index):
        barrier.wait(timeout=5)
        return ledgers[index].record_intent(
            client_order_id=f"parallel-order-{index}",
            symbol="BTCUSDT" if index == 0 else "ETHUSDT",
            side="BUY",
            order_type="LIMIT",
            quantity="1",
            price="100",
            exclusive=True,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt, i) for i in range(2)]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result(timeout=10))
            except OrderLedgerError as exc:
                outcomes.append(exc)

    assert sum(not isinstance(item, Exception) for item in outcomes) == 1
    assert sum(isinstance(item, OrderLedgerError) for item in outcomes) == 1
    with OrderLedger(database)._connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM testnet_order_ledger"
        ).fetchone()[0]
    assert count == 1

def test_conflicting_exchange_order_id_is_rejected(tmp_path):
    from crypto_bot.storage.order_ledger import OrderLedgerError

    ledger = make_ledger(tmp_path)
    record_intent(ledger)
    ledger.mark_acknowledged("test-order-001", "123456")

    with pytest.raises(OrderLedgerError, match="exchange order ID"):
        ledger.mark_exchange_status("test-order-001", "NEW", "999999")

    stored = ledger.get("test-order-001")
    assert stored.exchange_order_id == "123456"
    assert stored.state is OrderState.ACKNOWLEDGED


def test_matching_exchange_order_id_can_be_reconciled_again(tmp_path):
    ledger = make_ledger(tmp_path)
    record_intent(ledger)
    ledger.mark_acknowledged("test-order-001", "123456")

    stored = ledger.mark_exchange_status(
        "test-order-001", "PARTIALLY_FILLED", "123456"
    )

    assert stored.exchange_order_id == "123456"
    assert stored.state is OrderState.PARTIALLY_FILLED
