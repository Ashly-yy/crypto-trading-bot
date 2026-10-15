"""CLI entry point for one-off, read-only Binance Spot market discovery."""

from __future__ import annotations

import logging

from crypto_bot.config.settings import get_settings
from crypto_bot.market_data.binance_spot import (
    BinanceMarketDataError,
    BinanceSpotMarketDiscoveryClient,
)


def main() -> int:
    settings = get_settings()
    logger = logging.getLogger("crypto_bot.market_discovery")
    logger.info("Starting public Binance Spot market discovery; no credentials are used.")

    client = BinanceSpotMarketDiscoveryClient(
        symbols=settings.market_symbol_list,
        quote_asset=settings.market_quote_asset,
        excluded_symbols=settings.excluded_market_symbol_list,
    )
    try:
        results = client.discover()
    except BinanceMarketDataError as exc:
        print(f"Market discovery failed safely: {exc}")
        return 2

    eligible_count = sum(result.eligible for result in results)
    print("\nBINANCE SPOT MARKET DISCOVERY")
    print("Source: official public Spot REST exchange information")
    print("Authentication: none; no API keys loaded")
    print(f"Configured quote asset: {settings.market_quote_asset}")
    print(f"Checked at (UTC): {client.last_checked_at_utc}")
    print(f"Result: {eligible_count} of {len(results)} configured symbols eligible\n")

    for result in results:
        status = "ELIGIBLE" if result.eligible else "REJECTED"
        print(f"{result.requested_symbol}: {status}")
        print(f"  Reason: {result.reason}")
        if result.eligible:
            print(f"  Base/quote: {result.base_asset}/{result.quote_asset}")
            print(f"  Status: {result.status}; Spot allowed: {result.spot_trading_allowed}")
            print(f"  Filters recorded: {', '.join(result.filter_types)}")

    print("\nRead-only discovery complete. No account data or orders were accessed.")
    return 0 if eligible_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())