"""Fail-closed validation of LIMIT orders against Binance Spot exchange metadata."""
from decimal import Decimal, InvalidOperation


class ExchangeFilterValidationError(ValueError):
    """Raised when exchange metadata or an order cannot be safely validated."""


def _decimal(value, field):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ExchangeFilterValidationError(f"Invalid numeric value for {field}") from None
    if not result.is_finite():
        raise ExchangeFilterValidationError(f"Non-finite numeric value for {field}")
    return result


def _positive(value, field):
    result = _decimal(value, field)
    if result <= 0:
        raise ExchangeFilterValidationError(f"{field} must be positive")
    return result


def _check_range(value, rule, field):
    minimum = _decimal(rule.get("min", "0"), f"{field} minimum")
    maximum = _decimal(rule.get("max", "0"), f"{field} maximum")
    if minimum > 0 and value < minimum:
        raise ExchangeFilterValidationError(f"{field} is below the exchange minimum")
    if maximum > 0 and value > maximum:
        raise ExchangeFilterValidationError(f"{field} exceeds the exchange maximum")


def _check_increment(value, increment, field):
    step = _decimal(increment, f"{field} increment")
    if step < 0:
        raise ExchangeFilterValidationError(f"Invalid {field} increment")
    if step > 0 and value % step != 0:
        raise ExchangeFilterValidationError(f"{field} does not match the exchange increment")


def validate_limit_order_against_exchange_info(order, exchange_info):
    """Validate a LIMIT order against a full /api/v3/exchangeInfo response.

    No network calls are made. Missing or ambiguous metadata is rejected.
    """
    if not isinstance(exchange_info, dict):
        raise ExchangeFilterValidationError("Exchange metadata must be an object")

    symbols = exchange_info.get("symbols")
    if not isinstance(symbols, list) or not symbols:
        raise ExchangeFilterValidationError("Exchange metadata has no symbol list")

    symbol = str(getattr(order, "symbol", "")).upper()
    if not symbol:
        raise ExchangeFilterValidationError("Order symbol is missing")

    matches = [
        item for item in symbols
        if isinstance(item, dict) and item.get("symbol") == symbol
    ]
    if len(matches) != 1:
        raise ExchangeFilterValidationError("Symbol is missing or ambiguous in exchange metadata")

    metadata = matches[0]
    if metadata.get("status") != "TRADING":
        raise ExchangeFilterValidationError("Symbol is not trading")

    if getattr(order, "type", getattr(order, "order_type", None)) != "LIMIT":
        raise ExchangeFilterValidationError("Only LIMIT orders are supported by this validator")

    if getattr(order, "side", None) not in ("BUY", "SELL"):
        raise ExchangeFilterValidationError("Unsupported order side")

    raw_filters = metadata.get("filters")
    if not isinstance(raw_filters, list):
        raise ExchangeFilterValidationError("Symbol filters are missing")

    filters = {}
    for item in raw_filters:
        if not isinstance(item, dict) or not isinstance(item.get("filterType"), str):
            raise ExchangeFilterValidationError("Malformed exchange filter")
        kind = item["filterType"]
        if kind in filters:
            raise ExchangeFilterValidationError(f"Duplicate {kind} filter")
        filters[kind] = item

    for required in ("LOT_SIZE", "PRICE_FILTER"):
        if required not in filters:
            raise ExchangeFilterValidationError(f"Required {required} filter is missing")

    notional_filter = filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL")
    if notional_filter is None:
        raise ExchangeFilterValidationError("Notional filter is missing")

    quantity = _positive(getattr(order, "quantity", None), "quantity")
    price = _positive(getattr(order, "price", None), "price")

    lot = filters["LOT_SIZE"]
    _check_range(quantity, {"min": lot.get("minQty"), "max": lot.get("maxQty")}, "quantity")
    _check_increment(quantity, lot.get("stepSize"), "quantity")

    price_filter = filters["PRICE_FILTER"]
    _check_range(price, {"min": price_filter.get("minPrice"), "max": price_filter.get("maxPrice")}, "price")
    _check_increment(price, price_filter.get("tickSize"), "price")

    notional = quantity * price
    if "minNotional" not in notional_filter:
        raise ExchangeFilterValidationError("Notional minimum is missing")
    min_notional = _decimal(notional_filter.get("minNotional", "0"), "minimum notional")
    max_notional = _decimal(notional_filter.get("maxNotional", "0"), "maximum notional")

    if min_notional > 0 and notional < min_notional:
        raise ExchangeFilterValidationError("Order notional is below the exchange minimum")
    if max_notional > 0 and notional > max_notional:
        raise ExchangeFilterValidationError("Order notional exceeds the exchange maximum")

    return True
