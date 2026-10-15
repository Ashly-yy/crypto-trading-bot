"""Read-only market-regime inspection using public Binance Spot closed candles."""

from __future__ import annotations

import argparse
import logging

from crypto_bot.config.settings import get_settings
from crypto_bot.config.timeframe_config import validate_single_interval
from crypto_bot.market_data.candles import BinanceSpotCandleClient, CandleDataError
from crypto_bot.market_intelligence.regime import DataQuality, assess_market_regime, required_candle_count


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect explainable market regime from public Binance Spot candles.")
    parser.add_argument("--symbol", help="One configured symbol, e.g. BTCUSDT. Default: all configured symbols.")
    parser.add_argument("--interval", default="5m", help="Supported configured interval; default: 5m.")
    parser.add_argument("--limit", type=int, default=100, help="Closed-candle history request; default: 100.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    settings = get_settings()
    try:
        interval = validate_single_interval(args.interval, field_name="interval")
        if interval not in settings.supported_candle_interval_list:
            raise ValueError("Requested interval is not enabled in SUPPORTED_CANDLE_INTERVALS.")
        minimum = required_candle_count()
        if isinstance(args.limit, bool) or not minimum <= args.limit <= 1000:
            raise ValueError(f"limit must be between {minimum} and 1000 closed candles for regime assessment.")
        symbols = settings.market_symbol_list
        if args.symbol:
            symbol = args.symbol.strip().upper()
            if symbol not in symbols:
                raise ValueError("symbol must be in the configured market watchlist.")
            symbols = (symbol,)
    except ValueError as exc:
        print(f"Invalid request: {exc}")
        return 2

    print("EXPLAINABLE MARKET REGIME INSPECTION")
    print("Source: public Binance Spot closed candles; authentication: none")
    print(f"Interval: {interval}; history: {args.limit}; minimum required: {required_candle_count()} candles")
    print("Thresholds are deterministic research baselines, not validated trading rules or buy/sell signals.")
    client = BinanceSpotCandleClient()
    failed = False
    for symbol in symbols:
        try:
            batch = client.fetch_closed_candles(symbol, interval=interval, limit=args.limit)
            assessment = assess_market_regime(batch)
            print(f"\n{symbol}: {assessment.regime.value.upper()}")
            print(f"  Volatility: {assessment.volatility.value}; volume activity: {assessment.volume_activity.value}")
            print(f"  Data quality: {assessment.data_quality.value}; closed candles: {assessment.closed_candle_count}")
            print(f"  Last closed candle UTC ms: {assessment.latest_candle_close_time_ms}; assessment UTC ms: {assessment.assessment_time_ms}")
            if assessment.trend_separation_pct is not None:
                print(f"  Normalized EMA separation: {assessment.trend_separation_pct * 100:.4f}%")
            if assessment.volatility_ratio is not None:
                print(f"  Relative realized-volatility ratio: {assessment.volatility_ratio:.3f}")
            print(f"  Liquidity: {assessment.liquidity_assessment}")
            for explanation in assessment.explanations:
                print(f"  - {explanation}")
            if assessment.data_quality not in (DataQuality.USABLE, DataQuality.FRESHNESS_UNVERIFIED):
                failed = True
        except (CandleDataError, ValueError) as exc:
            print(f"\n{symbol}: BLOCKED â€” {exc}")
            failed = True
        except Exception as exc:
            logging.getLogger(__name__).exception("Unexpected regime inspection failure for %s (%s).", symbol, type(exc).__name__)
            print(f"\n{symbol}: BLOCKED â€” unexpected {type(exc).__name__}; see local application log.")
            failed = True
    print("\nRegime inspection finished. No signals, account endpoints, or orders were used.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())