"""Fail-safe, read-only adapters for optional public crypto context sources.

No trading decisions are made here. Each source is independent; a failed or stale
provider remains unavailable context and is never synthesized into a buy/sell score.
"""
from __future__ import annotations

import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from enum import Enum
from typing import Any

FNG_URL = "https://api.alternative.me/fng/"
ALTERNATIVE_GLOBAL_URL = "https://api.alternative.me/v2/global/"
BINANCE_PUBLIC_BASE_URL = "https://data-api.binance.vision/api/v3/ticker/24hr"
MAX_RESPONSE_BYTES = 1_000_000
MAX_FNG_AGE_MS = 36 * 60 * 60 * 1000
MAX_GLOBAL_AGE_MS = 6 * 60 * 60 * 1000
MAX_FUTURE_SKEW_MS = 5 * 60 * 1000


class SourceStatus(str, Enum):
    AVAILABLE = "available"
    STALE = "stale"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class SentimentLabel(str, Enum):
    EXTREME_FEAR = "extreme_fear"
    FEAR = "fear"
    NEUTRAL = "neutral"
    GREED = "greed"
    EXTREME_GREED = "extreme_greed"


class ExternalSourceError(ValueError):
    """Malformed, unsafe, or semantically invalid provider response."""


@dataclass(frozen=True)
class FearGreedReading:
    value: int
    classification: SentimentLabel
    observed_at_ms: int
    retrieved_at_ms: int
    status: SourceStatus
    source: str = "Alternative.me Fear & Greed Index"
    explanations: tuple[str, ...] = ()


@dataclass(frozen=True)
class GlobalMarketReading:
    total_market_cap_usd: float
    total_volume_24h_usd: float
    bitcoin_dominance_pct: float
    observed_at_ms: int
    retrieved_at_ms: int
    status: SourceStatus
    source: str = "Alternative.me global crypto market data"
    explanations: tuple[str, ...] = ()


@dataclass(frozen=True)
class Ticker24hReading:
    symbol: str
    price_change_pct_24h: float
    retrieved_at_ms: int
    source: str = "Binance public Spot 24-hour ticker"


@dataclass(frozen=True)
class MarketBreadthReading:
    requested_symbols: tuple[str, ...]
    observed_symbols: tuple[str, ...]
    advancing_symbols: tuple[str, ...]
    declining_symbols: tuple[str, ...]
    unchanged_symbols: tuple[str, ...]
    retrieved_at_ms: int
    status: SourceStatus
    explanations: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceFailure:
    source: str
    category: str


@dataclass(frozen=True)
class ExternalIntelligenceSnapshot:
    sentiment: FearGreedReading | None
    global_market: GlobalMarketReading | None
    market_breadth: MarketBreadthReading | None
    failures: tuple[SourceFailure, ...]
    retrieved_at_ms: int
    disclaimer: str = (
        "Optional context only. This snapshot does not create trading signals, alter sizing, "
        "or override strategy rules, the central risk manager, or the kill switch."
    )


def _as_finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ExternalSourceError(f"{field} must be numeric.")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ExternalSourceError(f"{field} must be numeric.") from exc
    if not math.isfinite(result):
        raise ExternalSourceError(f"{field} must be finite.")
    return result


def _as_timestamp_ms(value: Any, field: str) -> int:
    number = _as_finite_number(value, field)
    # Provider documents Unix timestamps in seconds. This function is used only
    # after parsing those specific fields, so require integral second values.
    if number < 0 or not number.is_integer():
        raise ExternalSourceError(f"{field} must be a non-negative whole-second timestamp.")
    seconds = int(number)
    if seconds > 32_503_680_000:  # year 3000 in seconds; rejects accidental ms values
        raise ExternalSourceError(f"{field} is outside the supported timestamp range.")
    return seconds * 1000


def _validate_observation_time(observed_at_ms: int, retrieved_at_ms: int, max_age_ms: int) -> SourceStatus:
    age = retrieved_at_ms - observed_at_ms
    if age < -MAX_FUTURE_SKEW_MS:
        raise ExternalSourceError("provider timestamp is implausibly far in the future.")
    if age > max_age_ms:
        return SourceStatus.STALE
    return SourceStatus.AVAILABLE


