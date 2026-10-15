"""Read-only discovery of configured Binance Spot markets.

This module calls only the public exchange-information endpoint. It does not load
credentials, retrieve account data, or submit/cancel orders.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]{5,20}$")


class BinanceMarketDataError(RuntimeError):
    """Raised when public Binance market metadata cannot be trusted."""


def _field(value: Any, *names: str, default: Any = None) -> Any:
    """Read a field from either a dict-like response or a generated SDK model."""
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
    for name in names:
        try:
            candidate = getattr(value, name)
        except (AttributeError, TypeError):
            continue
        if candidate is not None:
            return candidate
    return default


def _text(value: Any) -> str:
    if value is None:
        return ""
    enum_value = getattr(value, "value", value)
    return str(enum_value).strip()


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return None


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _permission_values(symbol: Any) -> set[str]:
    values: set[str] = set()
    for item in _list(_field(symbol, "permissions", default=[])):
        text = _text(item).upper()
        if text:
            values.add(text)
    permission_sets = _field(symbol, "permission_sets", "permissionSets", default=[])
    for group in _list(permission_sets):
        for item in _list(group):
            text = _text(item).upper()
            if text:
                values.add(text)
    return values


def _plain_filter(raw_filter: Any) -> dict[str, str]:
    """Normalize dict filters and the official SDK's one-of filter wrappers.

    Binance's generated Spot SDK represents each exchangeInfo symbol filter as a
    wrapper object whose actual filter model is stored in ``actual_instance``.
    Unwrap that model before reading its filter type and public fields.
    """
    actual_instance = _field(raw_filter, "actual_instance", default=None)
    if actual_instance is not None:
        raw_filter = actual_instance

    # Generated SDK models provide to_dict(); it serializes aliases such as
    # filterType, minPrice, and stepSize. Plain test objects may expose attrs.
    if not isinstance(raw_filter, Mapping):
        converter = getattr(raw_filter, "to_dict", None)
        if callable(converter):
            try:
                converted = converter()
            except Exception:
                converted = None
            if isinstance(converted, Mapping):
                raw_filter = converted

    if not isinstance(raw_filter, Mapping):
        dumper = getattr(raw_filter, "model_dump", None)
        if callable(dumper):
            try:
                converted = dumper(by_alias=True, exclude_none=True)
            except TypeError:
                try:
                    converted = dumper()
                except Exception:
                    converted = None
            except Exception:
                converted = None
            if isinstance(converted, Mapping):
                raw_filter = converted

    aliases = {
        "filter_type": ("filter_type", "filterType"),
        "min_price": ("min_price", "minPrice"),
        "max_price": ("max_price", "maxPrice"),
        "tick_size": ("tick_size", "tickSize"),
        "min_qty": ("min_qty", "minQty"),
        "max_qty": ("max_qty", "maxQty"),
        "step_size": ("step_size", "stepSize"),
        "min_notional": ("min_notional", "minNotional"),
        "max_notional": ("max_notional", "maxNotional"),
        "notional": ("notional",),
        "apply_to_market": ("apply_to_market", "applyToMarket"),
        "avg_price_mins": ("avg_price_mins", "avgPriceMins"),
        "apply_min_to_market": ("apply_min_to_market", "applyMinToMarket"),
        "apply_max_to_market": ("apply_max_to_market", "applyMaxToMarket"),
    }
    result: dict[str, str] = {}
    for canonical, names in aliases.items():
        value = _field(raw_filter, *names)
        if value is not None:
            result[canonical] = _text(value)
    return result

@dataclass(frozen=True)
class MarketDiscoveryResult:
    """A validated decision about one configured market symbol."""

    requested_symbol: str
    eligible: bool
    reason: str
    status: str | None = None
    base_asset: str | None = None
    quote_asset: str | None = None
    spot_trading_allowed: bool | None = None
    permission_values: tuple[str, ...] = ()
    filters: tuple[dict[str, str], ...] = ()

    @property
    def filter_types(self) -> tuple[str, ...]:
        return tuple(filter_item["filter_type"] for filter_item in self.filters if filter_item.get("filter_type"))

    def to_public_dict(self) -> dict[str, Any]:
        """Return only public, normalized market metadata suitable for console output."""
        return {
            "symbol": self.requested_symbol,
            "eligible": self.eligible,
            "reason": self.reason,
            "status": self.status,
            "base_asset": self.base_asset,
            "quote_asset": self.quote_asset,
            "spot_trading_allowed": self.spot_trading_allowed,
            "permission_values": list(self.permission_values),
            "filters": [dict(item) for item in self.filters],
        }


def create_public_spot_client() -> Any:
    """Create the official Spot SDK client without credentials for public endpoints."""
    try:
        from binance_sdk_spot.spot import (
            ConfigurationRestAPI,
            SPOT_REST_API_PROD_URL,
            Spot,
        )
    except ImportError:
        raise BinanceMarketDataError(
            "The official Binance Spot SDK is unavailable. Install the project dependencies first."
        ) from None

    configuration = ConfigurationRestAPI(
        base_path=SPOT_REST_API_PROD_URL,
        timeout=5000,
        retries=1,
        backoff=500,
    )
    return Spot(config_rest_api=configuration)


class BinanceSpotMarketDiscoveryClient:
    """Discover and validate an allowlist using Binance public Spot metadata only."""

    def __init__(
        self,
        symbols: Iterable[str],
        quote_asset: str = "USDT",
        excluded_symbols: Iterable[str] = (),
        sdk_client: Any | None = None,
    ) -> None:
        normalized_symbols = tuple(str(symbol).strip().upper() for symbol in symbols)
        if not normalized_symbols:
            raise ValueError("At least one configured market symbol is required.")
        if any(not _SYMBOL_PATTERN.fullmatch(symbol) for symbol in normalized_symbols):
            raise ValueError("Configured market symbols must use a format such as BTCUSDT.")
        if len(set(normalized_symbols)) != len(normalized_symbols):
            raise ValueError("Configured market symbols must not contain duplicates.")

        normalized_quote = str(quote_asset).strip().upper()
        if not re.fullmatch(r"^[A-Z0-9]{2,12}$", normalized_quote):
            raise ValueError("Quote asset must be a valid asset code such as USDT.")

        normalized_excluded = tuple(str(symbol).strip().upper() for symbol in excluded_symbols)
        if any(not _SYMBOL_PATTERN.fullmatch(symbol) for symbol in normalized_excluded):
            raise ValueError("Excluded market symbols must use a format such as BTCUSDT.")
        if len(set(normalized_excluded)) != len(normalized_excluded):
            raise ValueError("Excluded market symbols must not contain duplicates.")

        self._symbols = normalized_symbols
        self._quote_asset = normalized_quote
        self._excluded_symbols = frozenset(normalized_excluded)
        self._sdk_client = sdk_client if sdk_client is not None else create_public_spot_client()

    def discover(self) -> list[MarketDiscoveryResult]:
        """Fetch exchange metadata once and assess each configured market."""
        try:
            response = self._sdk_client.rest_api.exchange_info()
            data_accessor = getattr(response, "data", None)
            payload = data_accessor() if callable(data_accessor) else (data_accessor if data_accessor is not None else response)
            symbols_raw = _field(payload, "symbols", default=None)
            if symbols_raw is None or not isinstance(symbols_raw, (list, tuple)):
                raise BinanceMarketDataError("Binance public exchange information returned an unexpected response shape.")
        except BinanceMarketDataError:
            raise
        except Exception as exc:
            # Avoid exposing arbitrary request/response details in logs.
            raise BinanceMarketDataError(
                f"Binance public exchange information request failed ({type(exc).__name__})."
            ) from None

        by_symbol: dict[str, Any] = {}
        for row in symbols_raw:
            symbol = _text(_field(row, "symbol"))
            if symbol:
                by_symbol[symbol.upper()] = row

        checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        results: list[MarketDiscoveryResult] = []
        for requested in self._symbols:
            if requested in self._excluded_symbols:
                results.append(MarketDiscoveryResult(
                    requested_symbol=requested,
                    eligible=False,
                    reason="Excluded by local market configuration.",
                ))
                continue

            row = by_symbol.get(requested)
            if row is None:
                results.append(MarketDiscoveryResult(
                    requested_symbol=requested,
                    eligible=False,
                    reason="Symbol was not present in Binance exchange information; no eligibility assumed.",
                ))
                continue

            status_raw = _field(row, "status")
            status = _text(status_raw).upper() or None
            base_asset = _text(_field(row, "base_asset", "baseAsset")).upper() or None
            quote_asset = _text(_field(row, "quote_asset", "quoteAsset")).upper() or None
            spot_allowed = _bool(_field(row, "is_spot_trading_allowed", "isSpotTradingAllowed"))
            permission_values = tuple(sorted(_permission_values(row)))
            raw_filters = _field(row, "filters", default=[])
            filters = tuple(
                filter_data
                for filter_data in (_plain_filter(item) for item in _list(raw_filters))
                if filter_data
            )

            reasons: list[str] = []
            if status != "TRADING":
                reasons.append(f"Trading status is {status or 'missing'}, not TRADING.")
            if spot_allowed is not True:
                reasons.append("Spot trading is not explicitly permitted by the response.")
            if quote_asset != self._quote_asset:
                reasons.append(f"Quote asset is {quote_asset or 'missing'}, expected {self._quote_asset}.")
            if permission_values and "SPOT" not in permission_values:
                reasons.append("Available permission metadata does not include SPOT.")
            if not filters or not all(item.get("filter_type") for item in filters):
                reasons.append("Required market filter metadata is missing or incomplete.")
            if not base_asset or not quote_asset or base_asset == quote_asset:
                reasons.append("Base/quote asset metadata is missing or inconsistent.")

            eligible = not reasons
            results.append(MarketDiscoveryResult(
                requested_symbol=requested,
                eligible=eligible,
                reason="Eligible for public Spot market monitoring." if eligible else " ".join(reasons),
                status=status,
                base_asset=base_asset,
                quote_asset=quote_asset,
                spot_trading_allowed=spot_allowed,
                permission_values=permission_values,
                filters=filters,
            ))

        # Keep this variable available for debugger inspection without leaking raw payloads.
        self.last_checked_at_utc = checked_at
        return results