# Shared Indicator Engine

## Purpose

`src/crypto_bot/indicators/engine.py` is the single deterministic implementation of technical indicators intended for historical testing, paper trading, and future approved Spot execution. It calculates features only; it does not create trading signals or place orders.

## Default configuration

- EMA short/long: 5 / 10
- MACD fast/slow/signal: 12 / 26 / 9
- RSI: 14 periods
- Bollinger Bands: 20 periods and 2 population standard deviations
- Volume SMA: 20 candles, including the current candle

All parameters can be changed through an `IndicatorConfig` object. Keep configuration/version recorded in any future strategy and trade-decision record. Do not silently change parameters during live execution.

## Warm-up

The default full warm-up is 34 candles because the MACD signal line needs 26 slow-EMA seed candles plus 8 further MACD observations. Each snapshot contains `None` for features that have not completed their own warm-up. A strategy must not treat missing values as zero or a valid signal.

## Data and causality

Callers must pass normalized, closed `Candle` objects. The engine rejects mixed symbols/intervals, duplicate or out-of-order timestamps, gaps, invalid OHLC relationships, and non-finite values. It never interpolates missing candles. Each row is calculated only from that row and earlier rows; future rows do not affect earlier snapshots.

Historical and paper code should call the same functions with an explicitly recorded configuration. The engine does not decide whether a market is stale; callers must first apply the relevant `CandleBatch` data-quality policy.

## Formula conventions

- EMA is seeded by the first-period SMA and then updated using alpha `2/(period+1)`.
- MACD is EMA(fast) minus EMA(slow), with an SMA-seeded EMA signal line.
- RSI uses Wilder's smoothed average gains/losses. A completely flat window is assigned RSI 50 by this implementation; zero losses with positive gains gives 100.
- Bollinger Bands use population standard deviation (divide by N).
- Volume SMA includes the current candle to preserve the prototype's simple rolling feature. A strategy needing a strictly prior-volume baseline must explicitly shift the series before comparing.

These conventions are now the canonical implementation for this project; indicator outputs may differ slightly from TA-Lib or `ta` library defaults. Do not combine outputs from different implementations in one backtest.

## Inspection command

From the project root:

```powershell
.\.venv\Scripts\python.exe -m crypto_bot.indicators.inspect_indicators --symbol BTCUSDT --interval 5m --limit 100
```

This command uses public Binance Spot candle data only. It does not read account information or submit orders.

## Limitations

These are analytical features, not validated strategy rules and not proof of a trading edge. Signal thresholds, regime use, entry/exit policy, and portfolio risk belong in later strategy and risk steps.
## Timestamp normalization and input guardrails

Before indicators are calculated, every item must be a normalized `Candle` instance for a single symbol and interval. Timestamp fields must be integer milliseconds (not booleans), open times must be non-negative and aligned to the interval boundary, and close time must equal `open_time_ms + interval_duration_ms - 1`. Candles must then be strictly increasing and evenly spaced. Malformed input is rejected with `IndicatorInputError`; the engine does not repair, sort, or infer candle timestamps.