def parse_fear_greed_payload(
    payload: Any, *, retrieved_at_ms: int, max_age_ms: int = MAX_FNG_AGE_MS
) -> FearGreedReading:
    """Parse Alternative.me FNG JSON; retain old readings but mark them stale."""
    if isinstance(retrieved_at_ms, bool) or not isinstance(retrieved_at_ms, int) or retrieved_at_ms < 0:
        raise ExternalSourceError("retrieved_at_ms must be a non-negative integer.")
    if isinstance(max_age_ms, bool) or not isinstance(max_age_ms, int) or max_age_ms < 1:
        raise ExternalSourceError("max_age_ms must be a positive integer.")
    if not isinstance(payload, dict):
        raise ExternalSourceError("Fear & Greed response must be a JSON object.")
    metadata = payload.get("metadata")
    if isinstance(metadata, dict) and metadata.get("error") not in (None, "", False):
        raise ExternalSourceError("provider reported an API error.")
    records = payload.get("data")
    if not isinstance(records, list) or not records or not isinstance(records[0], dict):
        raise ExternalSourceError("Fear & Greed response has no latest observation.")
    record = records[0]
    value_num = _as_finite_number(record.get("value"), "value")
    if not value_num.is_integer() or not 0 <= value_num <= 100:
        raise ExternalSourceError("value must be an integer from 0 to 100.")
    timestamp_ms = _as_timestamp_ms(record.get("timestamp"), "timestamp")
    raw_classification = record.get("value_classification")
    if not isinstance(raw_classification, str):
        raise ExternalSourceError("value_classification must be a string.")
    normalized = re.sub(r"[\s-]+", "_", raw_classification.strip().lower())
    try:
        classification = SentimentLabel(normalized)
    except ValueError as exc:
        raise ExternalSourceError("value_classification is not a recognized FNG label.") from exc
    status = _validate_observation_time(timestamp_ms, retrieved_at_ms, max_age_ms)
    explanation = (
        "Alternative.me Fear & Greed value is a provider-reported sentiment context, not a standalone signal.",
    )
    if status is SourceStatus.STALE:
        explanation += ("Provider observation exceeds the configured freshness window; do not treat it as current.",)
    return FearGreedReading(
        value=int(value_num), classification=classification, observed_at_ms=timestamp_ms,
        retrieved_at_ms=retrieved_at_ms, status=status, explanations=explanation,
    )


def parse_global_market_payload(
    payload: Any, *, retrieved_at_ms: int, max_age_ms: int = MAX_GLOBAL_AGE_MS
) -> GlobalMarketReading:
    """Parse Alternative.me `/v2/global/` USD market summary conservatively."""
    if isinstance(retrieved_at_ms, bool) or not isinstance(retrieved_at_ms, int) or retrieved_at_ms < 0:
        raise ExternalSourceError("retrieved_at_ms must be a non-negative integer.")
    if isinstance(max_age_ms, bool) or not isinstance(max_age_ms, int) or max_age_ms < 1:
        raise ExternalSourceError("max_age_ms must be a positive integer.")
    if not isinstance(payload, dict):
        raise ExternalSourceError("global-market response must be a JSON object.")
    metadata = payload.get("metadata")
    if isinstance(metadata, dict) and metadata.get("error") not in (None, "", False):
        raise ExternalSourceError("provider reported an API error.")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ExternalSourceError("global-market response has no data object.")
    quotes = data.get("quotes")
    usd = quotes.get("USD") if isinstance(quotes, dict) else None
    if not isinstance(usd, dict):
        raise ExternalSourceError("global-market response has no USD quote data.")
    cap = _as_finite_number(usd.get("total_market_cap"), "total_market_cap")
    volume = _as_finite_number(usd.get("total_volume_24h"), "total_volume_24h")
    # Regression guard: Alternative.me dominance ratio/percentage scale drift
    raw_dominance = _as_finite_number(data.get("bitcoin_percentage_of_market_cap"), "bitcoin_percentage_of_market_cap")
    # The published docs describe this field as percent, but the live v2 endpoint
    # was observed returning a ratio (~0.65) while BTC represented ~65% of total cap.
    # Normalize only strict fractional values; leave documented percent values intact.
    dominance_is_fraction = 0.0 < raw_dominance < 1.0
    dominance = raw_dominance * 100.0 if dominance_is_fraction else raw_dominance
    if cap <= 0 or volume < 0 or not 0 <= dominance <= 100:
        raise ExternalSourceError("global-market values are outside valid bounds.")
    timestamp_ms = _as_timestamp_ms(data.get("last_updated"), "last_updated")
    status = _validate_observation_time(timestamp_ms, retrieved_at_ms, max_age_ms)
    explanation = (
        "Global capitalization, reported 24-hour market volume, and Bitcoin dominance are provider estimates.",
        "These values are cross-market context; they do not establish executable liquidity for a trading pair.",
    )
    if dominance_is_fraction:
        explanation += ("Provider returned Bitcoin market share as a fractional ratio; normalized to percent (ratio Ã— 100).",)
    if status is SourceStatus.STALE:
        explanation += ("Provider observation exceeds the configured freshness window; do not treat it as current.",)
    return GlobalMarketReading(
        total_market_cap_usd=cap, total_volume_24h_usd=volume,
        bitcoin_dominance_pct=dominance, observed_at_ms=timestamp_ms,
        retrieved_at_ms=retrieved_at_ms, status=status, explanations=explanation,
    )


