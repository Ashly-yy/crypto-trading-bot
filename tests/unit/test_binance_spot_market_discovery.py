"""Offline tests for the Binance Spot market-discovery adapter; no network calls."""

from types import SimpleNamespace

import pytest

from crypto_bot.market_data.binance_spot import (
    BinanceMarketDataError,
    BinanceSpotMarketDiscoveryClient,
)


def make_symbol(
    symbol="BTCUSDT",
    *,
    status="TRADING",
    spot_allowed=True,
    base_asset="BTC",
    quote_asset="USDT",
    permissions=("SPOT",),
    filters=None,
):
    if filters is None:
        filters = [
            {"filterType": "PRICE_FILTER", "tickSize": "0.01000000"},
            {"filterType": "LOT_SIZE", "minQty": "0.00001000", "stepSize": "0.00001000"},
            {"filterType": "MIN_NOTIONAL", "minNotional": "5.00000000"},
        ]
    return {
        "symbol": symbol,
        "status": status,
        "baseAsset": base_asset,
        "quoteAsset": quote_asset,
        "isSpotTradingAllowed": spot_allowed,
        "permissions": list(permissions),
        "filters": filters,
    }


class FakeRestAPI:
    def __init__(self, symbols=None, error=None):
        self.symbols = symbols or []
        self.error = error
        self.calls = 0

    def exchange_info(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return SimpleNamespace(data=lambda: SimpleNamespace(symbols=self.symbols))


class FakeSDKClient:
    def __init__(self, symbols=None, error=None):
        self.rest_api = FakeRestAPI(symbols=symbols, error=error)


def test_eligible_spot_market_is_normalised_and_filters_are_recorded():
    fake = FakeSDKClient([make_symbol()])
    results = BinanceSpotMarketDiscoveryClient(["btcusdt"], sdk_client=fake).discover()
    assert len(results) == 1
    result = results[0]
    assert result.eligible is True
    assert result.requested_symbol == "BTCUSDT"
    assert result.base_asset == "BTC"
    assert result.quote_asset == "USDT"
    assert "PRICE_FILTER" in result.filter_types
    assert "LOT_SIZE" in result.filter_types
    assert fake.rest_api.calls == 1


def test_non_trading_market_is_rejected():
    fake = FakeSDKClient([make_symbol(status="HALT")])
    result = BinanceSpotMarketDiscoveryClient(["BTCUSDT"], sdk_client=fake).discover()[0]
    assert not result.eligible
    assert "not TRADING" in result.reason


def test_market_without_explicit_spot_permission_is_rejected():
    fake = FakeSDKClient([make_symbol(spot_allowed=False)])
    result = BinanceSpotMarketDiscoveryClient(["BTCUSDT"], sdk_client=fake).discover()[0]
    assert not result.eligible
    assert "not explicitly permitted" in result.reason


def test_wrong_quote_asset_is_rejected():
    fake = FakeSDKClient([make_symbol(quote_asset="USDC")])
    result = BinanceSpotMarketDiscoveryClient(["BTCUSDT"], quote_asset="USDT", sdk_client=fake).discover()[0]
    assert not result.eligible
    assert "expected USDT" in result.reason


def test_permission_metadata_without_spot_is_rejected():
    fake = FakeSDKClient([make_symbol(permissions=("MARGIN", "LEVERAGED"))])
    result = BinanceSpotMarketDiscoveryClient(["BTCUSDT"], sdk_client=fake).discover()[0]
    assert not result.eligible
    assert "does not include SPOT" in result.reason


def test_missing_market_is_rejected_not_assumed_eligible():
    fake = FakeSDKClient([])
    result = BinanceSpotMarketDiscoveryClient(["BTCUSDT"], sdk_client=fake).discover()[0]
    assert not result.eligible
    assert "not present" in result.reason


def test_excluded_market_is_never_eligible():
    fake = FakeSDKClient([make_symbol()])
    result = BinanceSpotMarketDiscoveryClient(
        ["BTCUSDT"], excluded_symbols=["BTCUSDT"], sdk_client=fake
    ).discover()[0]
    assert not result.eligible
    assert "Excluded" in result.reason


def test_missing_filters_cannot_be_marked_eligible():
    fake = FakeSDKClient([make_symbol(filters=[])])
    result = BinanceSpotMarketDiscoveryClient(["BTCUSDT"], sdk_client=fake).discover()[0]
    assert not result.eligible
    assert "filter metadata" in result.reason


def test_api_failure_is_reported_without_raw_exception_details():
    fake = FakeSDKClient(error=RuntimeError("simulated private-looking payload"))
    with pytest.raises(BinanceMarketDataError) as captured:
        BinanceSpotMarketDiscoveryClient(["BTCUSDT"], sdk_client=fake).discover()
    assert "RuntimeError" in str(captured.value)
    assert "simulated private-looking payload" not in str(captured.value)


def test_invalid_or_duplicate_symbols_are_rejected():
    with pytest.raises(ValueError):
        BinanceSpotMarketDiscoveryClient(["BTC/USDT"], sdk_client=FakeSDKClient())
    with pytest.raises(ValueError):
        BinanceSpotMarketDiscoveryClient(["BTCUSDT", "btcusdt"], sdk_client=FakeSDKClient())