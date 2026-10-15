"""Regression test for generated Binance SDK one-of filter model wrappers."""

from types import SimpleNamespace

from crypto_bot.market_data.binance_spot import BinanceSpotMarketDiscoveryClient


class FakeGeneratedFilter:
    """Minimal stand-in for a generated SDK filter model."""

    def __init__(self, data):
        self._data = data

    def to_dict(self):
        return dict(self._data)


class FakeRestAPI:
    def exchange_info(self):
        filters = [
            SimpleNamespace(actual_instance=FakeGeneratedFilter({
                "filterType": "PRICE_FILTER",
                "minPrice": "0.00000100",
                "maxPrice": "1000000.00000000",
                "tickSize": "0.00000100",
            })),
            SimpleNamespace(actual_instance=FakeGeneratedFilter({
                "filterType": "LOT_SIZE",
                "minQty": "0.00001000",
                "maxQty": "9000.00000000",
                "stepSize": "0.00001000",
            })),
            SimpleNamespace(actual_instance=FakeGeneratedFilter({
                "filterType": "NOTIONAL",
                "minNotional": "5.00000000",
                "maxNotional": "1000000.00000000",
            })),
        ]
        symbol = SimpleNamespace(
            symbol="BTCUSDT",
            status="TRADING",
            base_asset="BTC",
            quote_asset="USDT",
            is_spot_trading_allowed=True,
            permissions=["SPOT"],
            permission_sets=[["SPOT"]],
            filters=filters,
        )
        payload = SimpleNamespace(symbols=[symbol])
        return SimpleNamespace(data=lambda: payload)


class FakeSDK:
    def __init__(self):
        self.rest_api = FakeRestAPI()


def test_official_sdk_style_actual_instance_filters_are_normalized():
    client = BinanceSpotMarketDiscoveryClient(["BTCUSDT"], sdk_client=FakeSDK())
    result = client.discover()[0]

    assert result.eligible is True
    assert result.filter_types == ("PRICE_FILTER", "LOT_SIZE", "NOTIONAL")
    assert result.filters[0]["tick_size"] == "0.00000100"
    assert result.filters[1]["step_size"] == "0.00001000"
    assert result.filters[2]["min_notional"] == "5.00000000"