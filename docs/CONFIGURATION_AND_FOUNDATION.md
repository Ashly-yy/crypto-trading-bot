# Configuration and Foundation Notes

## Sources and defaults

Settings are read from process environment variables and, when present, a local UTF-8 `.env` file. Environment variables take precedence over values in `.env`. Empty environment values are ignored so blank placeholders do not become fake credentials.

Default mode is `BACKTEST`. Allowed mode labels are `BACKTEST`, `PAPER`, `BINANCE_SPOT_TESTNET`, and `BINANCE_SPOT_LIVE`. Defining a mode label does not mean its execution adapter exists. The LIVE adapter is not implemented in the foundation stage.

## Local-only binding

`APP_HOST` and `DASHBOARD_HOST` must be loopback IP addresses. This means the backend/dashboard will be reachable from the same computer only. Telegram may later serve as the approved remote interface with its own authorization checks.

## Secrets

Secret-valued settings use `SecretStr` so their normal representation is masked. Do not log the settings object or call `get_secret_value()` except at a narrow integration point that requires the actual credential. Do not put credentials in source code, tests, logs, screenshots, or Git history.

## Logging

Console and `logs/crypto_bot.log` receive application logs. The file rotates at approximately 5 MiB, retaining up to five rotated files. Do not log raw exchange responses that could contain sensitive account information.

## Step 4 boundary

This stage checks configuration, logging, package installation and tests only. It does not call Binance, Telegram, or any trading endpoint.