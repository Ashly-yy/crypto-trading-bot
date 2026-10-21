"""Mock-only tests for exchange-filter validation; no network or orders."""
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from crypto_bot.execution.exchange_filters import (
    ExchangeFilterValidationError,
    validate_limit_order_against_exchange_info,
)


def metadata(**changes):
    symbol = {
        "symbol": "BTCUSDT",
        "status": "TRADING",
        "filters": [
            {"filterType": "LOT_SIZE", "minQty": "0.001", "maxQty": "100", "stepSize": "0.001"},
            {"filterType": "PRICE_FILTER", "minPrice": "0.01", "maxPrice": "1000000", "tickSize": "0.01"},
            {"filterType": "MIN_NOTIONAL", "minNotional": "10"},
        ],
    }
    symbol.update(changes)
    return {"symbols": [symbol]}


def order(quantity="0.100", price="100.00", symbol="BTCUSDT", side="BUY", kind="LIMIT"):
    return SimpleNamespace(
        quantity=quantity, price=price, symbol=symbol, side=side, type=kind
    )


def test_valid_limit_order_passes():
    assert validate_limit_order_against_exchange_info(order(), metadata()) is True


@pytest.mark.parametrize("quantity", ["0", "-1", "0.0001", "100.001", "0.0105", "NaN"])
def test_invalid_quantity_is_rejected(quantity):
    with pytest.raises(ExchangeFilterValidationError):
        validate_limit_order_against_exchange_info(order(quantity=quantity), metadata())


@pytest.mark.parametrize("price", ["0", "-1", "0.001", "1000000.01", "100.005", "Infinity"])
def test_invalid_price_is_rejected(price):
    with pytest.raises(ExchangeFilterValidationError):
        validate_limit_order_against_exchange_info(order(price=price), metadata())


def test_order_below_minimum_notional_is_rejected():
    with pytest.raises(ExchangeFilterValidationError, match="notional"):
        validate_limit_order_against_exchange_info(order(quantity="0.010", price="10"), metadata())


def test_maximum_notional_is_enforced_when_present():
    info = metadata()
    info["symbols"][0]["filters"].append(
        {"filterType": "NOTIONAL", "minNotional": "10", "maxNotional": "500"}
    )
    with pytest.raises(ExchangeFilterValidationError, match="notional"):
        validate_limit_order_against_exchange_info(order(quantity="10", price="100"), info)


def test_symbol_must_exist_exactly_once():
    with pytest.raises(ExchangeFilterValidationError, match="missing or ambiguous"):
        validate_limit_order_against_exchange_info(order(symbol="ETHUSDT"), metadata())


def test_symbol_must_be_trading():
    with pytest.raises(ExchangeFilterValidationError, match="not trading"):
        validate_limit_order_against_exchange_info(
            order(), metadata(status="BREAK")
        )


@pytest.mark.parametrize("info", [None, {}, {"symbols": []}, {"symbols": "bad"}])
def test_missing_or_malformed_metadata_fails_closed(info):
    with pytest.raises(ExchangeFilterValidationError):
        validate_limit_order_against_exchange_info(order(), info)


@pytest.mark.parametrize(
    "filters",
    [
        [],
        [{"filterType": "LOT_SIZE", "minQty": "0.001", "maxQty": "10", "stepSize": "0.001"}],
        [
            {"filterType": "LOT_SIZE", "minQty": "0.001", "maxQty": "10", "stepSize": "0.001"},
            {"filterType": "PRICE_FILTER", "minPrice": "0.01", "maxPrice": "1000", "tickSize": "0.01"},
        ],
    ],
)
def test_required_filters_must_exist(filters):
    with pytest.raises(ExchangeFilterValidationError):
        validate_limit_order_against_exchange_info(
            order(), {"symbols": [{"symbol": "BTCUSDT", "status": "TRADING", "filters": filters}]}
        )


def test_market_order_is_rejected():
    with pytest.raises(ExchangeFilterValidationError, match="LIMIT"):
        validate_limit_order_against_exchange_info(order(kind="MARKET"), metadata())


def test_duplicate_filter_is_rejected():
    info = metadata()
    info["symbols"][0]["filters"].append(info["symbols"][0]["filters"][0].copy())
    with pytest.raises(ExchangeFilterValidationError, match="Duplicate"):
        validate_limit_order_against_exchange_info(order(), info)