def parse_binance_24h_ticker(payload: Any, *, expected_symbol: str, retrieved_at_ms: int) -> Ticker24hReading:
    """Parse one Binance public Spot 24-hour ticker for the requested symbol."""
    symbol = _validate_symbol(expected_symbol)
    if isinstance(retrieved_at_ms, bool) or not isinstance(retrieved_at_ms, int) or retrieved_at_ms < 0:
        raise ExternalSourceError("retrieved_at_ms must be a non-negative integer.")
    if not isinstance(payload, dict):
        raise ExternalSourceError("Binance ticker response must be a JSON object.")
    actual = payload.get("symbol")
    if not isinstance(actual, str) or actual.upper() != symbol:
        raise ExternalSourceError("Binance ticker response symbol does not match the request.")
    change = _as_finite_number(payload.get("priceChangePercent"), "priceChangePercent")
    return Ticker24hReading(symbol=symbol, price_change_pct_24h=change, retrieved_at_ms=retrieved_at_ms)


def summarize_market_breadth(
    requested_symbols: tuple[str, ...] | list[str],
    tickers: tuple[Ticker24hReading, ...] | list[Ticker24hReading],
    *,
    retrieved_at_ms: int,
) -> MarketBreadthReading:
    """Summarize 24-hour advances in the configured watchlist only, never all crypto."""
    requested = tuple(_validate_symbol(symbol) for symbol in requested_symbols)
    if not requested or len(set(requested)) != len(requested):
        raise ExternalSourceError("requested_symbols must be a non-empty unique list.")
    if len(requested) > 50:
        raise ExternalSourceError("at most 50 configured symbols are allowed for a breadth snapshot.")
    if isinstance(retrieved_at_ms, bool) or not isinstance(retrieved_at_ms, int) or retrieved_at_ms < 0:
        raise ExternalSourceError("retrieved_at_ms must be a non-negative integer.")
    by_symbol: dict[str, Ticker24hReading] = {}
    allowed = set(requested)
    for ticker in tickers:
        if not isinstance(ticker, Ticker24hReading) or ticker.symbol not in allowed:
            raise ExternalSourceError("ticker observations must match a requested symbol.")
        if ticker.symbol in by_symbol:
            raise ExternalSourceError("duplicate ticker observation.")
        by_symbol[ticker.symbol] = ticker
    observed = tuple(symbol for symbol in requested if symbol in by_symbol)
    advancing = tuple(symbol for symbol in observed if by_symbol[symbol].price_change_pct_24h > 0)
    declining = tuple(symbol for symbol in observed if by_symbol[symbol].price_change_pct_24h < 0)
    unchanged = tuple(symbol for symbol in observed if by_symbol[symbol].price_change_pct_24h == 0)
    if not observed:
        status = SourceStatus.UNAVAILABLE
        detail = ("No configured-watchlist ticker responses were usable.",)
    elif len(observed) != len(requested):
        status = SourceStatus.PARTIAL
        missing = tuple(symbol for symbol in requested if symbol not in by_symbol)
        detail = (f"Partial watchlist breadth; missing symbols: {', '.join(missing)}.",)
    else:
        status = SourceStatus.AVAILABLE
        detail = ("Breadth covers only the configured watchlist, not the complete crypto market.",)
    return MarketBreadthReading(
        requested_symbols=requested, observed_symbols=observed,
        advancing_symbols=advancing, declining_symbols=declining,
        unchanged_symbols=unchanged, retrieved_at_ms=retrieved_at_ms,
        status=status, explanations=detail,
    )


