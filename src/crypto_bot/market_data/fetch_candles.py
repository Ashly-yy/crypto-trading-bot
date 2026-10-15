"""CLI to fetch and validate closed Binance Spot candles without credentials."""

from __future__ import annotations

import argparse
import logging

from crypto_bot.config.settings import get_settings
from crypto_bot.market_data.binance_spot import BinanceMarketDataError, BinanceSpotMarketDiscoveryClient
from crypto_bot.market_data.candles import BinanceSpotCandleClient, INTERVAL_MILLISECONDS, utc_iso


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only Binance Spot closed-candle validation.")
    parser.add_argument("--symbol", action="append", help="Configured symbol to fetch; can be repeated. Default: all configured symbols.")
    parser.add_argument("--interval", default="5m", choices=tuple(INTERVAL_MILLISECONDS), help="Candle interval (default: 5m).")
    parser.add_argument("--limit", type=int, default=100, help="Candles requested per symbol; range 1-1000 (default: 100).")
    parser.add_argument("--max-staleness-intervals", type=int, default=2, help="Reject latest data older than this number of intervals (default: 2).")
    return parser


def main() -> int:
    settings = get_settings()
    args = build_parser().parse_args()
    selected = tuple(symbol.upper() for symbol in (args.symbol or settings.market_symbol_list))
    configured = set(settings.market_symbol_list)
    unknown = [symbol for symbol in selected if symbol not in configured]
    if unknown:
        print(f"Refusing unconfigured symbol(s): {', '.join(unknown)}. Add them to MARKET_SYMBOLS first.")
        return 2
    if len(set(selected)) != len(selected):
        print("Refusing duplicate symbols in the request.")
        return 2
    if not 1 <= args.limit <= 1000 or not 1 <= args.max_staleness_intervals <= 10:
        print("Invalid limit or staleness setting; no market request was made.")
        return 2

    logging.getLogger("crypto_bot.market_data").info("Starting public, read-only Binance Spot candle retrieval.")
    try:
        # One unauthenticated REST client is shared across metadata, server-time, and candle calls.
        from crypto_bot.market_data.binance_spot import create_public_spot_client
        sdk_client = create_public_spot_client()
        discovery = BinanceSpotMarketDiscoveryClient(
            symbols=settings.market_symbol_list,
            quote_asset=settings.market_quote_asset,
            excluded_symbols=settings.excluded_market_symbol_list,
            sdk_client=sdk_client,
        )
        market_results = {item.requested_symbol: item for item in discovery.discover()}
        candles_client = BinanceSpotCandleClient(sdk_client=sdk_client)
        as_of_ms = candles_client.server_time_ms()
    except BinanceMarketDataError as exc:
        print(f"Market data initialization failed safely: {exc}")
        return 2

    all_usable = True
    print("\nBINANCE SPOT CANDLE VALIDATION")
    print("Source: official public Spot REST API")
    print("Authentication: none; no keys loaded")
    print(f"Interval: {args.interval}; requested limit per symbol: {args.limit}")
    print(f"Exchange server time (UTC): {utc_iso(as_of_ms)}")
    print("Open/incomplete candles are excluded. No account or order endpoints are used.\n")

    for symbol in selected:
        eligibility = market_results.get(symbol)
        if eligibility is None or not eligibility.eligible:
            reason = eligibility.reason if eligibility else "Symbol was not returned by market discovery."
            print(f"{symbol}: SKIPPED — not confirmed eligible for Spot. {reason}\n")
            all_usable = False
            continue
        try:
            batch = candles_client.fetch_closed_candles(
                symbol, args.interval, args.limit,
                as_of_time_ms=as_of_ms,
                max_staleness_intervals=args.max_staleness_intervals,
            )
        except (BinanceMarketDataError, ValueError) as exc:
            print(f"{symbol}: FAILED SAFELY — {exc}\n")
            all_usable = False
            continue

        print(f"{symbol}: {'USABLE' if batch.is_usable else 'UNSAFE — do not use for decisions'}")
        print(f"  Closed candles: {len(batch.candles)} / {batch.received_count} returned")
        print(f"  Incomplete candles skipped: {batch.incomplete_candles_dropped}")
        print(f"  Gaps / irregular spacing: {len(batch.gaps)}")
        print(f"  Latest candle age (ms): {batch.latest_candle_age_ms}")
        print(f"  Stale: {batch.is_stale}")
        if batch.candles:
            latest = batch.candles[-1]
            print(f"  Latest closed candle open (UTC): {latest.open_time_utc}")
            print(f"  Latest OHLC: {latest.open} / {latest.high} / {latest.low} / {latest.close}")
            print(f"  Latest volume: {latest.volume}")
        for gap in batch.gaps[:5]:
            print(
                "  GAP: expected next open {expected}, got {actual}; missing-count estimate={count}".format(
                    expected=utc_iso(gap.expected_next_open_time_ms),
                    actual=utc_iso(gap.next_open_time_ms),
                    count=gap.missing_candle_count,
                )
            )
        print()
        all_usable = all_usable and batch.is_usable

    print("Read-only candle retrieval finished. No account data or orders were accessed.")
    return 0 if all_usable else 2


if __name__ == "__main__":
    raise SystemExit(main())