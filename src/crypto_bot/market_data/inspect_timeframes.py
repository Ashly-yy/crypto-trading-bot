"""Read-only live-data check for supported multi-timeframe alignment."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from crypto_bot.config.settings import get_settings
from crypto_bot.market_data.binance_spot import (
    BinanceMarketDataError,
    BinanceSpotMarketDiscoveryClient,
    create_public_spot_client,
)
from crypto_bot.market_data.candles import BinanceSpotCandleClient, CandleDataError, utc_iso
from crypto_bot.market_data.timeframe_alignment import align_entry_to_context, configured_strategy_timeframes


def _utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch public closed candles and verify causal multi-timeframe alignment; no trading."
    )
    parser.add_argument("--symbol", default="BTCUSDT", help="One configured symbol to inspect (default BTCUSDT).")
    parser.add_argument("--limit", type=int, default=100, help="Closed-candle history per interval (20-1000; default 100).")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    settings = get_settings()
    symbol = args.symbol.strip().upper()
    if symbol not in settings.market_symbol_list or symbol in settings.excluded_market_symbol_list:
        print("Refusing an unconfigured or locally excluded symbol; no market request made.")
        return 2
    if isinstance(args.limit, bool) or not 20 <= args.limit <= 1000:
        print("--limit must be between 20 and 1000; no market request made.")
        return 2

    sdk_client = create_public_spot_client()
    try:
        discovery = BinanceSpotMarketDiscoveryClient(
            symbols=(symbol,), quote_asset=settings.market_quote_asset,
            excluded_symbols=settings.excluded_market_symbol_list, sdk_client=sdk_client,
        ).discover()[0]
        if not discovery.eligible:
            print(f"Market eligibility check failed safely: {discovery.reason}")
            return 2
        candle_client = BinanceSpotCandleClient(sdk_client=sdk_client)
        as_of_time_ms = candle_client.server_time_ms()
        batches = {}
        failed: list[str] = []
        print("\nBINANCE SPOT MULTI-TIMEFRAME ALIGNMENT CHECK")
        print("Source: public Spot REST candle data; authentication: none")
        print(f"Symbol: {symbol}; common exchange-time snapshot: {_utc(as_of_time_ms)}")
        print(f"Requested intervals: {', '.join(settings.supported_candle_interval_list)}")
        print("Only candles closed before the common snapshot are eligible.")

        for interval in settings.supported_candle_interval_list:
            batch = candle_client.fetch_closed_candles(
                symbol, interval, args.limit, as_of_time_ms=as_of_time_ms,
                max_staleness_intervals=2,
            )
            batches[interval] = batch
            status = "USABLE" if batch.is_usable else "UNSAFE"
            print(
                f"\n{interval}: {status}; closed={len(batch.candles)}; "
                f"incomplete_dropped={batch.incomplete_candles_dropped}; "
                f"gaps={len(batch.gaps)}; stale={batch.is_stale}; "
                f"latest_age_ms={batch.latest_candle_age_ms}"
            )
            if not batch.is_usable:
                failed.append(interval)
            elif batch.candles:
                print(f"  latest closed open (UTC): {batch.candles[-1].open_time_utc}")

        if failed:
            print(f"\nAlignment not certified; unsafe interval(s): {', '.join(failed)}. No strategy decisions were made.")
            return 2

        print("\nCONFIGURED STRATEGY TIMEFRAME ALIGNMENT (CONFIGURATION ONLY; NO SIGNALS)")
        mappings = configured_strategy_timeframes(settings)
        for style, mapping in mappings.items():
            context_map = {
                interval: batches[interval].candles
                for interval in mapping.context_intervals
            }
            aligned = align_entry_to_context(
                batches[mapping.entry_interval].candles,
                context_map,
                as_of_time_ms=as_of_time_ms,
            )
            latest = aligned[-1] if aligned else None
            print(
                f"{style.upper()}: entry={mapping.entry_interval}; "
                f"contexts={','.join(mapping.context_intervals)}; "
                f"aligned_rows={len(aligned)}"
            )
            if latest is None:
                print("  Latest row: unavailable; no closed entry candle.")
                continue
            print(f"  Latest decision time (UTC): {_utc(latest.decision_time_ms)}")
            for interval, context in latest.context_candles.items():
                if context is None:
                    print(f"  {interval}: NO PREVIOUS CLOSED CONTEXT (must not trade on this context)")
                else:
                    print(f"  {interval}: open={context.open_time_utc}; close={context.close_time_utc}")
                    if context.close_time_ms >= latest.decision_time_ms:
                        raise RuntimeError("Internal causal alignment invariant failed.")

        print("\nAlignment check complete. All selected context timestamps precede their decision timestamps.")
        print("Public market data only. No account data, credentials, signals, or orders were used.")
        return 0
    except (BinanceMarketDataError, CandleDataError, ValueError) as exc:
        print(f"Multi-timeframe check failed safely ({type(exc).__name__}): {str(exc).splitlines()[0][:200]}")
        print("No trade decision or order was made.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())