# Step 10 â€” Optional External Market Intelligence

## Purpose and boundaries

This step adds read-only context adapters. It does **not** produce entries/exits, trading signals, strategy selections, or position sizes. It cannot override the Step 9 regime assessment, centralized risk manager, persistent kill switch, or live-trading authorization. These sources are optional; failed or stale provider responses stay explicitly unavailable/stale, and a source failure must not crash the rest of the report or be interpreted as permission to trade.

## Source adapters

1. **Alternative.me Fear & Greed Index** â€” `https://api.alternative.me/fng/?limit=1&format=json`. Provider observations are parsed and timestamped. The initial local freshness limit is 36 hours. The provider's value and classification are descriptive context only. Attribution must be shown whenever this data is displayed: [Alternative.me Fear & Greed Index](https://alternative.me/crypto/fear-and-greed-index/).
### Bitcoin dominance scale normalization

The v2 global endpoint is documented with `bitcoin_percentage_of_market_cap` expressed as a percentage, but live responses observed during Step 10 verification returned a fractional ratio (for example, approximately `0.65` when an independent calculation from the same-time BTC and global market caps yielded approximately `65%`). The parser therefore normalizes values strictly between `0` and `1` by multiplying by `100`, while values of `1` or greater are treated as already expressed in percentage points. Regression tests cover both forms. If the provider changes field semantics again, verify against market-cap values before changing this rule; do not silently present a questionable value as current.
2. **Alternative.me global crypto market summary** â€” `https://api.alternative.me/v2/global/?convert=USD`. Reads total market capitalization, reported 24-hour volume, and Bitcoin's market-cap share. The initial local freshness limit is six hours. These broad estimates do not establish the executable liquidity or spread of a trading pair.
3. **Binance public Spot 24-hour ticker** â€” `https://data-api.binance.vision/api/v3/ticker/24hr?symbol=...`. Computes advances/declines/unchanged only across the configured watchlist. It must never be described as market-wide breadth when only the configured symbols were queried. Binance documents the `data-api.binance.vision` endpoints as public market-data-only and unauthenticated: [Binance market-data-only endpoints](https://github.com/binance/binance-spot-api-docs/blob/master/faqs/market_data_only.md).

No API key is required for these requests. The patch adds no third-party dependency and uses bounded, HTTPS-only, read-only requests with a per-request timeout and maximum response size. Provider availability, response shapes, rate limits, and source terms may change; the source adapters validate response shape and fail safely. Freshness windows are this project's conservative guardrails, not provider guarantees.

## What is reported

- Sentiment's provider value, classification, timestamp, and freshness status.
- Broad-market cap, reported 24-hour volume, Bitcoin dominance, timestamp, and freshness status.
- Configured-watchlist 24-hour advancing/declining/unchanged counts, observed symbols, partial/unavailable status, and explanations.
- Provider failures by source and failure category without printing request payloads or credentials.

There is deliberately no combined sentiment score and no hidden sentiment weighting. Step 10 remains a reporting/context feature until later strategy, data-quality, backtesting, and risk validation explicitly authorize any downstream use.

## Run the read-only inspector

From the project root:

```powershell
.\.venv\Scripts\python.exe -m crypto_bot.market_intelligence.inspect_external
```

Optionally provide one or more configured symbols and a bounded timeout:

```powershell
.\.venv\Scripts\python.exe -m crypto_bot.market_intelligence.inspect_external --symbol BTCUSDT --symbol ETHUSDT --timeout 5
```

The command makes only public GET requests. It does not call authenticated endpoints, read `.env`, use API credentials, touch account data, connect the source data to the trading loop, or place orders. If external sources are unavailable, the command prints unavailable status instead of manufacturing values or trading signals.

## Testing

`tests/unit/test_external_sources.py` tests parsers, timestamp/freshness behavior, invalid responses, ticker symbol matching, bounded inputs, partial watchlist results, unavailable-provider behavior, and the rule that no overall trading signal is constructed. Tests use fixtures/mocks and do not require the network.