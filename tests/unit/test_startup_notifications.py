from pathlib import Path
from types import SimpleNamespace

import crypto_bot.main as startup


class FakeEventNotifier:
    def __init__(self, calls):
        self.calls = calls

    async def startup_started(self, mode):
        self.calls.append(("startup_started", mode))
        return True

    async def startup_blocked(self, reason):
        self.calls.append(("startup_blocked", reason))
        return True

    async def recovery_completed(self, report):
        self.calls.append(("recovery_completed", report))
        return True


def setup_settings(monkeypatch, tmp_path):
    settings = SimpleNamespace(
        app_env="test",
        default_trading_mode=SimpleNamespace(value="BACKTEST"),
        live_trading_enabled=False,
        state_directory=tmp_path,
    )
    monkeypatch.setattr(startup, "get_settings", lambda: settings)
    monkeypatch.setattr(startup, "configure_logging", lambda settings: None)
    return settings


def setup_events(monkeypatch, calls):
    monkeypatch.setattr(startup, "TelegramNotifier", lambda settings: object())
    monkeypatch.setattr(
        startup,
        "TelegramEventNotifier",
        lambda notifier: FakeEventNotifier(calls),
    )


def test_main_notifies_startup_and_blocked_preflight(monkeypatch, tmp_path):
    calls = []
    setup_settings(monkeypatch, tmp_path)
    setup_events(monkeypatch, calls)
    monkeypatch.setattr(
        startup,
        "recovery_databases_ready",
        lambda directory: (False, "Required recovery database is missing"),
    )

    startup.main()

    assert calls == [
        ("startup_started", "BACKTEST"),
        ("startup_blocked", "Required recovery database is missing"),
    ]


def test_main_notifies_after_recovery_inspection(monkeypatch, tmp_path):
    calls = []
    setup_settings(monkeypatch, tmp_path)
    setup_events(monkeypatch, calls)
    monkeypatch.setattr(
        startup,
        "recovery_databases_ready",
        lambda directory: (True, "Existing recovery databases and schemas found"),
    )

    monkeypatch.setattr(startup, "PortfolioStore", lambda path: object())
    monkeypatch.setattr(startup, "KillSwitch", lambda *args, **kwargs: object())
    monkeypatch.setattr(startup, "OrderLedger", lambda *args, **kwargs: object())

    report = SimpleNamespace(
        kill_switch_state="STOPPED",
        unresolved_orders=(),
        portfolio_error=None,
        ledger_error=None,
        recovery_checks_passed=True,
        entries_authorized=False,
    )
    monkeypatch.setattr(
        startup,
        "RecoveryCoordinator",
        lambda **kwargs: SimpleNamespace(recover=lambda: report),
    )

    startup.main()

    assert calls[0] == ("startup_started", "BACKTEST")
    assert calls[1] == ("recovery_completed", report)
    assert report.entries_authorized is False


def test_notification_exception_does_not_escape_helper(caplog):
    class BrokenNotifier:
        async def startup_started(self, mode):
            raise RuntimeError("simulated notification failure")

    startup._notify_startup(BrokenNotifier(), "startup_started", "BACKTEST")

    assert "continuing safety logic" in caplog.text


def test_main_notifies_unexpected_recovery_failure(monkeypatch, tmp_path):
    calls = []
    setup_settings(monkeypatch, tmp_path)
    setup_events(monkeypatch, calls)
    monkeypatch.setattr(
        startup,
        "recovery_databases_ready",
        lambda directory: (True, "Existing recovery databases and schemas found"),
    )
    monkeypatch.setattr(startup, "PortfolioStore", lambda path: object())
    monkeypatch.setattr(startup, "KillSwitch", lambda *args, **kwargs: object())
    monkeypatch.setattr(startup, "OrderLedger", lambda *args, **kwargs: object())

    def fail_recovery():
        raise RuntimeError("private internal exception detail")

    monkeypatch.setattr(
        startup,
        "RecoveryCoordinator",
        lambda **kwargs: SimpleNamespace(recover=fail_recovery),
    )

    startup.main()

    assert calls[0] == ("startup_started", "BACKTEST")
    assert calls[1] == (
        "startup_blocked",
        "Recovery inspection raised an unexpected error",
    )
