"""Read-only Binance Spot candle retrieval and validation.

This module handles public market data only. It never reads account credentials,
queries account endpoints, or submits orders.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from crypto_bot.market_data.binance_spot import (
    BinanceMarketDataError,
    create_public_spot_client,
)

_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]{5,20}$")
# Values are milliseconds; 1M is calendar-based and handled separately.
INTERVAL_MILLISECONDS: dict[str, int | None] = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "3d": 259_200_000,
    "1w": 604_800_000,
    "1M": None,
}
MAX_KLINE_LIMIT = 1000


class CandleDataError(BinanceMarketDataError):
    """Raised when exchange candle data cannot be trusted or normalized."""


def _field(value: Any, *names: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
        return default
    for name in names:
        try:
            candidate = getattr(value, name)
        except (AttributeError, TypeError):
            continue
        if candidate is not None:
            return candidate
    return default


def _plain(value: Any, _depth: int = 0) -> Any:
    """Convert SDK/Pydantic response models into ordinary Python values."""
    if _depth > 8:
        return value
    if isinstance(value, Mapping):
        return {str(k): _plain(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v, _depth + 1) for v in value]
    for method_name in ("model_dump", "to_dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            try:
                converted = method(by_alias=True, exclude_none=True) if method_name == "model_dump" else method()
            except TypeError:
                try:
                    converted = method()
                except Exception:
                    converted = None
            except Exception:
                converted = None
            if converted is not None and converted is not value:
                return _plain(converted, _depth + 1)
    for name in ("root", "__root__", "items"):
        candidate = getattr(value, name, None)
        if candidate is not None and candidate is not value:
            if name != "items" or not callable(candidate):
                return _plain(candidate, _depth + 1)
    return value


def _unwrap_response(response: Any) -> Any:
    data_accessor = getattr(response, "data", None)
    try:
        payload = data_accessor() if callable(data_accessor) else data_accessor
    except Exception as exc:
        raise CandleDataError(f"Binance returned an unreadable candle response ({type(exc).__name__}).") from None
    if payload is None:
        payload = response
    return _plain(payload)


def _to_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise CandleDataError(f"Candle field {name} must be an integer.")
    try:
        number = int(value)
    except (ValueError, TypeError, OverflowError):
        raise CandleDataError(f"Candle field {name} is missing or invalid.") from None
    # Do not silently accept a non-integral numeric string such as "12.5".
    try:
        if Decimal(str(value)) != Decimal(number):
            raise CandleDataError(f"Candle field {name} must be an integer.")
    except InvalidOperation:
        raise CandleDataError(f"Candle field {name} is missing or invalid.") from None
    return number


def _to_decimal(value: Any, name: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise CandleDataError(f"Candle field {name} is missing or invalid.") from None
    if not number.is_finite():
        raise CandleDataError(f"Candle field {name} must be finite.")
    return number


def utc_iso(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Candle:
    """One normalized candle with prices retained as Decimal values."""

    symbol: str
    interval: str
    open_time_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    close_time_ms: int
    quote_asset_volume: Decimal
    number_of_trades: int
    taker_buy_base_volume: Decimal
    taker_buy_quote_volume: Decimal

    @property
    def open_time_utc(self) -> str:
        return utc_iso(self.open_time_ms)

    @property
    def close_time_utc(self) -> str:
        return utc_iso(self.close_time_ms)


@dataclass(frozen=True)
class CandleGap:
    """Unexpected spacing between adjacent candles."""

    previous_open_time_ms: int
    next_open_time_ms: int
    expected_next_open_time_ms: int
    missing_candle_count: int


@dataclass(frozen=True)
class CandleBatch:
    """Validated closed candles plus explicit data-quality metadata."""

    symbol: str
    interval: str
    requested_limit: int
    received_count: int
    candles: tuple[Candle, ...]
    incomplete_candles_dropped: int
    gaps: tuple[CandleGap, ...]
    is_stale: bool
    latest_candle_age_ms: int | None
    as_of_time_ms: int

    @property
    def is_usable(self) -> bool:
        return bool(self.candles) and not self.gaps and not self.is_stale


_RAW_ALIASES: dict[str, tuple[str, ...]] = {
    "open_time_ms": ("open_time_ms", "open_time", "openTime", "t"),
    "open": ("open", "o"),
    "high": ("high", "h"),
    "low": ("low", "l"),
    "close": ("close", "c"),
    "volume": ("volume", "v"),
    "close_time_ms": ("close_time_ms", "close_time", "closeTime", "T"),
    "quote_asset_volume": ("quote_asset_volume", "quoteAssetVolume", "q"),
    "number_of_trades": ("number_of_trades", "numberOfTrades", "n"),
    "taker_buy_base_volume": ("taker_buy_base_volume", "takerBuyBaseAssetVolume", "V"),
    "taker_buy_quote_volume": ("taker_buy_quote_volume", "takerBuyQuoteAssetVolume", "Q"),
}


def parse_candle_row(row: Any, symbol: str, interval: str) -> Candle:
    """Parse one Binance REST array row, mapping, or SDK model into a Candle."""
    row = _plain(row)
    if isinstance(row, Sequence) and not isinstance(row, (str, bytes, bytearray, Mapping)):
        if len(row) < 12:
            raise CandleDataError("Kline array has fewer than 12 required fields.")
        values = {
            "open_time_ms": row[0], "open": row[1], "high": row[2], "low": row[3],
            "close": row[4], "volume": row[5], "close_time_ms": row[6],
            "quote_asset_volume": row[7], "number_of_trades": row[8],
            "taker_buy_base_volume": row[9], "taker_buy_quote_volume": row[10],
        }
    else:
        values = {key: _field(row, *aliases, default=None) for key, aliases in _RAW_ALIASES.items()}

    open_time = _to_int(values["open_time_ms"], "open_time")
    close_time = _to_int(values["close_time_ms"], "close_time")
    if open_time < 0 or close_time < open_time:
        raise CandleDataError("Candle timestamps are invalid or inconsistent.")

    opening = _to_decimal(values["open"], "open")
    high = _to_decimal(values["high"], "high")
    low = _to_decimal(values["low"], "low")
    closing = _to_decimal(values["close"], "close")
    volume = _to_decimal(values["volume"], "volume")
    quote_volume = _to_decimal(values["quote_asset_volume"], "quote_asset_volume")
    taker_base = _to_decimal(values["taker_buy_base_volume"], "taker_buy_base_volume")
    taker_quote = _to_decimal(values["taker_buy_quote_volume"], "taker_buy_quote_volume")
    trades = _to_int(values["number_of_trades"], "number_of_trades")

    if min(opening, high, low, closing) <= 0:
        raise CandleDataError("Candle prices must be greater than zero.")
    if high < max(opening, closing, low) or low > min(opening, closing, high):
        raise CandleDataError("Candle OHLC values are inconsistent.")
    if min(volume, quote_volume, taker_base, taker_quote) < 0 or trades < 0:
        raise CandleDataError("Candle volumes and trade count cannot be negative.")

    return Candle(
        symbol=symbol,
        interval=interval,
        open_time_ms=open_time,
        open=opening,
        high=high,
        low=low,
        close=closing,
        volume=volume,
        close_time_ms=close_time,
        quote_asset_volume=quote_volume,
        number_of_trades=trades,
        taker_buy_base_volume=taker_base,
        taker_buy_quote_volume=taker_quote,
    )


def _next_open_time_ms(open_time_ms: int, interval: str) -> int:
    duration = INTERVAL_MILLISECONDS[interval]
    if duration is not None:
        return open_time_ms + duration
    current = datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc)
    next_month_index = current.year * 12 + (current.month - 1) + 1
    next_year, zero_based_month = divmod(next_month_index, 12)
    next_month = zero_based_month + 1
    next_day = min(current.day, calendar.monthrange(next_year, next_month)[1])
    next_dt = current.replace(year=next_year, month=next_month, day=next_day)
    return int(next_dt.timestamp() * 1000)


def _detect_gaps(candles: Sequence[Candle], interval: str) -> tuple[CandleGap, ...]:
    gaps: list[CandleGap] = []
    for previous, following in zip(candles, candles[1:]):
        expected = _next_open_time_ms(previous.open_time_ms, interval)
        if following.open_time_ms != expected:
            duration = INTERVAL_MILLISECONDS[interval]
            if duration is not None and (following.open_time_ms - previous.open_time_ms) % duration == 0:
                missing = max(0, (following.open_time_ms - previous.open_time_ms) // duration - 1)
            elif interval == "1M":
                prev_dt = datetime.fromtimestamp(previous.open_time_ms / 1000, tz=timezone.utc)
                next_dt = datetime.fromtimestamp(following.open_time_ms / 1000, tz=timezone.utc)
                month_delta = (next_dt.year - prev_dt.year) * 12 + next_dt.month - prev_dt.month
                missing = max(0, month_delta - 1)
            else:
                missing = 0
            gaps.append(CandleGap(previous.open_time_ms, following.open_time_ms, expected, missing))
    return tuple(gaps)


def _extract_rows(payload: Any) -> list[Any]:
    payload = _plain(payload)
    if isinstance(payload, Mapping):
        for key in ("root", "__root__", "data", "klines", "items"):
            if key in payload and isinstance(payload[key], (list, tuple)):
                payload = payload[key]
                break
    if not isinstance(payload, (list, tuple)):
        raise CandleDataError("Binance returned an unexpected candle response shape.")
    return list(payload)


def _resolve_interval_enum(interval: str, enum_type: Any) -> Any:
    """Resolve by enum value rather than relying on generated member spelling."""
    for member in enum_type:
        if str(getattr(member, "value", member)) == interval:
            return member
    raise CandleDataError(f"The installed Binance SDK does not support interval {interval}.")


class BinanceSpotCandleClient:
    """Retrieve and validate public Spot candles using the official Binance SDK."""

    def __init__(self, sdk_client: Any | None = None, interval_enum_type: Any | None = None) -> None:
        self._sdk_client = sdk_client if sdk_client is not None else create_public_spot_client()
        self._interval_enum_type = interval_enum_type

    def server_time_ms(self) -> int:
        """Read exchange UTC time; fail closed if the clock cannot be verified."""
        try:
            response = self._sdk_client.rest_api.time()
            payload = _unwrap_response(response)
            server_time = _field(payload, "serverTime", "server_time", "server_time_ms")
            if server_time is None:
                raise CandleDataError("Binance server-time response omitted its timestamp.")
            return _to_int(server_time, "server_time")
        except CandleDataError:
            raise
        except Exception as exc:
            raise CandleDataError(f"Binance server-time request failed ({type(exc).__name__}).") from None

    def fetch_closed_candles(
        self,
        symbol: str,
        interval: str = "5m",
        limit: int = 100,
        *,
        as_of_time_ms: int | None = None,
        max_staleness_intervals: int = 2,
    ) -> CandleBatch:
        symbol = str(symbol).strip().upper()
        if not _SYMBOL_PATTERN.fullmatch(symbol):
            raise ValueError("Symbol must use exchange format such as BTCUSDT.")
        if interval not in INTERVAL_MILLISECONDS:
            raise ValueError(f"Unsupported interval {interval!r}; supported: {', '.join(INTERVAL_MILLISECONDS)}")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_KLINE_LIMIT:
            raise ValueError(f"limit must be an integer from 1 to {MAX_KLINE_LIMIT}.")
        if isinstance(max_staleness_intervals, bool) or not isinstance(max_staleness_intervals, int) or not 1 <= max_staleness_intervals <= 10:
            raise ValueError("max_staleness_intervals must be an integer between 1 and 10.")

        try:
            if self._interval_enum_type is None:
                from binance_sdk_spot.rest_api.models import KlinesIntervalEnum
                enum_type = KlinesIntervalEnum
            else:
                enum_type = self._interval_enum_type
            interval_enum = _resolve_interval_enum(interval, enum_type)
            response = self._sdk_client.rest_api.klines(symbol=symbol, interval=interval_enum, limit=limit)
            rows = _extract_rows(_unwrap_response(response))
        except CandleDataError:
            raise
        except Exception as exc:
            # Do not include arbitrary exception text or response bodies in user-facing errors.
            raise CandleDataError(f"Binance kline request failed for {symbol} {interval} ({type(exc).__name__}).") from None

        parsed = [parse_candle_row(row, symbol, interval) for row in rows]
        for previous, current in zip(parsed, parsed[1:]):
            if current.open_time_ms == previous.open_time_ms:
                raise CandleDataError("Binance returned duplicate candle open timestamps.")
            if current.open_time_ms < previous.open_time_ms:
                raise CandleDataError("Binance returned candles out of chronological order.")

        as_of = as_of_time_ms if as_of_time_ms is not None else self.server_time_ms()
        if isinstance(as_of, bool) or not isinstance(as_of, int) or as_of < 0:
            raise ValueError("as_of_time_ms must be a non-negative integer timestamp in milliseconds.")
        closed = tuple(candle for candle in parsed if candle.close_time_ms < as_of)
        incomplete = len(parsed) - len(closed)
        gaps = _detect_gaps(closed, interval)
        latest_age = as_of - closed[-1].close_time_ms if closed else None
        interval_duration = INTERVAL_MILLISECONDS[interval] or 31 * 86_400_000
        stale = latest_age is None or latest_age > interval_duration * max_staleness_intervals

        return CandleBatch(
            symbol=symbol,
            interval=interval,
            requested_limit=limit,
            received_count=len(parsed),
            candles=closed,
            incomplete_candles_dropped=incomplete,
            gaps=gaps,
            is_stale=stale,
            latest_candle_age_ms=latest_age,
            as_of_time_ms=as_of,
        )