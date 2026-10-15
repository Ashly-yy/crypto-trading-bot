# Architecture Notes

The canonical, approved requirements are in `../PROJECT_SPECIFICATION.md`.
This note is a short orientation, not proof that the components exist yet.

```text
Binance/historical data
  -> validation and aligned market data
  -> indicators and market intelligence
  -> independent scalp/day/swing signal proposals
  -> opportunity arbitration
  -> central portfolio and risk manager / persistent kill switch
  -> BACKTEST or PAPER or Spot Testnet adapter or future locked Live Spot adapter
  -> trade ledger and recovery storage
  -> local FastAPI API -> local React dashboard
  -> authorized Telegram bot for remote monitoring/commands
```

The dashboard is local-only and should bind to loopback by default. Telegram is the remote interface and must authorize every control command on the backend. All strategies share the same accounting and risk manager. Live Spot execution remains disabled until a separate readiness review and explicit authorization.

## Shared indicator engine

Technical indicators are calculated in `src/crypto_bot/indicators/engine.py`, independent of market-data retrieval and order execution. Callers pass validated closed candles and an explicit `IndicatorConfig`. Strategy implementations must reuse this engine rather than reimplementing formulas in separate trading modes.
