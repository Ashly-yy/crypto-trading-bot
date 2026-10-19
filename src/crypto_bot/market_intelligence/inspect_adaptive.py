"""Read-only cross-timeframe adaptive-context inspection using public Spot data."""
from __future__ import annotations

import argparse
import logging

from crypto_bot.config.settings import get_settings
from crypto_bot.config.timeframe_config import validate_single_interval
from crypto_bot.market_data.binance_spot import (
    BinanceMarketDataError,
    BinanceSpotMarketDiscoveryClient,
    create_public_spot_client,
)
from crypto_bot.market_data.candles import BinanceSpotCandleClient, CandleDataError, utc_iso
from crypto_bot.market_data.timeframe_alignment import configured_strategy_timeframes
from crypto_bot.market_intelligence.adaptive import AdaptiveStatus, assess_adaptive_market
from crypto_bot.market_intelligence.external_sources import fetch_external_intelligence
from crypto_bot.market_intelligence.regime import assess_market_regime, required_candle_count


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect adaptive multi-timeframe market context; no signals/orders are generated."
    )
    parser.add_argument("--symbol", default="BTCUSDT", help="Configured Spot watchlist symbol; default BTCUSDT.")
    parser.add_argument("--style", choices=("scalp", "day", "swing"), default="scalp", help="Timeframe mapping to assess.")
    parser.add_argument("--limit", type=int, default=100, help="Closed candles per timeframe, 41-1000; default 100.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    settings = get_settings()
    symbol = args.symbol.strip().upper()
    if symbol not in settings.market_symbol_list:
        print("Refusing symbol outside the configured market watchlist; no request was made.")
        return 2
    minimum = required_candle_count()
    if isinstance(args.limit, bool) or not isinstance(args.limit, int) or not minimum <= args.limit <= 1000:
        print(f"Invalid candle limit. Choose {minimum}-1000; no request was made.")
        return 2

    mapping = configured_strategy_timeframes(settings).get(args.style)
    if mapping is None:
        print(f"No configured timeframe mapping exists for {args.style}; no request was made.")
        return 2
    intervals = (mapping.entry_interval, *mapping.context_intervals)
    try:
        checked_intervals = tuple(validate_single_interval(item, field_name="adaptive intervals") for item in intervals)
    except ValueError as exc:
        print(f"Invalid configured timeframe mapping: {exc}")
        return 2
    not_enabled = [item for item in checked_intervals if item not in settings.supported_candle_interval_list]
    if not_enabled:
        print(f"Required timeframe(s) are not enabled in SUPPORTED_CANDLE_INTERVALS: {', '.join(not_enabled)}")
        return 2

    print("ADAPTIVE MARKET CONTEXT INSPECTION")
    print("Purpose: explainable context and exposure haircuts only; not a trading signal or order authorization.")
    print(f"Symbol: {symbol}; style mapping: {args.style}; intervals: {', '.join(checked_intervals)}")
    print("Source: unauthenticated public Binance Spot candles; optional public market/sentiment context.")
    print(f"Minimum regime history: {minimum}; requested candles per timeframe: {args.limit}\n")

    try:
        sdk_client = create_public_spot_client()
        discovery = BinanceSpotMarketDiscoveryClient(
            symbols=settings.market_symbol_list,
            quote_asset=settings.market_quote_asset,
            excluded_symbols=settings.excluded_market_symbol_list,
            sdk_client=sdk_client,
        )
        eligibility = {item.requested_symbol: item for item in discovery.discover()}.get(symbol)
        if eligibility is None or not eligibility.eligible:
            reason = eligibility.reason if eligibility else "symbol not returned by public market discovery"
            print(f"{symbol}: BLOCKED — not confirmed eligible for Spot ({reason}).")
            return 2
        candle_client = BinanceSpotCandleClient(sdk_client=sdk_client)
        as_of_ms = candle_client.server_time_ms()
        regime_by_interval = {}
        for interval in checked_intervals:
            batch = candle_client.fetch_closed_candles(
                symbol,
                interval=interval,
                limit=args.limit,
                as_of_time_ms=as_of_ms,
                max_staleness_intervals=2,
            )
            regime = assess_market_regime(batch)
            regime_by_interval[interval] = regime
            print(
                f"{interval}: {regime.regime.value}; volatility={regime.volatility.value}; "
                f"quality={regime.data_quality.value}; latest close UTC={utc_iso(regime.latest_candle_close_time_ms) if regime.latest_candle_close_time_ms is not None else 'unavailable'}"
            )
        try:
            external = fetch_external_intelligence(tuple(settings.market_symbol_list), timeout_seconds=5.0)
        except (ValueError, TypeError) as exc:
            external = None
            print(f"Optional external context unavailable ({type(exc).__name__}); continuing without it.")
        result = assess_adaptive_market(
            symbol,
            checked_intervals,
            regime_by_interval,
            now_ms=as_of_ms,
            external_snapshot=external,
        )
    except (BinanceMarketDataError, CandleDataError, ValueError) as exc:
        print(f"Adaptive inspection failed safely: {type(exc).__name__}: {exc}")
        return 2
    except Exception as exc:
        logging.getLogger(__name__).exception("Unexpected adaptive inspection failure (%s).", type(exc).__name__)
        print(f"Adaptive inspection failed safely due to {type(exc).__name__}; see local application log.")
        return 2

    print(f"\nAssessment UTC: {utc_iso(result.assessment_time_ms)}")
    print(f"Status: {result.status.value.upper()}")
    print(f"Evidence-consistency score: {result.evidence_score}/100 (not a probability of profit)")
    print(f"Research exposure multiplier for an otherwise valid candidate: {result.risk_multiplier:.3f}")
    print(f"Eligible for provisional sizing: {result.eligible_for_sizing}")
    print(f"External breadth status: {result.breadth_status}")
    if result.sentiment_value is not None:
        print(f"Fear & Greed: {result.sentiment_value}/100 ({result.sentiment_label})")
    if result.bitcoin_dominance_pct is not None:
        print(f"Provider-reported Bitcoin dominance: {result.bitcoin_dominance_pct:.2f}%")
    for explanation in result.explanations:
        print(f"  - {explanation}")
    print("\nNo strategy candidate or size was generated: verified cost estimates and explicit caller-supplied caps are required.")
    print("This tool made no account requests and submits no orders. Live trading settings were not changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())