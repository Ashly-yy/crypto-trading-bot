from decimal import Decimal

import pytest

from crypto_bot.portfolio import Portfolio
from crypto_bot.portfolio.persistence import PortfolioStore


D = Decimal


def test_portfolio_round_trips_exact_decimal_state(tmp_path):
    store = PortfolioStore(tmp_path / "state" / "portfolio.sqlite")
    original = Portfolio(D("1000.00000001"))
    original.buy("BTC", "0.12345678", "123.456789", "0.00001234")
    original.sell("BTC", "0.02345678", "130.123456", "0.00000123")
    original.buy("ETH", "2.00000001", "50.00000001", "0.01")

    store.save(original)
    restored = store.load()

    assert restored.initial_cash == original.initial_cash
    assert restored.quote_currency == original.quote_currency
    assert restored.cash == original.cash
    assert restored.positions == original.positions
    assert restored.realized_pnl == original.realized_pnl
    assert restored.fees_paid == original.fees_paid
    assert restored.snapshot({"BTC": "140", "ETH": "55"}).equity == (
        original.snapshot({"BTC": "140", "ETH": "55"}).equity
    )


def test_save_replaces_snapshot_and_survives_another_load(tmp_path):
    store = PortfolioStore(tmp_path / "portfolio.sqlite")
    store.save(Portfolio(D("1000")))
    updated = store.load()
    updated.buy("BTC", "1", "100", "1")
    store.save(updated)

    assert store.load().cash == D("899")
    assert store.load().positions["BTC"].quantity == D("1")


def test_missing_database_fails_instead_of_creating_empty_portfolio(tmp_path):
    store = PortfolioStore(tmp_path / "missing.sqlite")

    with pytest.raises(FileNotFoundError):
        store.load()

    assert not (tmp_path / "missing.sqlite").exists()


def test_existing_database_without_snapshot_fails_closed(tmp_path):
    store = PortfolioStore(tmp_path / "empty.sqlite")
    store.database_path.touch()

    with pytest.raises(ValueError, match="schema|snapshot"):
        store.load()


def test_modified_snapshot_fails_checksum_validation(tmp_path):
    import sqlite3

    store = PortfolioStore(tmp_path / "portfolio.sqlite")
    store.save(Portfolio(D("1000")))

    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE portfolio_snapshot SET payload = ? WHERE singleton = 1",
            ('{"schema_version":1}',),
        )

    with pytest.raises(ValueError, match="checksum"):
        store.load()


def test_invalid_saved_schema_is_rejected(tmp_path):
    import hashlib
    import sqlite3

    store = PortfolioStore(tmp_path / "portfolio.sqlite")
    store.save(Portfolio(D("1000")))
    bad_payload = '{"schema_version":1}'
    checksum = hashlib.sha256(bad_payload.encode("utf-8")).hexdigest()

    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE portfolio_snapshot SET payload = ?, checksum = ? WHERE singleton = 1",
            (bad_payload, checksum),
        )

    with pytest.raises(ValueError, match="schema"):
        store.load()


def test_restoration_does_not_enable_or_execute_trading(tmp_path):
    store = PortfolioStore(tmp_path / "portfolio.sqlite")
    portfolio = Portfolio(D("1000"))
    portfolio.buy("BTC", "1", "100")
    store.save(portfolio)

    restored = store.load()

    assert restored.cash == D("900")
    assert restored.positions["BTC"].quantity == D("1")
    # Portfolio restoration exposes no order-submission or trading-resume operation.

def test_inconsistent_accounting_snapshot_is_rejected(tmp_path):
    import hashlib
    import json
    import sqlite3

    store = PortfolioStore(tmp_path / "portfolio.sqlite")
    store.save(Portfolio(D("1000")))

    with sqlite3.connect(store.database_path) as connection:
        row = connection.execute(
            "SELECT payload FROM portfolio_snapshot WHERE singleton = 1"
        ).fetchone()
        data = json.loads(row[0])
        data["cash"] = "999"
        payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
        checksum = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        connection.execute(
            "UPDATE portfolio_snapshot SET payload = ?, checksum = ? "
            "WHERE singleton = 1",
            (payload, checksum),
        )

    with pytest.raises(ValueError, match="accounting|inconsistent"):
        store.load()
