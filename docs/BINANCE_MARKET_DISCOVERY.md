# Binance Spot Market Discovery â€” Step 5

## Purpose

The market-discovery adapter retrieves public Binance Spot exchange information and validates the configured market allowlist. The initial allowlist preserves the original project universe: `BTCUSDT`, `ETHUSDT`, and `SOLUSDT`.

## Public-only and read-only boundary

- Uses the official `binance-sdk-spot` package and the public Spot REST `exchange_info` endpoint.
- Does not read API keys or secrets and does not create account clients.
- Does not query balances, account data, open orders, or user trades.
- Does not submit, cancel, or amend orders.
- Does not access Futures or Margin APIs.
- Makes one public exchange-information request per invocation; this is an on-demand diagnostic, not a polling loop.

## Eligibility rules

A configured symbol is marked eligible only if it appears in Binance exchange information, its status is `TRADING`, the response explicitly permits Spot trading, the quote asset matches `MARKET_QUOTE_ASSET`, any available permission metadata does not exclude `SPOT`, and non-empty filter metadata is present. An explicit local exclusion always rejects the symbol. Missing or ambiguous metadata fails closed.

## Configuration

`MARKET_SYMBOLS` is a comma-separated allowlist, initially `BTCUSDT,ETHUSDT,SOLUSDT`.
`MARKET_QUOTE_ASSET` defaults to `USDT`.
`MARKET_EXCLUDED_SYMBOLS` is an optional comma-separated local exclusion list.

Settings validate formatting and duplicates. The exchange remains the source of truth for whether each symbol is currently available for Spot trading.

## Run

From the project root:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m crypto_bot.market_data.discover_markets
```

The command prints normalized public metadata and a reason for any rejection. An API outage, malformed response, or missing market never implies that a symbol is eligible.

## Limits

This step discovers markets and captures public filters for later validation. It does not yet certify that an order quantity is valid, estimate liquidity, download candles, open a WebSocket, or place any order. Order sizing and enforcement of every applicable filter belong to later steps.
## SDK filter normalization regression fix

The official generated SDK models wrap symbol filters in a one-of model; the active filter model is stored in ctual_instance. The adapter must unwrap and normalize these models before reading ilterType and rule fields. An offline regression test covers this response shape. Market eligibility continues to fail closed when filter metadata is actually absent or incomplete.
