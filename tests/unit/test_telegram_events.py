"""Tests for best-effort Telegram lifecycle notifications."""

import asyncio

from crypto_bot.recovery_coordinator import RecoveryReport
from crypto_bot.telegram.events import TelegramEventNotifier


class FakeNotifier:
    def __init__(self, result=True, error=None):
        self.result = result
        self.error = error
        self.messages = []

    async def send_message(self, message):
        self.messages.append(message)
        if self.error is not None:
            raise self.error
        return self.result


def make_report(
    *,
    unresolved_orders=(),
    portfolio_error=None,
    ledger_error=None,
    kill_switch_state="STOPPED",
):
    return RecoveryReport(
        portfolio=object() if portfolio_error is None else None,
        kill_switch_state=kill_switch_state,
        unresolved_orders=tuple(unresolved_orders),
        portfolio_error=portfolio_error,
        ledger_error=ledger_error,
    )


def test_startup_started_reports_mode_and_disabled_authorization():
    notifier = FakeNotifier()
    events = TelegramEventNotifier(notifier)

    result = asyncio.run(events.startup_started("BACKTEST"))

    assert result is True
    assert len(notifier.messages) == 1
    assert "BACKTEST" in notifier.messages[0]
    assert "Trading authorization: DISABLED" in notifier.messages[0]


def test_startup_blocked_reports_reason():
    notifier = FakeNotifier()
    events = TelegramEventNotifier(notifier)

    result = asyncio.run(events.startup_blocked("Recovery database missing"))

    assert result is True
    assert "startup blocked" in notifier.messages[0].lower()
    assert "Recovery database missing" in notifier.messages[0]
    assert "Trading authorization: DISABLED" in notifier.messages[0]


def test_startup_blocked_handles_empty_reason():
    notifier = FakeNotifier()
    events = TelegramEventNotifier(notifier)

    asyncio.run(events.startup_blocked("   "))

    assert "Unspecified preflight failure" in notifier.messages[0]


def test_successful_recovery_reports_passed_checks():
    notifier = FakeNotifier()
    events = TelegramEventNotifier(notifier)

    result = asyncio.run(events.recovery_completed(make_report()))

    assert result is True
    message = notifier.messages[0]
    assert "Checks: PASSED" in message
    assert "Kill switch: STOPPED" in message
    assert "Unresolved orders: 0" in message
    assert "Trading authorization: DISABLED" in message


def test_unresolved_orders_report_failed_recovery():
    notifier = FakeNotifier()
    events = TelegramEventNotifier(notifier)

    report = make_report(unresolved_orders=("order-1",))
    result = asyncio.run(events.recovery_completed(report))

    assert result is True
    message = notifier.messages[0]
    assert "Checks: FAILED" in message
    assert "Unresolved orders: 1" in message
    assert "Trading authorization: DISABLED" in message


def test_recovery_errors_are_reported_without_exposing_details():
    notifier = FakeNotifier()
    events = TelegramEventNotifier(notifier)

    report = make_report(
        portfolio_error="sensitive internal detail",
        ledger_error="another internal detail",
        kill_switch_state="EMERGENCY_HALTED",
    )
    result = asyncio.run(events.recovery_completed(report))

    assert result is True
    message = notifier.messages[0]
    assert "Checks: FAILED" in message
    assert "Kill switch: EMERGENCY_HALTED" in message
    assert "Portfolio error present: True" in message
    assert "Ledger error present: True" in message
    assert "sensitive internal detail" not in message
    assert "another internal detail" not in message


def test_delivery_failure_returns_false():
    notifier = FakeNotifier(result=False)
    events = TelegramEventNotifier(notifier)

    assert asyncio.run(events.startup_started("BACKTEST")) is False


def test_unexpected_delivery_exception_does_not_escape():
    notifier = FakeNotifier(error=RuntimeError("simulated transport failure"))
    events = TelegramEventNotifier(notifier)

    assert asyncio.run(events.startup_started("BACKTEST")) is False
