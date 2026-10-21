"""Mock-only tests for the Binance Spot Testnet adapter; no network access or orders."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from crypto_bot.config.settings import Settings, TradingMode
from crypto_bot.execution.adapters.binance_spot_testnet import (
    BinanceSpotTestnetAdapter,
    SpotOrderRequest,
    TESTNET_BASE_URL,
    TestnetConfigurationError,
    TestnetRequestError,
)


def make_settings(**overrides):
    values = {
        "_env_file": None,
        "app_env": "test",
        "default_trading_mode": TradingMode.BINANCE_SPOT_TESTNET,
        "live_trading_enabled": False,
        "binance_spot_testnet_api_key": SecretStr("mock-key"),
        "binance_spot_testnet_api_secret": SecretStr("mock-secret"),
    }
    values.update(overrides)
    return Settings(**values)


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status = status
        self.payload = payload if payload is not None else {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def text(self):
        return json.dumps(self.payload)


class FakeSession:
    def __init__(self, response=None):
        self.response = response or FakeResponse()
        self.calls = []

    async def request(self, method, url, headers=None, **kwargs):
        assert kwargs.get("allow_redirects") is False
        assert kwargs.get("timeout") is not None
        self.calls.append((method, url, headers or {}))
        return self.response


@pytest.mark.parametrize("overrides", [
    {"default_trading_mode": TradingMode.BACKTEST},
    {"live_trading_enabled": True},
    {"binance_spot_testnet_api_key": None},
    {"binance_spot_testnet_api_secret": None},
])
def test_constructor_fails_closed_for_unsafe_configuration(overrides):
    with pytest.raises(TestnetConfigurationError):
        BinanceSpotTestnetAdapter(make_settings(**overrides), session=FakeSession())


def test_ping_uses_fixed_testnet_endpoint_without_credentials_header():
    session = FakeSession()
    adapter = BinanceSpotTestnetAdapter(make_settings(), session=session, clock_ms=lambda: 123)
    import asyncio
    assert asyncio.run(adapter.ping()) == {}
    method, url, headers = session.calls[0]
    assert method == "GET"
    assert url == f"{TESTNET_BASE_URL}/api/v3/ping"
    assert "X-MBX-APIKEY" not in headers
    assert "api.binance.com/api" not in url


def test_signed_account_request_uses_api_key_header_and_signature():
    session = FakeSession(FakeResponse(payload={"balances": []}))
    adapter = BinanceSpotTestnetAdapter(make_settings(), session=session, clock_ms=lambda: 123)
    import asyncio
    assert asyncio.run(adapter.get_account()) == {"balances": []}
    _, url, headers = session.calls[0]
    assert url.startswith(TESTNET_BASE_URL + "/api/v3/account?")
    assert "signature=" in url and "timestamp=123" in url
    assert headers["X-MBX-APIKEY"] == "mock-key"
    assert "mock-secret" not in url


def test_order_validation_rejects_invalid_values_before_network_call():
    with pytest.raises(ValueError):
        SpotOrderRequest("BTCUSDT", "BUY", "MARKET", "0").to_params()
    with pytest.raises(ValueError):
        SpotOrderRequest("BTCUSDT", "SHORT", "MARKET", "1").to_params()
    with pytest.raises(ValueError):
        SpotOrderRequest("BTCUSDT", "BUY", "LIMIT", "1").to_params()


def test_limit_order_parameters_are_normalized():
    params = SpotOrderRequest("btcusdt", "buy", "limit", "0.10", "65000.00").to_params()
    assert params == {
        "symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT", "quantity": "0.10",
        "price": "65000.00", "timeInForce": "GTC",
    }


def test_exchange_error_is_sanitized_and_reported():
    session = FakeSession(FakeResponse(status=400, payload={"code": -1121, "msg": "Invalid symbol."}))
    adapter = BinanceSpotTestnetAdapter(make_settings(), session=session, clock_ms=lambda: 123)
    import asyncio
    with pytest.raises(TestnetRequestError, match="Invalid symbol"):
        asyncio.run(adapter.get_account())



def test_exchange_info_uses_fixed_testnet_endpoint():
    import asyncio

    session = FakeSession(FakeResponse(payload={"symbols": []}))
    adapter = BinanceSpotTestnetAdapter(make_settings(), session=session)

    result = asyncio.run(adapter.get_exchange_info("btcusdt"))

    assert result == {"symbols": []}
    method, url, headers = session.calls[0]
    assert method == "GET"
    assert url == f"{TESTNET_BASE_URL}/api/v3/exchangeInfo?symbol=BTCUSDT"
    assert headers == {}


def test_successful_response_with_malformed_json_fails_safely():
    import asyncio
    from crypto_bot.execution.adapters.binance_spot_testnet import TestnetRequestError

    class MalformedResponse(FakeResponse):
        async def text(self):
            return "{not-valid-json"

    session = FakeSession(MalformedResponse(status=200))
    adapter = BinanceSpotTestnetAdapter(make_settings(), session=session)

    with pytest.raises(TestnetRequestError, match="invalid JSON"):
        asyncio.run(adapter.get_exchange_info("BTCUSDT"))

    assert len(session.calls) == 1


def test_timeout_raises_transport_error_without_retrying():
    import asyncio
    from crypto_bot.execution.adapters.binance_spot_testnet import TestnetTransportError

    class TimeoutSession:
        def __init__(self):
            self.calls = []

        async def request(self, method, url, headers=None, **kwargs):
            self.calls.append((method, url))
            raise TimeoutError("mock timeout")

    session = TimeoutSession()
    adapter = BinanceSpotTestnetAdapter(make_settings(), session=session)

    with pytest.raises(TestnetTransportError, match="Do not blindly retry orders"):
        asyncio.run(adapter.get_exchange_info("BTCUSDT"))

    assert len(session.calls) == 1

def test_get_my_trades_uses_signed_testnet_endpoint_and_optional_filters():
    import asyncio
    from urllib.parse import parse_qs, urlsplit

    sample = [{
        "id": 7,
        "orderId": 42,
        "symbol": "BTCUSDT",
        "price": "100.00",
        "qty": "0.01",
        "quoteQty": "1.00",
        "commission": "0.001",
        "commissionAsset": "BTC",
        "time": 123456,
        "isBuyer": True,
        "isMaker": False,
        "isBestMatch": True,
    }]
    session = FakeSession(FakeResponse(payload=sample))
    adapter = BinanceSpotTestnetAdapter(
        make_settings(), session=session, clock_ms=lambda: 123
    )

    result = asyncio.run(adapter.get_my_trades(
        "btcusdt", order_id=42, limit=100, from_id=5,
    ))

    assert result == sample
    method, url, headers = session.calls[0]
    parsed = urlsplit(url)
    params = parse_qs(parsed.query)
    assert method == "GET"
    assert parsed.scheme == "https"
    assert parsed.netloc == "testnet.binance.vision"
    assert parsed.path == "/api/v3/myTrades"
    assert params["symbol"] == ["BTCUSDT"]
    assert params["orderId"] == ["42"]
    assert params["limit"] == ["100"]
    assert params["fromId"] == ["5"]
    assert params["timestamp"] == ["123"]
    assert "signature" in params
    assert headers["X-MBX-APIKEY"] == "mock-key"


def test_get_my_trades_rejects_invalid_parameters_before_network_call():
    import asyncio

    session = FakeSession()
    adapter = BinanceSpotTestnetAdapter(make_settings(), session=session)

    invalid_calls = [
        lambda: adapter.get_my_trades(""),
        lambda: adapter.get_my_trades("BTC/USDT"),
        lambda: adapter.get_my_trades("BTCUSDT", order_id=0),
        lambda: adapter.get_my_trades("BTCUSDT", order_id=True),
        lambda: adapter.get_my_trades("BTCUSDT", limit=1001),
        lambda: adapter.get_my_trades("BTCUSDT", limit=0),
        lambda: adapter.get_my_trades("BTCUSDT", from_id=-1),
        lambda: adapter.get_my_trades("BTCUSDT", start_time=-1),
        lambda: adapter.get_my_trades("BTCUSDT", end_time="2000"),
        lambda: adapter.get_my_trades("BTCUSDT", order_id=42, start_time=1000),
        lambda: adapter.get_my_trades("BTCUSDT", from_id=5, end_time=2000),
        lambda: adapter.get_my_trades("BTCUSDT", start_time=2000, end_time=1000),
    ]
    for call in invalid_calls:
        with pytest.raises(ValueError):
            asyncio.run(call())

    assert session.calls == []


def test_get_my_trades_rejects_malformed_response():
    import asyncio

    session = FakeSession(FakeResponse(payload={"trades": []}))
    adapter = BinanceSpotTestnetAdapter(make_settings(), session=session)

    with pytest.raises(TestnetRequestError, match="malformed trade history"):
        asyncio.run(adapter.get_my_trades("BTCUSDT"))

    assert len(session.calls) == 1



@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("id", None),
        ("price", "NaN"),
        ("symbol", "ETHUSDT"),
        ("isBuyer", "true"),
        ("qty", "-0.01"),
        ("commissionAsset", ""),
    ],
)
def test_get_my_trades_rejects_malformed_trade_records(field, replacement):
    import asyncio

    trade = {
        "id": 7,
        "orderId": 42,
        "symbol": "BTCUSDT",
        "price": "100.00",
        "qty": "0.01",
        "quoteQty": "1.00",
        "commission": "0.001",
        "commissionAsset": "BTC",
        "time": 123456,
        "isBuyer": True,
        "isMaker": False,
        "isBestMatch": True,
    }

    if replacement is None:
        trade.pop(field)
    else:
        trade[field] = replacement

    session = FakeSession(FakeResponse(payload=[trade]))
    adapter = BinanceSpotTestnetAdapter(make_settings(), session=session)

    with pytest.raises(TestnetRequestError, match="trade"):
        asyncio.run(adapter.get_my_trades("BTCUSDT"))

    assert len(session.calls) == 1