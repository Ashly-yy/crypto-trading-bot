from decimal import Decimal

from crypto_bot.control.kill_switch import KillSwitch, KillSwitchState
from crypto_bot.portfolio.accounting import Portfolio
from crypto_bot.portfolio.persistence import PortfolioStore
from crypto_bot.recovery_coordinator import RecoveryCoordinator
from crypto_bot.storage.order_ledger import OrderLedger


def make_coordinator(tmp_path):
    portfolio_db = tmp_path / "portfolio.db"
    kill_db = tmp_path / "kill.db"
    ledger_db = tmp_path / "orders.db"

    portfolio = Portfolio(
        initial_cash=Decimal("1000"),
        quote_currency="USDT",
    )
    PortfolioStore(portfolio_db).save(portfolio)

    return (
        RecoveryCoordinator(
            portfolio_store=PortfolioStore(portfolio_db),
            kill_switch=KillSwitch(kill_db),
            order_ledger=OrderLedger(ledger_db),
        ),
        kill_db,
    )


def test_recovery_loads_portfolio_and_never_authorizes_entries(tmp_path):
    coordinator, _ = make_coordinator(tmp_path)

    report = coordinator.recover()

    assert report.portfolio is not None
    assert report.portfolio_error is None
    assert report.ledger_error is None
    assert report.kill_switch_state == KillSwitchState.STOPPED.value
    assert report.unresolved_orders == ()
    assert report.state_inspection_passed
    assert report.recovery_checks_passed
    assert report.entries_authorized is False


def test_missing_portfolio_snapshot_fails_closed(tmp_path):
    coordinator, _ = make_coordinator(tmp_path)
    coordinator._portfolio_store = PortfolioStore(tmp_path / "missing.db")

    report = coordinator.recover()

    assert report.portfolio is None
    assert report.portfolio_error is not None
    assert not report.recovery_checks_passed
    assert report.entries_authorized is False


def test_unresolved_order_is_reported_without_being_changed(tmp_path):
    coordinator, _ = make_coordinator(tmp_path)
    coordinator._order_ledger.record_intent(
        client_order_id="recovery-test-1",
        symbol="BTCUSDT",
        side="BUY",
        order_type="LIMIT",
        quantity="0.001",
        price="50000",
    )

    report = coordinator.recover()

    assert len(report.unresolved_orders) == 1
    assert report.unresolved_orders[0].client_order_id == "recovery-test-1"
    assert report.state_inspection_passed
    assert not report.recovery_checks_passed
    assert report.entries_authorized is False

    persisted = coordinator._order_ledger.get("recovery-test-1")
    assert persisted is not None
    assert persisted.state.value == "INTENT_RECORDED"


def test_recovery_does_not_change_kill_switch_state(tmp_path):
    coordinator, kill_db = make_coordinator(tmp_path)
    switch = KillSwitch(kill_db)
    switch.transition(
        KillSwitchState.ENTRIES_PAUSED,
        actor="test",
        reason="Verify recovery preserves state",
        authorized=True,
    )

    report = coordinator.recover()

    assert report.kill_switch_state == KillSwitchState.ENTRIES_PAUSED.value
    assert KillSwitch(kill_db).state is KillSwitchState.ENTRIES_PAUSED
    assert report.entries_authorized is False

def test_read_only_recovery_constructors_do_not_create_missing_databases(tmp_path):
    kill_db = tmp_path / "missing-kill.sqlite3"
    ledger_db = tmp_path / "missing-ledger.sqlite3"

    import pytest

    with pytest.raises(FileNotFoundError):
        KillSwitch(kill_db, initialize=False)

    with pytest.raises(FileNotFoundError):
        OrderLedger(ledger_db, initialize=False)

    assert not kill_db.exists()
    assert not ledger_db.exists()


def test_read_only_recovery_inspection_does_not_modify_database_files(tmp_path):
    kill_db = tmp_path / "kill.sqlite3"
    ledger_db = tmp_path / "ledger.sqlite3"

    KillSwitch(kill_db)
    OrderLedger(ledger_db)

    kill_bytes_before = kill_db.read_bytes()
    ledger_bytes_before = ledger_db.read_bytes()

    read_only_switch = KillSwitch(kill_db, initialize=False)
    read_only_ledger = OrderLedger(ledger_db, initialize=False)

    assert read_only_switch.state == KillSwitchState.STOPPED
    assert read_only_ledger.list_unresolved() == ()

    assert kill_db.read_bytes() == kill_bytes_before
    assert ledger_db.read_bytes() == ledger_bytes_before
