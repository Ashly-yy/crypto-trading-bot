# Testnet execution safety gate

The guarded execution route is `TestnetOrderExecutor.execute_buy`. Submission is disabled by default and requires an explicit `allow_testnet_submission=True` constructor opt-in. Even when explicitly enabled for controlled fake-adapter tests, it accepts BUY LIMIT orders only, requires an explicit exchange-symbol-to-risk-asset mapping, matches quantity and limit price exactly to the `TradeProposal`, obtains portfolio/context values through providers, evaluates the central `RiskManager`, checks the persistent kill switch immediately before submission, and then uses an internal in-process capability to reach the adapter. Do not enable this opt-in for exchange use until the separate execution-readiness review is approved.

Direct calls to `BinanceSpotTestnetAdapter.submit_order` without that capability are rejected before HTTP. This is an accidental-bypass guard, not a security boundary against deliberate modification or introspection of the Python process.

Important limitations:
- This step does not submit any orders and does not connect to Binance.
- Tests use fake adapters and local SQLite only.
- The context/portfolio providers must themselves return genuinely fresh and reconciled state; the gate cannot prove freshness on its own.
- Fill-level accounting, commission accounting, and authoritative balance/position reconciliation are not implemented. Reconciliation therefore deliberately keeps terminal orders unresolved and blocks subsequent entries.
- The constructor opt-in is a software guard, not a security boundary against deliberate code changes.
- Only BUY LIMIT is allowed here. SELLs/exits require a separately designed safe path.
- Do not use Testnet orders until this gate and the full test suite pass. Live trading remains disabled.
