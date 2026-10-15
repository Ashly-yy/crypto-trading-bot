# Crypto Trading Bot

A modular Binance Spot-only research and paper-trading application. The project is being built in verified steps; a placeholder folder is not a completed feature.

## Current foundation stage

- Approved requirements: `PROJECT_SPECIFICATION.md`.
- Python configuration is validated with Pydantic Settings.
- Logging uses the Python standard library and a rotating UTF-8 log file.
- Unit tests use pytest.
- No exchange connection, strategy execution, Telegram command handler, or order submission exists at this stage.
- `BACKTEST` is the default configured mode. Live trading remains disabled.

## Setup (Windows PowerShell)

From the project root:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m crypto_bot.main
```

The virtual environment keeps this project's Python libraries separate from global Python installations. Do not commit `.venv`.

## Configuration and security

- `.env.example` contains names and safe defaults only.
- `.env` is ignored by Git. Do not share it or paste its contents into chats.
- Do not reuse previously exposed credentials; revoke and replace them before any future exchange connection.
- Keep Binance Spot Testnet and live Spot credentials separate.
- Never enable withdrawal permissions for trading keys.
- Both backend and dashboard hosts are validated as loopback addresses (`127.0.0.1` or `::1`).
- The `LIVE_TRADING_ENABLED` setting is only a configuration guard in this stage; it does not implement or authorize live execution.

## Testing

Run tests from the project root with `.\.venv\Scripts\python.exe -m pytest -q`. Tests must be deterministic and must not call Binance or Telegram.

## Current limitations

This foundation validates configuration and logging only. It does not yet fetch market data, calculate indicators, trade, or connect to Telegram. Each capability will be implemented and verified in a later step.
## Step 6 — Read-only market data

Fetch and validate closed Binance Spot candles (public endpoints only):

```powershell
.\.venv\Scripts\python.exe -m crypto_bot.market_data.fetch_candles
```

Observe a short public kline stream (forming updates are not final candles):

```powershell
.\.venv\Scripts\python.exe -m crypto_bot.market_data.stream_klines --symbol BTCUSDT --interval 1m --seconds 20
```

See `docs/MARKET_DATA_ENGINE.md` for validation rules. These commands do not query account endpoints or place orders.

WebSocket startup errors are reported with a short sanitized reason; the stream remains read-only and does not require credentials.
WebSocket market updates use `aiohttp` directly against Binance's documented public raw Spot kline stream. REST market discovery and candles continue to use the official Binance Spot SDK. The stream observer is unauthenticated and does not submit orders.
### Step 7 â€” Multi-timeframe alignment

After completing Step 7, inspect configured timeframe mappings with public Binance data (no credentials or trading):

```powershell
.\.venv\Scripts\python.exe -m crypto_bot.market_data.inspect_timeframes --symbol BTCUSDT --limit 100
```

The default mappings are configurable research defaults, not a validated strategy. Context data is aligned by close timestamp; future candles are never selected for earlier decisions.

## Step 8 — Shared technical indicators

The canonical indicator implementation is `src/crypto_bot/indicators/engine.py`. It provides causal EMA, MACD, RSI, Bollinger Band, and rolling-volume features with explicit warm-up values and strict candle-series validation. See `docs/INDICATOR_ENGINE.md` for formula conventions and the public-data inspection command.
