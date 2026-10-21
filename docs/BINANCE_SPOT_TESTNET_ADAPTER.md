# Binance Spot Testnet Adapter

This adapter is bound to `https://testnet.binance.vision` and has no configurable production endpoint or live fallback. It supports a public connectivity check, exchange metadata, signed account/order lookup requests, and explicit Spot Testnet order submission. Creating the adapter requires `DEFAULT_TRADING_MODE=BINANCE_SPOT_TESTNET`, `LIVE_TRADING_ENABLED=false`, and locally configured Testnet credentials.

## Configuration

Set these only in the ignored local `.env` file, never in source control:

```dotenv
DEFAULT_TRADING_MODE=BINANCE_SPOT_TESTNET
LIVE_TRADING_ENABLED=false
BINANCE_SPOT_TESTNET_API_KEY=your_testnet_key
BINANCE_SPOT_TESTNET_API_SECRET=your_testnet_secret
```

Use keys generated specifically for the Binance Spot Test Network. Do not use production credentials. Never share credentials in logs, screenshots, chat, or commits. If a key was previously exposed, revoke/rotate it.

## Safe usage

The adapter is not wired into application startup or any strategy. Unit tests use fake sessions and do not contact Binance or submit orders. Do not call `submit_order` until a separately approved Testnet integration procedure is in place. A transport timeout after submission can mean the exchange accepted the order; query the order using its exchange/client order ID instead of blindly retrying.

The Spot Test Network is simulated and periodically reset. Testnet fills, balances, and behavior are not evidence of profitability or production readiness. Live trading remains out of scope and disabled.
