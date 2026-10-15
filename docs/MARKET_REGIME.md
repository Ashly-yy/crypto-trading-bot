# Step 9 â€” Explainable market-regime assessment

## Purpose and boundaries

The regime engine provides a deterministic, timestamped description of a market condition. It is not an entry/exit strategy and does not place orders, change account state, size positions, or override centralized risk controls. It consumes normalized closed candles and Step 8's shared indicator calculations.

## Labels

- **Trend regime:** `upward_trend`, `downward_trend`, `range`, or `uncertain`.
- **Volatility:** `low`, `normal`, `high`, or `unknown`, assessed relative to a rolling median of the same symbol/interval's realized log-return volatility.
- **Volume activity:** `above_average`, `at_or_below_average`, or `unknown`, using the existing current-inclusive volume average definition.
- **Liquidity:** deliberately `not_assessed` in this step. Candle volume alone is not treated as proof of spread or order-book depth.
- **Data quality:** carries usable, freshness-unverified, insufficient, empty, stale, gapped, stale-and-gapped, or invalid status. Stale, gapped, invalid, or insufficient data forces an `uncertain` regime.

## Explainable baseline rules

The initial research baseline confirms upward/downward trend only when normalized short/long EMA separation crosses the configurable threshold, close is on the corresponding side of the long EMA, and the MACD line agrees in direction. A range label requires small EMA separation, a small normalized MACD histogram, and RSI inside the configurable neutral band. Mixed evidence returns `uncertain` rather than forcing a directional label.

Volatility is calculated from closed-candle log returns over a rolling lookback and compared with the median of earlier rolling windows. The default high/low ratios (1.50 and 0.67) and trend/neutral bands are engineering baselines only; they have not been shown profitable or calibrated through backtesting. They must be evaluated in later validation phases before any strategy relies on them.

When inputs include `CandleBatch`, the engine respects stale/gap metadata and records both the assessment timestamp and latest candle close timestamp. A raw candle sequence can be structurally checked but cannot establish freshness; the result explicitly says `freshness_unverified`.

## Multi-timeframe agreement

`summary_timeframe_agreement` compares separately calculated assessments. Consistent non-uncertain labels report `agreed`; directional/range conflict reports `mixed` with an uncertain direction; missing, invalid, or uncertain inputs report `incomplete`. Agreement is context onlyâ€”not additional proof of edge and not a risk override.

## Run read-only inspection

From the project root:

```powershell
.\.venv\Scripts\python.exe -m crypto_bot.market_intelligence.inspect_regime --symbol BTCUSDT --interval 5m --limit 100
```

This fetches public Spot closed candles only. It uses no account endpoints, API credentials, or order execution. If current data quality checks fail, the assessment is `uncertain` and the command reports a blocked state.