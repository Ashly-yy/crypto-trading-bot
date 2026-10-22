from decimal import Decimal
from types import SimpleNamespace

from fastapi.testclient import TestClient

from crypto_bot.api.app import create_app
from crypto_bot.config.settings import Settings, TradingMode
from crypto_bot.portfolio.accounting import Portfolio, Position


def make_settings(tmp_path):
    return Settings(
        state_directory=tmp_path,
        default_trading_mode=TradingMode.BACKTEST,
        live_trading_enabled=False,
    )


def make_report():
    portfolio = Portfolio(Decimal("10000"))
    portfolio.buy("BTC", "0.1", "50000")
    return SimpleNamespace(
        portfolio=portfolio,
        kill_switch_state="STOPPED",
        unresolved_orders=(),
        portfolio_error=None,
        ledger_error=None,
        recovery_checks_passed=True,
        entries_authorized=False,
    )


def test_health_reports_api_process_only(tmp_path):
    app = create_app(make_settings(tmp_path))
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "scope": "api_process_only",
    }


def test_status_never_authorizes_entries_or_live_trading(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(
        settings,
        recovery_provider=lambda _: (True, "test recovery", make_report()),
    )

    response = TestClient(app).get("/api/v1/status")
    data = response.json()

    assert response.status_code == 200
    assert data["configured_mode"] == "BACKTEST"
    assert data["live_trading_enabled"] is False
    assert data["recovery_checks_passed"] is True
    assert data["kill_switch_state"] == "STOPPED"
    assert data["entries_authorized"] is False


def test_status_fails_closed_when_preflight_fails(tmp_path):
    app = create_app(
        make_settings(tmp_path),
        recovery_provider=lambda _: (False, "required database missing", None),
    )

    response = TestClient(app).get("/api/v1/status")
    data = response.json()

    assert response.status_code == 200
    assert data["recovery_preflight_passed"] is False
    assert data["recovery_checks_passed"] is False
    assert data["portfolio_available"] is False
    assert data["entries_authorized"] is False


def test_portfolio_returns_saved_values_without_market_valuation(tmp_path):
    app = create_app(
        make_settings(tmp_path),
        recovery_provider=lambda _: (True, "ok", make_report()),
    )

    response = TestClient(app).get("/api/v1/portfolio")
    data = response.json()

    assert response.status_code == 200
    assert data["quote_currency"] == "USDT"
    assert data["cash"] == "5000.0"
    assert data["positions"][0]["asset"] == "BTC"
    assert data["valuation"]["available"] is False


def test_portfolio_returns_503_if_recovery_is_unavailable(tmp_path):
    app = create_app(
        make_settings(tmp_path),
        recovery_provider=lambda _: (False, "missing state", None),
    )

    response = TestClient(app).get("/api/v1/portfolio")

    assert response.status_code == 503


def test_unresolved_orders_prevent_recovery_success(tmp_path):
    report = make_report()
    report.unresolved_orders = (object(),)
    report.recovery_checks_passed = False

    app = create_app(
        make_settings(tmp_path),
        recovery_provider=lambda _: (True, "ok", report),
    )

    data = TestClient(app).get("/api/v1/status").json()

    assert data["unresolved_order_count"] == 1
    assert data["recovery_checks_passed"] is False
    assert data["entries_authorized"] is False


def test_portfolio_returns_503_when_recovery_checks_fail(tmp_path):
    report = make_report()
    report.recovery_checks_passed = False

    app = create_app(
        make_settings(tmp_path),
        recovery_provider=lambda _: (True, "recovery checks failed", report),
    )

    response = TestClient(app).get("/api/v1/portfolio")

    assert response.status_code == 503


def test_status_does_not_report_preflight_success_when_recovery_fails(tmp_path):
    report = make_report()
    report.recovery_checks_passed = False

    app = create_app(
        make_settings(tmp_path),
        recovery_provider=lambda _: (True, "inspection completed with unresolved state", report),
    )

    data = TestClient(app).get("/api/v1/status").json()

    assert data["recovery_checks_passed"] is False
    assert data["entries_authorized"] is False
