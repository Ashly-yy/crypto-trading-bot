# Step 11 — Strategy research baselines

## Purpose and safety boundary

This step supplies three independent, deterministic strategy evaluators. They consume normalized closed-candle batches, the shared indicator engine, aligned higher-timeframe batches, a timestamp-matched market-regime assessment, and an explicit cost estimate before proposing a new spot entry. Each strategy configuration is **disabled by default**. A strategy result is advisory; it is not an order, a size decision, or permission to trade. No strategy module imports or calls an execution adapter.

Only `BUY_CANDIDATE`, `EXIT_CANDIDATE`, and `NO_ACTION` exist. `EXIT_CANDIDATE` is for reducing an existing spot holding and cannot open a short. The central portfolio risk manager, order validation, position accounting, deduplication persistence, and final permission gates remain downstream responsibilities.

## Shared gates

- Inputs must be normalized `CandleBatch` objects marked usable, with no gaps/staleness, correctly aligned fixed-interval timestamps, closed candles only, and sufficient indicator warm-up.
- Entry and context symbols must match. Context candles that close after the entry decision candle are rejected to prevent look-ahead.
- The provided `RegimeAssessment` must be usable, for the same symbol/entry interval, and aligned to the latest entry-candle close.
- A new long entry requires `RegimeLabel.UPWARD_TREND`, all style-specific closed-candle conditions, and a fresh `MarketCostEstimate` containing spread, round-trip fees, and round-trip slippage. Missing, stale, future-dated, or economically inadequate costs reject a new entry; zero costs are never assumed.
- Structural stop is the lowest low over the strategy's recent stop lookback, and target is computed from the configured reward/risk multiple. Levels are reference estimates—not guaranteed fills or protective orders.
- The output decision timestamp is no earlier than the latest entry-candle close plus 1 ms and any entry/context snapshot or regime-assessment timestamp used. The output also contains source timeframes, reasons, strategy version, stop/target references, cost estimate, and a stable key derived from the closed candle. The downstream coordinator must persist consumed keys and resolve cross-strategy overlap.

## Default timeframes

The strategies read the existing Step 7 mappings rather than hard-coding defaults in their evaluators:

| Style | Entry | Context |
|---|---|---|
| Scalp | 1m | 3m, 5m |
| Day | 5m | 15m, 1h |
| Swing | 1h | 4h, 1d |

Deployments may override mappings via the established settings and configuration objects. Context is strictly confirmatory; no incomplete higher-timeframe candle may be used.

## Scalp baseline — version 1.0.0

Entry requires all conditions (AND): a fresh upward EMA-short/EMA-long cross on the entry timeframe; MACD above its signal; RSI between 50 and 68 inclusive; volume above the shared current-inclusive volume average; every configured context timeframe has EMA-short above EMA-long; and the matching regime is `upward_trend`.

An existing spot position produces an exit candidate when the fast EMA crosses below the slow EMA, MACD is below signal while RSI is below 50, RSI reaches 75, or the matching regime turns downward. Initial reference stop uses the lowest low among the last 5 entry candles; target is 1.25R. Entry cost age limit is 60 seconds and gross target movement must be at least 2 times the estimated round-trip cost.

## Day baseline — version 1.0.0

Entry requires all conditions (AND): close crosses from at/below to above the Bollinger middle line; EMA-short is above EMA-long; MACD is above signal; RSI is between 52 and 68 inclusive; volume is above its shared average; all configured intraday context EMA filters are bullish; and the matching regime is `upward_trend`.

An existing spot position produces an exit candidate when close crosses below the Bollinger middle, MACD is below signal with RSI below 45, or the regime turns downward. Initial reference stop uses the lowest low among the last 10 entry candles; target is 1.75R. Cost estimate age limit is 5 minutes and gross target movement must be at least 1.5 times estimated round-trip cost. `DayStrategyConfig.allow_overnight` defaults to `None`; a new-entry candidate is blocked until the user explicitly sets it to `True` or `False`. This setting is policy metadata for the later trading-session coordinator—the evaluator itself does not enforce a session cutoff, and the coordinator must enforce the chosen policy.

## Swing baseline — version 1.0.0

Entry requires all conditions (AND): close is above the highest high of the previous 20 completed entry candles (excluding the current candle); close is above the Bollinger middle; EMA-short is above EMA-long; MACD is above signal; RSI is between 50 and 72 inclusive; all context intervals pass both EMA and MACD bullish filters; and the matching regime is `upward_trend`.

An existing spot position produces an exit candidate when close breaks below the prior 20-candle structural low, EMA and MACD are both bearish, or the regime turns downward. Initial reference stop uses the lowest low across the last 20 entry candles; target is 2R. Cost estimate age limit is one hour and gross target movement must be at least 1.25 times estimated round-trip cost.

## Research assumptions, trade-offs, and limitations

All numerical thresholds above are explainable starting baselines, not calibrated or profitable settings. Strict AND rules and upward-regime gating reduce the number of candidates and may miss trades; this is intentional for a conservative initial implementation. EMA/MACD/RSI/Bollinger and volume are not independent evidence sources and can be correlated. Breakout/exit rules may produce false positives and are not a substitute for chronological backtesting, walk-forward analysis, paper fill models, fee modelling, and review per asset/regime.

The evaluator needs a caller-supplied cost estimate. Step 10's free cross-market indicators do not by themselves supply reliable pair-specific spread, round-trip fees, and slippage. Until a validated provider supplies those values, strategies fail closed for new entries. The engine makes no claim about live liquidity, actual fills, win rate, or profitability. It does not enforce position limits; those must be applied later by the shared central risk manager.

Each strategy must be assessed in isolation before any combined candidate selector is introduced. A later coordinator must arbitrate conflicting signals, shared asset exposure, duplicated opportunity keys, daily limits, and correlated portfolio risk; three strategies may not independently authorize three positions in one asset.