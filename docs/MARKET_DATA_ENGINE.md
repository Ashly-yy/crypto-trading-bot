# Market Data Engine — Step 6

## Scope

This step adds a read-only Binance Spot market-data layer. It does not query account endpoints, load API keys, calculate strategy signals, or place orders.

## REST candle workflow

1. Discover the configured markets and require explicit Spot eligibility.
2. Fetch exchange server time from the public `/api/v3/time` endpoint.
3. Request OHLCV candles through the official Binance Spot SDK `klines` endpoint.
4. Normalize array responses and SDK response models into immutable `Candle` records. Prices and volume are retained as `Decimal` values.
5. Reject invalid prices, negative volumes, inconsistent OHLC, duplicate open times, and out-of-order candles.
6. Drop candles whose inclusive close timestamp has not passed at the captured exchange server time.
7. Detect discontinuities between adjacent closed candles and mark the batch unusable if any gap or irregular spacing is found.
8. Flag stale results. The default staleness threshold is two intervals, configurable on the CLI.
9. Only report a batch as `USABLE` if it contains closed candles and has neither gaps nor stale data.

Binance's Spot REST kline endpoint returns at most 1,000 records per request; supported intervals are validated locally. See the official [Spot REST API documentation](https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md).

## REST command

From the project root:

```powershell
.\.venv\Scripts\python.exe -m crypto_bot.market_data.fetch_candles
```

Options:

```powershell
.\.venv\Scripts\python.exe -m crypto_bot.market_data.fetch_candles --symbol BTCUSDT --interval 5m --limit 100
.\.venv\Scripts\python.exe -m crypto_bot.market_data.fetch_candles --symbol BTCUSDT --symbol ETHUSDT --interval 1h --limit 200
```

Symbols must already be present in `MARKET_SYMBOLS`. REST calls are public and do not require keys. The process exits non-zero if market eligibility or candle quality cannot be established.

## WebSocket workflow

The short stream observer opens a public Spot kline stream for one configured symbol. Forming updates are displayed as provisional only. An update is counted as a closed candle only when Binance sets the kline `x` field to `true`. It rejects stale/out-of-order event timestamps and warns when it detects missed closures or candle gaps.

```powershell
.\.venv\Scripts\python.exe -m crypto_bot.market_data.stream_klines --symbol BTCUSDT --interval 1m --seconds 20
```

The observer stops after the requested duration, unsubscribes, and closes the connection. If no candle closes during a short window, that is acceptable: forming updates must not be treated as complete candles. No trading strategy consumes these stream events in this step.

See the official [Spot WebSocket Streams documentation](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md) for kline payloads and the `x` candle-closed field.

## Safety and limitations

- Public market-data requests only; no API key or secret is loaded.
- No account endpoints, orders, account balances, or trading modes are changed.
- REST candle times and incomplete-candle exclusion use Binance server time, not the Windows clock.
- A detected gap, stale batch, malformed candle, unexpected response model, or network error makes data unusable rather than filling or guessing missing values.
- WebSocket reconnect/recovery orchestration, multi-timeframe coordination, caching/persistence, and strategy consumption are later tasks.
- A clean test result proves the validation logic for tested cases; it does not prove strategy profitability.
## WebSocket transport decision after live diagnostic

A direct diagnostic successfully connected to Binance's documented public Spot raw kline endpoint and received valid `BTCUSDT` `1m` updates. The app's SDK-backed stream observer nevertheless returned zero messages. To remove that failing abstraction instead of adding another callback workaround, `stream_klines.py` now uses `aiohttp` directly for the public raw market stream at `wss://stream.binance.com:9443/ws/<lowercase-symbol>@kline_<interval>`. The official Binance Spot SDK remains in use for REST market discovery and historical candles. `aiohttp` is declared as a direct dependency because the stream module imports it directly.

The direct stream remains read-only and unauthenticated. It validates decoded payloads with the same `KlineStreamMonitor`, responds to protocol pings through the WebSocket client's standard autoping, and reports concise sanitized connection errors. It is a short observation utility, not yet a resilient long-running streaming service; reconnect orchestration and persistence are later tasks.
## Step 7 â€” Multi-timeframe alignment

The application supports the configurable, case-sensitive intervals `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, and `1d`. The settings file exposes independent entry and higher-timeframe context mappings for scalp, day, and swing research. Initial mappings are starter defaults only and are not claims of strategy profitability.

`crypto_bot.market_data.timeframe_alignment.align_entry_to_context` performs a causal timestamp join. For each entry candle, its decision timestamp is `entry.close_time_ms + 1` because Binance's candle close timestamps are inclusive. A context candle is eligible only when `context.close_time_ms < decision_time_ms`. Missing context is represented explicitly as `None`; it must not be interpreted as a positive signal. Live-style callers can pass a common `as_of_time_ms` to exclude entry candles not closed before that snapshot. Historical use evaluates each entry candle at its own simulated close and never selects later context rows.

Run `python -m crypto_bot.market_data.inspect_timeframes --symbol BTCUSDT --limit 100` to validate live public candles and inspect the current alignment for the configured mappings. This is a read-only request; it does not generate a trade signal or submit orders. If any interval is stale, has gaps, or otherwise fails validation, the command refuses to certify alignment.
