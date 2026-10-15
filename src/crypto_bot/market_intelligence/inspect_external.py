"""Read-only inspector for optional free external crypto market context."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from crypto_bot.config.settings import get_settings
from crypto_bot.market_intelligence.external_sources import (
    ExternalIntelligenceSnapshot,
    SourceStatus,
    fetch_external_intelligence,
)


def _utc(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect optional public crypto market and sentiment context; no orders or accounts.")
    parser.add_argument("--symbol", action="append", dest="symbols", help="Configured symbol to include in 24-hour breadth; repeat as needed. Default: configured watchlist.")
    parser.add_argument("--timeout", type=float, default=5.0, help="Per-request timeout in seconds, 0.1 to 15; default 5.")
    return parser


def _print_snapshot(snapshot: ExternalIntelligenceSnapshot) -> None:
    print("OPTIONAL EXTERNAL MARKET INTELLIGENCE")
    print("Purpose: descriptive context only; not a buy/sell signal or a risk override.")
    print("Fear & Greed attribution: Alternative.me â€” https://alternative.me/crypto/fear-and-greed-index/")
    print("Free public endpoints; no API credentials used. Provider limits/availability may change.")

    sentiment = snapshot.sentiment
    if sentiment is None:
        print("\nFear & Greed: UNAVAILABLE")
    else:
        print(f"\nFear & Greed: {sentiment.status.value.upper()}")
        print(f"  Value/classification: {sentiment.value}/100; {sentiment.classification.value}")
        print(f"  Observation UTC: {_utc(sentiment.observed_at_ms)}")
        for item in sentiment.explanations:
            print(f"  - {item}")

    global_market = snapshot.global_market
    if global_market is None:
        print("\nGlobal crypto market context: UNAVAILABLE")
    else:
        print(f"\nGlobal crypto market context: {global_market.status.value.upper()}")
        print(f"  Total market cap (USD): {global_market.total_market_cap_usd:,.0f}")
        print(f"  Reported 24-hour volume (USD): {global_market.total_volume_24h_usd:,.0f}")
        print(f"  Bitcoin market-cap share: {global_market.bitcoin_dominance_pct:.2f}%")
        print(f"  Observation UTC: {_utc(global_market.observed_at_ms)}")
        for item in global_market.explanations:
            print(f"  - {item}")

    breadth = snapshot.market_breadth
    if breadth is None:
        print("\nConfigured-watchlist breadth: UNAVAILABLE")
    else:
        print(f"\nConfigured-watchlist breadth: {breadth.status.value.upper()}")
        print(f"  Symbols observed: {len(breadth.observed_symbols)}/{len(breadth.requested_symbols)}")
        print(f"  24-hour advancing/declining/unchanged: {len(breadth.advancing_symbols)}/{len(breadth.declining_symbols)}/{len(breadth.unchanged_symbols)}")
        print(f"  Advancing: {', '.join(breadth.advancing_symbols) or 'none'}")
        print(f"  Declining: {', '.join(breadth.declining_symbols) or 'none'}")
        print(f"  Unchanged: {', '.join(breadth.unchanged_symbols) or 'none'}")
        for item in breadth.explanations:
            print(f"  - {item}")

    if snapshot.failures:
        print("\nSource failures (other sources remain independent):")
        for failure in snapshot.failures:
            print(f"  - {failure.source}: {failure.category}")
    else:
        print("\nSource errors: none reported.")
    print(f"\nSnapshot retrieved UTC: {_utc(snapshot.retrieved_at_ms)}")
    print(f"Limitation: {snapshot.disclaimer}")
    print("No trading signals, account endpoints, API credentials, or orders were used.")


def main() -> int:
    args = build_parser().parse_args()
    try:
        settings = get_settings()
        configured = tuple(settings.market_symbol_list)
        if args.symbols:
            requested = tuple(item.strip().upper() for item in args.symbols)
            if not requested or any(item not in configured for item in requested):
                print("Invalid request: every --symbol must be present in the configured market watchlist.")
                return 2
            if len(set(requested)) != len(requested):
                print("Invalid request: do not repeat the same --symbol.")
                return 2
        else:
            requested = configured
        snapshot = fetch_external_intelligence(requested, timeout_seconds=args.timeout)
    except (ValueError, TypeError) as exc:
        print(f"Invalid configuration or request: {exc}")
        return 2
    _print_snapshot(snapshot)
    # Optional context sources fail independently; a provider outage must not be
    # treated as trading permission, but it also must not crash a read-only report.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())