"""Read-only inspection of the standardized indicator engine on public Spot candles."""

from __future__ import annotations

import argparse
import logging
import sys

from crypto_bot.config.settings import get_settings
from crypto_bot.indicators.engine import calculate_latest_indicators, required_warmup_candles
from crypto_bot.market_data.candles import BinanceSpotCandleClient, CandleDataError
from crypto_bot.config.timeframe_config import validate_single_interval


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect shared technical indicators using public Binance Spot candles.")
    parser.add_argument("--symbol", help="One configured symbol, e.g. BTCUSDT. Default: all configured symbols.")
    parser.add_argument("--interval", default="5m", help="Supported interval; default: 5m.")
    parser.add_argument("--limit", type=int, default=100, help="Closed-candle history request, 35-1000; default: 100.")
    return parser


def _fmt(value: float | None) -> str:
    return "WARM-UP / unavailable" if value is None else f"{value:.8f}"


def main() -> int:
    args = build_parser().parse_args()
    settings = get_settings()
    try:
        interval = validate_single_interval(args.interval, field_name="interval")
        if interval not in settings.supported_candle_interval_list:
            raise ValueError("Requested interval is not enabled in SUPPORTED_CANDLE_INTERVALS.")
        if isinstance(args.limit, bool) or not 35 <= args.limit <= 1000:
            raise ValueError("limit must be between 35 and 1000 for this indicator inspection.")
        symbols = settings.market_symbol_list
        if args.symbol:
            symbol = args.symbol.strip().upper()
            if symbol not in symbols:
                raise ValueError("symbol must be in the configured market watchlist.")
            symbols = (symbol,)
    except ValueError as exc:
        print(f"Invalid request: {exc}")
        return 2

    print("SHARED TECHNICAL INDICATOR INSPECTION")
    print("Source: public Binance Spot closed candles; authentication: none")
    print(f"Interval: {interval}; requested history: {args.limit}; expected full warm-up: {required_warmup_candles()} candles")
    print("Indicator configuration is a research baseline, not a trading signal.")

    client = BinanceSpotCandleClient()
    any_failure = False
    for symbol in symbols:
        try:
            batch = client.fetch_closed_candles(symbol, interval=interval, limit=args.limit)
            if not batch.is_usable:
                reasons = []
                if not batch.candles:
                    reasons.append("no validated closed candles")
                if batch.gaps:
                    reasons.append(f"{len(batch.gaps)} candle gap(s)")
                if batch.is_stale:
                    reasons.append("latest candle is stale")
                print(f"\n{symbol}: BLOCKED — {', '.join(reasons) or 'data-quality checks failed'}")
                any_failure = True
                continue
            snapshot = calculate_latest_indicators(batch.candles)
            if snapshot is None:
                print(f"\n{symbol}: BLOCKED — no usable indicator snapshot")
                any_failure = True
                continue
            print(f"\n{symbol}: {'WARMED UP' if snapshot.is_warmed_up else 'WARM-UP INCOMPLETE'}")
            print(f"  Closed candles: {len(batch.candles)}; gaps: {len(batch.gaps)}; stale: {batch.is_stale}")
            print(f"  Latest candle open UTC: {batch.candles[-1].open_time_utc}")
            print(f"  Close: {_fmt(snapshot.close)}")
            print(f"  EMA short/long: {_fmt(snapshot.ema_short)} / {_fmt(snapshot.ema_long)}")
            print(f"  MACD / signal / histogram: {_fmt(snapshot.macd)} / {_fmt(snapshot.macd_signal)} / {_fmt(snapshot.macd_histogram)}")
            print(f"  RSI: {_fmt(snapshot.rsi)}")
            print(f"  Bollinger upper/middle/lower: {_fmt(snapshot.bollinger_upper)} / {_fmt(snapshot.bollinger_middle)} / {_fmt(snapshot.bollinger_lower)}")
            print(f"  Volume SMA / above average: {_fmt(snapshot.volume_sma)} / {snapshot.volume_above_average}")
        except (CandleDataError, ValueError) as exc:
            print(f"\n{symbol}: BLOCKED — {exc}")
            any_failure = True
        except Exception as exc:
            logging.getLogger(__name__).exception("Unexpected indicator inspection failure for %s (%s).", symbol, type(exc).__name__)
            print(f"\n{symbol}: BLOCKED — unexpected {type(exc).__name__}; see local application log.")
            any_failure = True
    print("\nIndicator inspection finished. No signals, account endpoints, or orders were used.")
    return 1 if any_failure else 0


if __name__ == "__main__":
    raise SystemExit(main())