def _validate_symbol(value: Any) -> str:
    if not isinstance(value, str):
        raise ExternalSourceError("symbol must be a string.")
    symbol = value.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{3,20}", symbol):
        raise ExternalSourceError("symbol contains unsupported characters or has an invalid length.")
    return symbol


def _fetch_json(url: str, *, timeout_seconds: float) -> Any:
    if not math.isfinite(float(timeout_seconds)) or not 0.1 <= float(timeout_seconds) <= 15:
        raise ValueError("timeout_seconds must be between 0.1 and 15 seconds.")
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "crypto-trading-bot-research/0.1"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=float(timeout_seconds)) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ExternalSourceError("provider response exceeds the configured size limit.")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExternalSourceError("provider response was not valid UTF-8 JSON.") from exc


def _fetch_source(name: str, url: str, parse: Any, *, timeout_seconds: float, max_age_ms: int | None = None) -> tuple[Any | None, SourceFailure | None]:
    try:
        payload = _fetch_json(url, timeout_seconds=timeout_seconds)
        retrieved_at_ms = int(time.time() * 1000)
        if max_age_ms is None:
            reading = parse(payload, retrieved_at_ms=retrieved_at_ms)
        else:
            reading = parse(payload, retrieved_at_ms=retrieved_at_ms, max_age_ms=max_age_ms)
        return reading, None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, SourceFailure(name, f"network request failed ({type(exc).__name__})")
    except (ExternalSourceError, ValueError, TypeError, KeyError, OverflowError) as exc:
        return None, SourceFailure(name, f"response rejected ({type(exc).__name__})")
    except Exception as exc:
        # Unexpected per-provider failures remain isolated and never become a
        # fabricated observation or an exception escaping the read-only report.
        return None, SourceFailure(name, f"source processing failed ({type(exc).__name__})")


def fetch_external_intelligence(
    symbols: tuple[str, ...] | list[str], *, timeout_seconds: float = 5.0
) -> ExternalIntelligenceSnapshot:
    """Fetch optional public context independently; failures never raise into trading."""
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(float(timeout_seconds)) or not 0.1 <= float(timeout_seconds) <= 15:
        raise ValueError("timeout_seconds must be between 0.1 and 15 seconds.")
    requested_symbols = tuple(_validate_symbol(symbol) for symbol in symbols)
    if not requested_symbols or len(requested_symbols) > 50 or len(set(requested_symbols)) != len(requested_symbols):
        raise ValueError("symbols must be a non-empty unique list of at most 50 symbols.")
    failures: list[SourceFailure] = []
    sentiment, failure = _fetch_source(
        "Alternative.me Fear & Greed", FNG_URL + "?" + urllib.parse.urlencode({"limit": 1, "format": "json"}),
        parse_fear_greed_payload, timeout_seconds=float(timeout_seconds), max_age_ms=MAX_FNG_AGE_MS,
    )
    if failure:
        failures.append(failure)
    global_market, failure = _fetch_source(
        "Alternative.me global market", ALTERNATIVE_GLOBAL_URL + "?" + urllib.parse.urlencode({"convert": "USD"}),
        parse_global_market_payload, timeout_seconds=float(timeout_seconds), max_age_ms=MAX_GLOBAL_AGE_MS,
    )
    if failure:
        failures.append(failure)
    tickers: list[Ticker24hReading] = []
    for symbol in requested_symbols:
        url = BINANCE_PUBLIC_BASE_URL + "?" + urllib.parse.urlencode({"symbol": symbol})
        reading, failure = _fetch_source(
            f"Binance Spot ticker {symbol}", url,
            lambda payload, retrieved_at_ms, requested=symbol: parse_binance_24h_ticker(
                payload, expected_symbol=requested, retrieved_at_ms=retrieved_at_ms
            ),
            timeout_seconds=float(timeout_seconds),
        )
        if failure:
            failures.append(failure)
        elif reading is not None:
            tickers.append(reading)
    breadth = summarize_market_breadth(requested_symbols, tickers, retrieved_at_ms=int(time.time() * 1000))
    return ExternalIntelligenceSnapshot(
        sentiment=sentiment, global_market=global_market, market_breadth=breadth,
        failures=tuple(failures), retrieved_at_ms=int(time.time() * 1000),
    )