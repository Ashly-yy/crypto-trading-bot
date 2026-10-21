from pathlib import Path
import hashlib
import json
import sqlite3

from crypto_bot.main import recovery_databases_ready


def create_database(path, statements):
    with sqlite3.connect(path) as connection:
        for statement in statements:
            connection.execute(statement)


def create_portfolio_database(path, *, snapshot=True, incompatible=False):
    with sqlite3.connect(path) as connection:
        if incompatible:
            connection.execute(
                "CREATE TABLE portfolio_snapshot (wrong_column TEXT)"
            )
            return

        connection.execute(
            """
            CREATE TABLE portfolio_snapshot (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                payload TEXT NOT NULL,
                checksum TEXT NOT NULL
            )
            """
        )
        if snapshot:
            payload = json.dumps(
                {
                    "schema_version": 1,
                    "initial_cash": "1000",
                    "quote_currency": "USDT",
                    "cash": "1000",
                    "positions": {},
                    "realized_pnl": "0",
                    "fees_paid": "0",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            checksum = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            connection.execute(
                "INSERT INTO portfolio_snapshot VALUES (1, ?, ?)",
                (payload, checksum),
            )


def create_other_databases(directory, *, include_kill_state=True):
    create_database(
        directory / "kill_switch.sqlite3",
        [
            "CREATE TABLE kill_switch_state (singleton_id INTEGER PRIMARY KEY, state TEXT)",
            "CREATE TABLE kill_switch_events (id INTEGER PRIMARY KEY, previous_state TEXT, new_state TEXT, actor TEXT, reason TEXT, created_at TEXT)",
            *(
                ["INSERT INTO kill_switch_state VALUES (1, 'STOPPED')"]
                if include_kill_state
                else []
            ),
        ],
    )
    create_database(
        directory / "order_ledger.sqlite3",
        [
            """
            CREATE TABLE testnet_order_ledger (
                client_order_id TEXT PRIMARY KEY,
                symbol TEXT,
                side TEXT,
                order_type TEXT,
                quantity TEXT,
                price TEXT,
                state TEXT,
                exchange_order_id TEXT,
                rejection_reason TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        ],
    )


def test_preflight_fails_when_state_database_is_missing(tmp_path):
    ready, message = recovery_databases_ready(tmp_path)

    assert ready is False
    assert "missing" in message.lower()
    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []


def test_preflight_accepts_existing_required_schemas(tmp_path):
    create_portfolio_database(tmp_path / "portfolio.sqlite3")
    create_other_databases(tmp_path)

    ready, message = recovery_databases_ready(tmp_path)

    assert ready is True
    assert "found" in message.lower()


def test_preflight_rejects_missing_kill_switch_state(tmp_path):
    create_portfolio_database(tmp_path / "portfolio.sqlite3")
    create_other_databases(tmp_path, include_kill_state=False)

    ready, message = recovery_databases_ready(tmp_path)

    assert ready is False
    assert "state is missing" in message.lower()


def test_preflight_rejects_missing_portfolio_snapshot(tmp_path):
    create_portfolio_database(
        tmp_path / "portfolio.sqlite3", snapshot=False
    )
    create_other_databases(tmp_path)

    ready, message = recovery_databases_ready(tmp_path)

    assert ready is False
    assert "portfolio snapshot validation failed" in message.lower()


def test_preflight_rejects_incompatible_portfolio_schema(tmp_path):
    create_portfolio_database(
        tmp_path / "portfolio.sqlite3", incompatible=True
    )
    create_other_databases(tmp_path)

    ready, message = recovery_databases_ready(tmp_path)

    assert ready is False
    assert "missing required columns" in message.lower()


def test_preflight_rejects_corrupt_sqlite_database(tmp_path):
    (tmp_path / "portfolio.sqlite3").write_bytes(
        b"this is not a valid sqlite database"
    )
    create_other_databases(tmp_path)

    ready, message = recovery_databases_ready(tmp_path)

    assert ready is False
    assert "cannot validate portfolio.sqlite3" in message.lower()


def test_preflight_rejects_missing_order_ledger_columns(tmp_path):
    create_portfolio_database(tmp_path / "portfolio.sqlite3")
    create_database(
        tmp_path / "kill_switch.sqlite3",
        [
            "CREATE TABLE kill_switch_state (singleton_id INTEGER PRIMARY KEY, state TEXT)",
            "CREATE TABLE kill_switch_events (id INTEGER PRIMARY KEY, previous_state TEXT, new_state TEXT, actor TEXT, reason TEXT, created_at TEXT)",
            "INSERT INTO kill_switch_state VALUES (1, 'STOPPED')",
        ],
    )
    create_database(
        tmp_path / "order_ledger.sqlite3",
        [
            """
            CREATE TABLE testnet_order_ledger (
                client_order_id TEXT PRIMARY KEY,
                symbol TEXT,
                side TEXT,
                order_type TEXT,
                quantity TEXT,
                price TEXT,
                state TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        ],
    )

    ready, message = recovery_databases_ready(tmp_path)

    assert ready is False
    assert "missing required columns" in message.lower()
