"""Best-effort Telegram notifications for startup and recovery events."""

import logging

from crypto_bot.recovery_coordinator import RecoveryReport
from crypto_bot.telegram.notifier import TelegramNotifier

logger = logging.getLogger(__name__)


class TelegramEventNotifier:
    """Format lifecycle alerts without controlling trading or recovery."""

    def __init__(self, notifier: TelegramNotifier) -> None:
        self._notifier = notifier

    async def startup_started(self, mode: str) -> bool:
        """Notify that application startup inspection has begun."""
        return await self._send(
            f"Crypto bot startup inspection started.\nConfigured mode: {mode}\n"
            "Trading authorization: DISABLED"
        )

    async def startup_blocked(self, reason: str) -> bool:
        """Notify that startup preflight blocked further inspection."""
        safe_reason = reason.strip() if isinstance(reason, str) else ""
        if not safe_reason:
            safe_reason = "Unspecified preflight failure"

        return await self._send(
            "Crypto bot startup blocked.\n"
            f"Reason: {safe_reason}\n"
            "Trading authorization: DISABLED"
        )

    async def recovery_completed(self, report: RecoveryReport) -> bool:
        """Notify the outcome of read-only recovery inspection."""
        if report.recovery_checks_passed:
            outcome = "PASSED"
        else:
            outcome = "FAILED"

        message = (
            "Crypto bot recovery inspection completed.\n"
            f"Checks: {outcome}\n"
            f"Kill switch: {report.kill_switch_state}\n"
            f"Unresolved orders: {len(report.unresolved_orders)}\n"
            f"Portfolio error present: {report.portfolio_error is not None}\n"
            f"Ledger error present: {report.ledger_error is not None}\n"
            "Trading authorization: DISABLED"
        )
        return await self._send(message)

    async def _send(self, message: str) -> bool:
        """Never let notification errors interrupt application safety logic."""
        try:
            return await self._notifier.send_message(message)
        except Exception:
            logger.exception(
                "Telegram event notification raised an unexpected error."
            )
            return False
