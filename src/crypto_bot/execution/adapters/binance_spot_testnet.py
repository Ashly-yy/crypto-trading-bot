"""Explicit Binance Spot Testnet REST adapter. Never targets production endpoints."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping
from urllib.parse import urlencode

import aiohttp

from crypto_bot.config.settings import Settings, TradingMode
from crypto_bot.execution._gate_authority import _ORDER_GATE_AUTHORITY

TESTNET_BASE_URL = "https://testnet.binance.vision"
_ALLOWED_ORDER_SIDES = {"BUY", "SELL"}
_ALLOWED_ORDER_TYPES = {"MARKET", "LIMIT"}


class TestnetAdapterError(RuntimeError):
    """Base exception for safe, sanitized Testnet adapter failures."""


class TestnetConfigurationError(TestnetAdapterError):
    """Raised when Testnet execution is not explicitly and safely configured."""

    __test__ = False


class TestnetRequestError(TestnetAdapterError):
    """Raised when Binance rejects a request or returns an invalid response."""

    __test__ = False


class TestnetTransportError(TestnetAdapterError):
    """Raised when a request outcome may be unknown due to transport failure."""

    __test__ = False


@dataclass(frozen=True)
class SpotOrderRequest:
    symbol: str
    side: str
    order_type: str
    quantity: str
    price: str | None = None
    time_in_force: str = "GTC"
    client_order_id: str | None = None

    def to_params(self) -> dict[str, str]:
        symbol = self.symbol.strip().upper()
        side = self.side.strip().upper()
        order_type = self.order_type.strip().upper()
        if not symbol or not symbol.isascii() or not symbol.isalnum():
            raise ValueError("symbol must contain only ASCII letters and digits")
        if side not in _ALLOWED_ORDER_SIDES:
            raise ValueError("side must be BUY or SELL")
        if order_type not in _ALLOWED_ORDER_TYPES:
            raise ValueError("order_type must be MARKET or LIMIT")
        try:
            quantity = Decimal(self.quantity)
        except (InvalidOperation, TypeError):
            raise ValueError("quantity must be a positive decimal") from None
        if not quantity.is_finite() or quantity <= 0:
            raise ValueError("quantity must be a positive finite decimal")
        params = {"symbol": symbol, "side": side, "type": order_type, "quantity": format(quantity, "f")}
        if order_type == "LIMIT":
            if self.price is None:
                raise ValueError("LIMIT orders require a positive price")
            try:
                price = Decimal(self.price)
            except (InvalidOperation, TypeError):
                raise ValueError("price must be a positive decimal") from None
            if not price.is_finite() or price <= 0:
                raise ValueError("price must be a positive finite decimal")
            tif = self.time_in_force.strip().upper()
            if tif not in {"GTC", "IOC", "FOK"}:
                raise ValueError("time_in_force must be GTC, IOC, or FOK")
            params.update(price=format(price, "f"), timeInForce=tif)
        elif self.price is not None:
            raise ValueError("MARKET orders must not include a price")
        if self.client_order_id is not None:
            client_id = self.client_order_id.strip()
            if not client_id or len(client_id) > 36 or not client_id.isascii():
                raise ValueError("client_order_id must be 1-36 ASCII characters")
            params["newClientOrderId"] = client_id
        return params


class BinanceSpotTestnetAdapter:
    """Minimal async REST adapter bound to the Spot Testnet host at construction."""

    def __init__(
        self,
        settings: Settings,
        *,
        session: Any | None = None,
        timeout_seconds: float = 10.0,
        clock_ms: Any = None,
    ) -> None:
        if settings.default_trading_mode is not TradingMode.BINANCE_SPOT_TESTNET:
            raise TestnetConfigurationError("default trading mode must explicitly be BINANCE_SPOT_TESTNET")
        if settings.live_trading_enabled:
            raise TestnetConfigurationError("live trading flag must remain disabled for Testnet adapter use")
        key = settings.binance_spot_testnet_api_key
        secret = settings.binance_spot_testnet_api_secret
        if key is None or secret is None or not key.get_secret_value().strip() or not secret.get_secret_value().strip():
            raise TestnetConfigurationError("Testnet API key and secret must be configured locally")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._api_key = key.get_secret_value()
        self._api_secret = secret.get_secret_value().encode("utf-8")
        self._session = session
        self._owns_session = session is None
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        # Intentionally fixed: there is no configurable production URL or fallback.
        self.base_url = TESTNET_BASE_URL

    async def __aenter__(self) -> "BinanceSpotTestnetAdapter":
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    def _signed_query(self, params: Mapping[str, Any]) -> str:
        values = {key: str(value) for key, value in params.items()}
        values["timestamp"] = str(self._clock_ms())
        values["recvWindow"] = "5000"
        payload = urlencode(values)
        signature = hmac.new(self._api_secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{payload}&signature={signature}"

    async def _request(
        self, method: str, path: str, *, params: Mapping[str, Any] | None = None, signed: bool = False
    ) -> Any:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        query = self._signed_query(params or {}) if signed else urlencode(params or {})
        url = f"{TESTNET_BASE_URL}{path}"
        if query:
            url = f"{url}?{query}"
        headers = {"X-MBX-APIKEY": self._api_key} if signed else {}
        try:
            response = await self._session.request(method, url, headers=headers, allow_redirects=False, timeout=self._timeout)
            async with response:
                body = await response.text()
                try:
                    payload = json.loads(body) if body else {}
                except json.JSONDecodeError:
                    payload = None
                if response.status < 200 or response.status >= 300:
                    # Do not include request URLs, headers, signatures, or credentials in errors.
                    message = payload.get("msg", "exchange request failed") if isinstance(payload, dict) else "exchange returned a non-JSON error"
                    code = payload.get("code") if isinstance(payload, dict) else None
                    raise TestnetRequestError(f"Binance Spot Testnet HTTP {response.status}; code={code}; message={message}")
                if payload is None:
                    raise TestnetRequestError("Binance Spot Testnet returned invalid JSON")
                return payload
        except TestnetRequestError:
            raise
        except (aiohttp.ClientError, TimeoutError, OSError) as exc:
            raise TestnetTransportError(
                f"Testnet transport failed; request outcome may be unknown ({type(exc).__name__}). Do not blindly retry orders."
            ) from None

    async def ping(self) -> Any:
        """Check public Testnet connectivity; no credentials or orders are used."""
        return await self._request("GET", "/api/v3/ping")

    async def get_exchange_info(self, symbol: str | None = None) -> Any:
        params = {"symbol": symbol.strip().upper()} if symbol else {}
        return await self._request("GET", "/api/v3/exchangeInfo", params=params)

    async def get_account(self) -> Any:
        return await self._request("GET", "/api/v3/account", signed=True)

    async def get_order(self, symbol: str, order_id: int | None = None, client_order_id: str | None = None) -> Any:
        if (order_id is None) == (client_order_id is None):
            raise ValueError("provide exactly one of order_id or client_order_id")
        params: dict[str, Any] = {"symbol": symbol.strip().upper()}
        params["orderId" if order_id is not None else "origClientOrderId"] = order_id if order_id is not None else client_order_id
        return await self._request("GET", "/api/v3/order", params=params, signed=True)

    async def get_my_trades(
        self,
        symbol: str,
        *,
        order_id: int | None = None,
        limit: int | None = None,
        from_id: int | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[dict[str, Any]]:
        """Read personal trade history from Spot Testnet; never places an order."""
        normalized_symbol = symbol.strip().upper() if isinstance(symbol, str) else ""
        if not normalized_symbol or not normalized_symbol.isascii() or not normalized_symbol.isalnum():
            raise ValueError("symbol must contain only ASCII letters and digits")

        optional_values = {
            "order_id": (order_id, 1, None),
            "limit": (limit, 1, 1000),
            "from_id": (from_id, 0, None),
            "start_time": (start_time, 0, None),
            "end_time": (end_time, 0, None),
        }
        for name, (value, minimum, maximum) in optional_values.items():
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")
            if value < minimum or (maximum is not None and value > maximum):
                if maximum is None:
                    raise ValueError(f"{name} must be at least {minimum}")
                raise ValueError(f"{name} must be between {minimum} and {maximum}")

        # Binance documents specific supported filter combinations.
        if order_id is not None and (start_time is not None or end_time is not None):
            raise ValueError("order_id cannot be combined with start_time or end_time")
        if from_id is not None and (start_time is not None or end_time is not None):
            raise ValueError("from_id cannot be combined with start_time or end_time")
        if start_time is not None and end_time is not None and start_time > end_time:
            raise ValueError("start_time must not be later than end_time")

        params: dict[str, Any] = {"symbol": normalized_symbol}
        for name, value in (
            ("orderId", order_id),
            ("limit", limit),
            ("fromId", from_id),
            ("startTime", start_time),
            ("endTime", end_time),
        ):
            if value is not None:
                params[name] = value

        payload = await self._request(
            "GET", "/api/v3/myTrades", params=params, signed=True
        )
        required_string_fields = (
            "price", "qty", "quoteQty", "commission", "commissionAsset",
        )
        required_integer_fields = ("id", "orderId", "time")
        required_boolean_fields = ("isBuyer", "isMaker", "isBestMatch")

        def valid_decimal_string(value: Any) -> bool:
            if not isinstance(value, str) or not value.strip():
                return False
            try:
                number = Decimal(value)
            except (InvalidOperation, ValueError):
                return False
            return number.is_finite() and number >= 0

        if not isinstance(payload, list):
            raise TestnetRequestError(
                "Binance Spot Testnet returned malformed trade history"
            )

        for trade in payload:
            if not isinstance(trade, dict):
                raise TestnetRequestError(
                    "Binance Spot Testnet returned malformed trade history"
                )

            if any(field not in trade for field in (
                "symbol", *required_string_fields,
                *required_integer_fields, *required_boolean_fields,
            )):
                raise TestnetRequestError(
                    "Binance Spot Testnet returned malformed trade history"
                )

            if trade["symbol"] != normalized_symbol:
                raise TestnetRequestError(
                    "Binance Spot Testnet returned a mismatched trade symbol"
                )

            for field in required_integer_fields:
                value = trade[field]
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise TestnetRequestError(
                        "Binance Spot Testnet returned malformed trade history"
                    )

            for field in required_string_fields:
                if field == "commissionAsset":
                    value = trade[field]
                    if not isinstance(value, str) or not value.strip():
                        raise TestnetRequestError(
                            "Binance Spot Testnet returned malformed trade history"
                        )
                elif not valid_decimal_string(trade[field]):
                    raise TestnetRequestError(
                        "Binance Spot Testnet returned malformed trade history"
                    )

            if any(not isinstance(trade[field], bool) for field in required_boolean_fields):
                raise TestnetRequestError(
                    "Binance Spot Testnet returned malformed trade history"
                )

        return payload

    async def submit_order(
        self, order: SpotOrderRequest, *, _authorization: object | None = None
    ) -> Any:
        """Submit only through the risk-gated Testnet execution path."""
        if _authorization is not _ORDER_GATE_AUTHORITY:
            raise TestnetConfigurationError(
                "direct order submission is blocked; use TestnetOrderExecutor"
            )
        params = order.to_params()
        return await self._request("POST", "/api/v3/order", params=params, signed=True)
