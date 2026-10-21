"""Risk-gated execution path for Binance Spot Testnet BUY limit orders."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from dataclasses import replace
from uuid import uuid4
from types import SimpleNamespace
from typing import Any, Callable, Mapping

from crypto_bot.control import KillSwitch
from crypto_bot.risk import RiskContext, RiskManager, TradeProposal
from crypto_bot.execution.adapters.binance_spot_testnet import (
    BinanceSpotTestnetAdapter,
    SpotOrderRequest,
)
from crypto_bot.execution._gate_authority import _ORDER_GATE_AUTHORITY
from crypto_bot.storage.order_ledger import OrderLedger
from crypto_bot.execution.exchange_filters import (
    ExchangeFilterValidationError,
    validate_limit_order_against_exchange_info,
)


class TestnetExecutionBlocked(RuntimeError):
    """Raised when a Testnet order fails any execution safety gate."""

    __test__ = False


def _decimal(value: str | Decimal, field: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise TestnetExecutionBlocked(f"{field} is not a valid decimal") from None
    if not result.is_finite():
        raise TestnetExecutionBlocked(f"{field} must be finite")
    return result


class TestnetOrderExecutor:
    """Single guarded route for Testnet BUY limit orders.

    Context and portfolio providers are called immediately before each evaluation.
    `symbol_to_asset` must explicitly map exchange symbols (e.g. BTCUSDT) to the
    asset keys used by the portfolio/risk manager (e.g. BTC). No symbol guessing
    is performed. Only this executor holds the in-process capability accepted by
    the adapter's guarded submit_order method.
    """

    __test__ = False

    def __init__(
        self,
        adapter: BinanceSpotTestnetAdapter,
        risk_manager: RiskManager,
        kill_switch: KillSwitch,
        *,
        portfolio_provider: Callable[[], Any],
        context_provider: Callable[[], RiskContext],
        symbol_to_asset: Mapping[str, str],
        ledger: OrderLedger,
        allow_testnet_submission: bool = False,
    ) -> None:
        if adapter is None or not callable(getattr(adapter, "submit_order", None)):
            raise ValueError("a Testnet adapter is required")
        if not isinstance(risk_manager, RiskManager):
            raise ValueError("a RiskManager is required")
        if not isinstance(kill_switch, KillSwitch):
            raise ValueError("a persistent KillSwitch is required")
        if getattr(risk_manager, "_kill_switch", None) is not kill_switch:
            raise ValueError("executor and RiskManager must share the same kill switch")
        if not callable(portfolio_provider) or not callable(context_provider):
            raise ValueError("portfolio_provider and context_provider must be callable")
        if not isinstance(ledger, OrderLedger):
            raise ValueError("a persistent OrderLedger is required")
        if not symbol_to_asset:
            raise ValueError("an explicit symbol_to_asset mapping is required")

        normalized: dict[str, str] = {}
        for symbol, asset in symbol_to_asset.items():
            if not isinstance(symbol, str) or not symbol.strip().isalnum() or not symbol.isascii():
                raise ValueError("symbol mapping keys must be ASCII alphanumeric exchange symbols")
            if not isinstance(asset, str) or not asset.strip():
                raise ValueError("symbol mapping values must be non-empty asset names")
            key = symbol.strip().upper()
            if key in normalized:
                raise ValueError("duplicate normalized exchange symbol in mapping")
            normalized[key] = asset.strip()
        self._adapter = adapter
        self._risk_manager = risk_manager
        self._kill_switch = kill_switch
        self._portfolio_provider = portfolio_provider
        self._context_provider = context_provider
        self._symbol_to_asset = normalized
        self._ledger = ledger
        if not isinstance(allow_testnet_submission, bool):
            raise ValueError("allow_testnet_submission must be a bool")
        self._allow_testnet_submission = allow_testnet_submission

    async def execute_buy(
        self, order: SpotOrderRequest, proposal: TradeProposal
    ) -> Any:
        """Evaluate the exact proposed BUY LIMIT order, then submit once if approved."""
        if not self._allow_testnet_submission:
            raise TestnetExecutionBlocked(
                "Testnet order submission is disabled by default; explicit reviewed opt-in is required"
            )
        if not isinstance(order, SpotOrderRequest):
            raise TestnetExecutionBlocked("order must be a SpotOrderRequest")
        if not isinstance(proposal, TradeProposal):
            raise TestnetExecutionBlocked("proposal must be a TradeProposal")

        try:
            params = order.to_params()
        except (TypeError, ValueError) as exc:
            raise TestnetExecutionBlocked(f"order validation failed: {exc}") from None

        if params["side"] != "BUY":
            raise TestnetExecutionBlocked("the guarded Testnet path only permits BUY orders")
        if params["type"] != "LIMIT":
            raise TestnetExecutionBlocked("the guarded Testnet path only permits LIMIT orders")
        mapped_asset = self._symbol_to_asset.get(params["symbol"])
        if mapped_asset is None or mapped_asset != proposal.asset:
            raise TestnetExecutionBlocked("order symbol does not match the explicit proposal asset mapping")

        order_quantity = _decimal(params["quantity"], "order quantity")
        order_price = _decimal(params["price"], "order price")
        if order_quantity != proposal.quantity:
            raise TestnetExecutionBlocked("order quantity does not exactly match the risk proposal")
        if order_price != proposal.entry_price:
            raise TestnetExecutionBlocked("order limit price does not exactly match the risk proposal")

        # Exchange metadata must be validated before risk approval can lead to submission.
        try:
            exchange_info = await self._adapter.get_exchange_info(params["symbol"])
            exchange_order = SimpleNamespace(
                symbol=params["symbol"],
                side=params["side"],
                type=params["type"],
                quantity=params["quantity"],
                price=params.get("price"),
            )
            validate_limit_order_against_exchange_info(exchange_order, exchange_info)
        except Exception as exc:
            detail = (
                "exchange filters rejected the order"
                if isinstance(exc, ExchangeFilterValidationError)
                else f"exchange metadata unavailable ({type(exc).__name__})"
            )
            raise TestnetExecutionBlocked(f"{detail}; order not submitted") from None

        # Fail closed if a provider errors, returns the wrong type, or state is unavailable.
        try:
            portfolio = self._portfolio_provider()
            context = self._context_provider()
        except Exception as exc:
            raise TestnetExecutionBlocked(
                f"fresh risk inputs unavailable ({type(exc).__name__}); order not submitted"
            ) from None
        if not isinstance(context, RiskContext):
            raise TestnetExecutionBlocked("context provider did not return RiskContext")

        decision = self._risk_manager.evaluate(proposal, portfolio, context)
        if not decision.approved:
            reasons = "; ".join(decision.reasons) or "unspecified risk rejection"
            raise TestnetExecutionBlocked(f"risk manager rejected order: {reasons}")
        if decision.permitted_quantity != order_quantity:
            raise TestnetExecutionBlocked("risk-approved quantity does not exactly match the order")
        if decision.order_quote != order_quantity * order_price:
            raise TestnetExecutionBlocked("risk-approved notional does not exactly match the order")

        # Re-check the persistent switch as close as possible to network submission.
        try:
            if not self._kill_switch.can_open_entries:
                raise TestnetExecutionBlocked("persistent kill switch blocks order submission")
        except TestnetExecutionBlocked:
            raise
        except Exception as exc:
            raise TestnetExecutionBlocked(
                f"persistent kill switch could not be verified ({type(exc).__name__})"
            ) from None

        # Persist the exact intent before the network submission attempt.
        # A duplicate ID or ledger failure blocks submission.
        client_order_id = order.client_order_id or f"cb{uuid4().hex}"
        try:
            submitted_order = replace(order, client_order_id=client_order_id)
            self._ledger.record_intent(
                client_order_id=client_order_id,
                symbol=params["symbol"],
                side=params["side"],
                order_type=params["type"],
                quantity=params["quantity"],
                price=params["price"],
                exclusive=True,
            )
        except Exception as exc:
            raise TestnetExecutionBlocked(
                f"order intent could not be safely recorded "
                f"({type(exc).__name__}); order not submitted"
            ) from None

        # Any exception after submission begins is treated as uncertain.
        # Never automatically retry an uncertain order.
        try:
            response = await self._adapter.submit_order(
                submitted_order, _authorization=_ORDER_GATE_AUTHORITY
            )
        except Exception as exc:
            try:
                self._ledger.mark_submission_unknown(client_order_id)
            except Exception:
                raise TestnetExecutionBlocked(
                    "submission outcome is uncertain and ledger update failed; "
                    "halt and reconcile before any further order"
                ) from None
            raise TestnetExecutionBlocked(
                f"submission outcome is uncertain ({type(exc).__name__}); "
                "do not retry before reconciliation"
            ) from None

        # Validate the acknowledgement against the exact submitted request.
        # Any mismatch leaves the outcome unresolved; never retry blindly.
        order_id = response.get("orderId") if isinstance(response, dict) else None
        valid_order_id = (
            (
                isinstance(order_id, int)
                and not isinstance(order_id, bool)
                and order_id > 0
            )
            or (
                isinstance(order_id, str)
                and order_id.isascii()
                and order_id.isdigit()
                and int(order_id) > 0
            )
        )
        valid_client_id = (
            isinstance(response, dict)
            and response.get("clientOrderId") == client_order_id
        )
        valid_identity = (
            isinstance(response, dict)
            and isinstance(response.get("symbol"), str)
            and response["symbol"].upper() == params["symbol"]
            and response.get("side") == params["side"]
            and response.get("type") == params["type"]
        )
        valid_status = (
            isinstance(response, dict)
            and response.get("status") in {"NEW", "PARTIALLY_FILLED", "FILLED"}
        )
        valid_amounts = False
        if isinstance(response, dict):
            try:
                acknowledged_quantity = Decimal(str(response["origQty"]))
                acknowledged_price = Decimal(str(response["price"]))
                valid_amounts = (
                    acknowledged_quantity.is_finite()
                    and acknowledged_price.is_finite()
                    and acknowledged_quantity == Decimal(params["quantity"])
                    and acknowledged_price == Decimal(params["price"])
                )
            except (KeyError, TypeError, ValueError, InvalidOperation):
                valid_amounts = False

        if not all((
            valid_order_id,
            valid_client_id,
            valid_identity,
            valid_status,
            valid_amounts,
        )):
            try:
                self._ledger.mark_submission_unknown(client_order_id)
            except Exception:
                raise TestnetExecutionBlocked(
                    "order acknowledgement is invalid and ledger update failed; "
                    "halt and reconcile"
                ) from None
            raise TestnetExecutionBlocked(
                "order acknowledgement is malformed or inconsistent with the "
                "submitted order; submission outcome requires reconciliation"
            )

        try:
            self._ledger.mark_acknowledged(
                client_order_id, response["orderId"]
            )
        except Exception:
            raise TestnetExecutionBlocked(
                "exchange acknowledged the order but ledger update failed; "
                "reconcile by client order ID before any further order"
            ) from None
        return response
