# Crypto Trading Bot — Project Specification

**Document status:** Draft for user review
**Specification version:** 0.1
**Last updated:** 15 October 2026
**Development environment:** Windows 11 x64; Python 3.13; VS Code; Git; Node.js LTS
**Dashboard access:** Local laptop only; Telegram remains available for authorized remote monitoring and commands.

> This is the living project specification. It describes approved requirements and proposed architecture, not proof that any feature has already been implemented or tested. Update this document whenever a material requirement or architecture decision is approved.

## 1. Project objective

Build a modular, understandable, secure, and testable cryptocurrency trading application for Binance **Spot only**. It will provide historical backtesting, live-data paper trading, Binance Spot Testnet order testing, and a future separately gated Binance live Spot mode. It will use one shared strategy definition, market-data layer, portfolio model, risk manager, logging system, and trade lifecycle model across its operating modes.

The project must support three separately configurable trading styles—scalping, day trading, and swing trading—without assuming any style is profitable. Adaptive market intelligence and dynamic sizing must be explainable, bounded, measurable, and subordinate to deterministic risk controls.

## 2. Core principles

1. Security before connectivity.
2. Correct data before strategy complexity.
3. Correct equity accounting before performance reporting.
4. Deterministic risk validation before any order submission.
5. Shared strategy logic across backtesting and paper trading.
6. Test before declaring a feature complete.
7. Paper trading and Testnet before considering live trading.
8. No paid services are required for core operation.
9. A trading signal proposes an action; it does not authorize it.
10. When material uncertainty affects safety, do not open new positions; report the issue and follow the documented policy for existing holdings.
11. Do not invent performance claims, market observations, test results, or feature status.
12. No secrets are ever included in source control, frontend bundles, diagnostic output, or chat messages.

## 3. Confirmed user and environment requirements

- Operating system: Windows 11, x64-based processor.
- Installed development editor: Visual Studio Code.
- Verified tools: Python 3.13.16, pip, Git 2.56.0, Node.js 24.22.0, npm 11.19.1, VS Code 1.141.1.
- Workspace: `$HOME\Projects\crypto-trading-bot`.
- Dashboard: local laptop only. Bind backend and frontend development services to loopback addresses; do not expose the dashboard to the home network or public internet by default.
- Remote access: authorized Telegram interactions are allowed, with backend authorization checks.
- Budget: zero paid-tool budget for core operation. Use free/open-source tools and public data endpoints where practical; disclose any registration, API key, rate-limit, or free-tier limitation before relying on an external service.
- Computer: normal Windows PC; no dedicated GPU may be required.
- Exchange: Binance, Spot markets only.

## 4. Strict instrument and exchange scope

The bot may buy and sell eligible assets in supported Binance Spot trading pairs using available spot-account balances. It must not implement or call futures, margin, leverage, short-selling, options, perpetual-contract, or other derivatives endpoints. The exchange adapter must validate that a symbol is a supported Spot market before it can be proposed for trading.

Eligible pairs must be discovered from exchange market metadata and checked for status, quote asset, filters, quantity increments, minimum/maximum quantities, and minimum notional where applicable. A symbol mentioned in a configuration or watchlist is not, by itself, proof that the pair is tradeable.

## 5. Operating modes

The modes are separate safety boundaries and must not silently fall back into one another.

### 5.1 Historical backtesting

- Uses historical data and simulated orders only.
- Must never submit exchange orders.
- Uses timestamp-aligned, point-in-time information and a documented fill/cost model.
- Reports results as backtested, not paper or live performance.

### 5.2 Paper trading

- Uses live public Binance market data.
- Uses a virtual portfolio and simulated orders/fills; it must never call a real order-submission endpoint.
- Models estimated fees, spread, slippage, balances, fills, partial-fill scenarios, and rejection scenarios to the extent supported by the simulation design.
- Does not require trading-enabled Binance API credentials.

### 5.3 Binance Spot Testnet

- Uses the distinct Binance Spot Testnet environment and separate test credentials.
- Exercises the exchange order lifecycle on sandbox assets.
- Must not treat Testnet fills or returns as proof of live fills or profitability.
- Environment configuration must be explicit and consistent across REST and WebSocket connections.
- Must not fall back to production endpoints if Testnet configuration or connectivity fails.

### 5.4 Binance Live Spot — future, disabled by default

- Not enabled during the initial development, backtesting, or paper-trading stages.
- Requires explicit user authorization, a documented readiness review, secure live-only configuration, and a separate activation procedure.
- Must fail closed if the mode, endpoint, key permissions, account state, clock, risk state, or order constraints cannot be verified.
- Must not become active due to restart, default values, configuration typos, frontend state, or a Telegram command alone.
- Requires exchange fill confirmation and account/order reconciliation before local state is considered authoritative.
- Withdrawal permission is never required and must remain disabled.

Recommended internal mode identifiers: `BACKTEST`, `PAPER`, `BINANCE_SPOT_TESTNET`, and `BINANCE_SPOT_LIVE`. Each mode must be visible in logs, the dashboard, and Telegram messages.

## 6. High-level architecture

Use one Python backend and one React frontend. Exchange, paper, and historical execution are adapters around shared strategy and risk logic rather than independent bots.

```text
Binance public market data / historical data
                  |
                  v
        Data adapters and validation
                  |
                  v
   Candle store, features, and market intelligence
                  |
                  v
     Scalp / Day / Swing strategy evaluators
                  |
                  v
          Opportunity arbitration
                  |
                  v
    Central portfolio and risk manager <---- Kill switch / operating state
                  |
                  v
      Trading-mode execution adapter
       /          |           |           \
  Backtest      Paper      Spot Testnet    Live Spot (locked)
       \          |           |           /
                  v
     Order/position ledger and reconciliation
                  |
         +--------+---------+
         |                  |
         v                  v
 Local FastAPI API     Authorized Telegram bot
         |
         v
 Local React dashboard (loopback only)
```

### Component responsibilities

- **Configuration:** parse and validate mode, symbols, strategies, risk limits, service settings, and secret references. Reject contradictory or missing settings.
- **Market data:** retrieve, normalize, cache, timestamp, and validate candles, trades, prices, and optional depth data.
- **Indicators/features:** one canonical implementation for each approved feature, with explicit warm-up, null, and invalid-value handling.
- **Market intelligence:** maintain a timestamped assessment of regime, trend, momentum, volatility, liquidity, market-wide conditions, sentiment source quality, timeframe agreement, costs, and uncertainty.
- **Strategies:** independent scalp, day, and swing evaluators which emit proposals, not exchange orders.
- **Opportunity arbitration:** rank candidates and identify duplicate, overlapping, conflicting, or correlated opportunities competing for capital.
- **Portfolio:** maintain cash, holdings, orders, positions, realized P&L, unrealized P&L, and total equity.
- **Risk manager:** mandatory centralized approval/rejection/resizing layer applied to every proposed entry and relevant position action.
- **Execution adapters:** distinct backtest, paper, Spot Testnet, and future live Spot implementations behind a common interface.
- **Persistence:** store decision events, orders, fills, position lifecycles, configuration changes, risk events, and recovery state.
- **API:** expose validated backend state and approved actions to the local dashboard.
- **Telegram:** authorized remote monitoring and commands; never carries secrets and cannot bypass backend controls.
- **Observability:** structured logs, health checks, data freshness, error and reconnect status, alert deduplication, and audit trails.

## 7. Proposed technology choices

These choices are the recommended baseline. The compatible package versions must be verified and pinned during implementation, not guessed in advance.

- **Backend:** Python 3.13, FastAPI, Pydantic for validated configuration and API models.
- **Binance integration:** prefer Binance's official Python Spot SDK (`binance-sdk-spot`) behind a dedicated adapter; validate its current package/API and Testnet support before integration. Do not mix several exchange libraries for the same responsibility without a documented reason.
- **Data and calculations:** pandas and NumPy initially; choose one tested indicator implementation for the production strategy rather than duplicating `ta` and TA-Lib calculations.
- **Persistence:** SQLite initially, behind repository/service interfaces so storage can be changed later if evidence warrants it.
- **Frontend:** React with Vite and TypeScript if compatible with the installed Node.js LTS line.
- **Frontend/backend communication:** local HTTP API; optional WebSocket events for live dashboard updates where useful.
- **Testing and quality:** pytest, formatting/linting, type checks where practical, deterministic fixtures, and integration/failure tests.
- **Secret loading:** environment variables and a local `.env` file excluded from Git. `.env.example` contains placeholders only.
- **Version control:** Git local repository. No remote hosting is required.
- **Charts:** choose a free, actively maintained charting library during frontend implementation after licensing and bundle size are reviewed.

The backend should listen on `127.0.0.1` by default, not `0.0.0.0`. The frontend should also use localhost development access. CORS must allow only the configured local frontend origin. This local-only assumption must be revisited before any network exposure is added.

## 8. Market-data requirements

- Prefer Binance public market-data endpoints and streams for core exchange data; public market data normally does not require private account credentials.
- Retrieve only supported candle intervals needed by enabled strategies. Candidate intervals include 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, and 1d, subject to endpoint support and strategy needs.
- Store times consistently in UTC internally and display local time only as a presentation concern.
- Detect gaps, duplicates, out-of-order updates, stale responses, and incomplete candles.
- Use only closed candles for strategies that specify close-confirmed signals.
- A higher-timeframe feature may only use data that was actually available at the decision timestamp; no future candle values may be forward-filled into the past.
- Cache reusable data and respect documented rate limits. Apply timeouts, bounded retries/backoff, and clear health states.
- WebSocket reconnects must trigger revalidation/resynchronization. If freshness or sequence integrity cannot be established, block new entries.
- Order-book features are optional. If used, reconstruct the book from the appropriate snapshot and ordered updates or use a verified maintained implementation. Do not treat a depth-difference update as a full book.
- The current old-code MVRV calculation is not genuine realized-cap MVRV. Do not label it as MVRV. Use a reputable timestamped on-chain source only if its terms, cost, coverage, and historical availability are acceptable; otherwise leave this feature out or accurately name and validate a proxy.
- Sentiment and public news/social data are optional. Do not make core operation depend on a premium API, unreliable scraping, or unlicensed sources.

## 9. Trading styles

### Scalping

- Candidate lower timeframes, but the actual set will be selected through tests.
- Must account for spread, fees, slippage, liquidity, data latency, and trade-frequency limits.
- Reject trades where expected movement does not justify costs.
- Deduplicate unchanged signals and prevent rapid repeat entries.
- No claim of ultra-low-latency execution from a normal Python polling loop.

### Day trading

- Intraday setup and higher-timeframe context.
- Explicit entry, exit, stop, target, and overnight-holding policy.
- Monitor daily equity and trading limits using marked-to-market equity.

### Swing trading

- Higher-timeframe trend and market structure.
- Holding periods may span days or longer; account for overnight/weekend exposure and larger price moves.
- Avoid reacting to irrelevant minor fluctuations.

Each style has its own documented parameters and can be enabled/disabled independently. Each must first be tested in isolation. All share one portfolio and risk manager. The system must avoid opening duplicated or conflicting positions just because several strategies emit a signal for the same coin.

The initial strategy research should revisit the old EMA, MACD, RSI, Bollinger Band, volume, whale-flow, and depth ideas rather than automatically adopting them. Entry rules, especially OR-versus-AND combinations, must be explicitly specified and kept consistent between backtesting and paper trading. No strategy is presumed profitable.

## 10. Market intelligence and bounded adaptation

Start with transparent, deterministic rules. Machine learning or agentic AI is optional and must not be a prerequisite for the core bot.

Maintain timestamped per-symbol assessments covering:

- Regime: upward trend, downward trend, range, high/low volatility, low liquidity, or uncertain.
- Trend/momentum and timeframe agreement.
- Volatility relative to the asset's own history and sudden changes.
- Volume, spread, liquidity, and estimated execution cost.
- Market-wide direction, BTC influence and correlations where measured reliably.
- Optional sentiment direction/freshness/source reliability.
- Contradictions, data quality, uncertainty, and recommended strategy response.

Adaptation may select a tested strategy, reduce trade frequency/exposure, require stronger confirmation, or abstain within preapproved boundaries. It may not rewrite its code, freely change risk limits, or automatically promote untested changes. Every adaptation must record the inputs, reason, strategy version, and later measured effect. A no-trade/cash state is valid.

## 11. Position sizing and portfolio accounting

Do not finalize arbitrary risk percentages in this step. Recommend conservative configurable settings and obtain user approval in the dedicated risk-configuration step.

For each proposal, calculate the maximum permissible quantity from the approved monetary risk budget and estimated loss per unit at the planned stop, then reduce it as needed for fees, spread, slippage, volatility, liquidity, available cash, exchange filters, strategy limits, single-asset concentration, correlated exposure, total portfolio exposure, and daily/drawdown limits.

If the minimum valid exchange order would exceed the approved risk budget, reject the trade. Never increase size to recover a previous loss; no martingale or uncontrolled averaging down.

Portfolio equity must equal available cash plus marked-to-market holdings, with applicable fee/liability treatment defined consistently. Track realized and unrealized P&L separately. Buying an asset must not create a false loss merely because cash decreases. Daily P&L uses a recorded daily equity baseline. All modes, reporting, dashboard, and risk checks must use the same accounting definitions.

Every position and order has a unique identifier and a lifecycle record. Submitted does not mean filled. Handle partial fills, rejections, timeouts, unknown statuses, duplicate requests, and reconciliation. Do not claim stops guarantee a maximum loss; a gap or failed connection can produce worse results.

## 12. Central safety controls and kill switch

- All strategies and order-submission paths must pass through a central risk manager.
- Persistent central states distinguish stopped, monitoring, entries-paused, emergency-halted, and running states as designed.
- Emergency halt blocks new entries across all strategies and remains in force after restart until an authorized resume.
- Cancel applicable outstanding entry orders where supported and safe; existing-holding handling follows a documented policy. Pausing must not automatically liquidate holdings unless a separately approved policy explicitly directs it.
- Unknown account state, stale/untrusted data, invalid exchange constraints, critical service failures, or inconsistent fills block new entries and alert the operator.
- Frontend and Telegram controls are requests to the backend, not direct access to exchange methods.
- Sensitive actions require authorization, confirmation as appropriate, idempotency/deduplication, and an audit record.
- Switching trading mode requires a controlled procedure, stopped strategy workers, configuration validation, and reconciliation; no silent fallback between modes.

## 13. Security and credentials

- Any credentials exposed in the old source are considered compromised and must not be reused; revoke/rotate them before later exchange integration.
- Use separate credentials for Testnet and live Spot. Paper mode does not need trading credentials.
- Keep `.env`, logs containing sensitive material, local state databases if they contain sensitive information, and secrets out of Git.
- `.env.example` contains placeholders only.
- Never enable withdrawals. Enable only the minimum required permissions for a selected mode.
- Do not log API secrets, signatures, full environment contents, or sensitive account payloads.
- Validate Telegram user/chat IDs on the backend before accepting commands. Do not trust frontend-only authorization.
- Dashboard is local-only by default. Any future remote dashboard access requires a separate security review and explicit approval.

## 14. Paper, Testnet, and live execution rules

- Shared strategy proposals and risk checks should behave consistently, but execution and fill semantics are adapter-specific.
- Backtest and paper adapters must be technically incapable of calling real order submission.
- Testnet configuration uses only Testnet endpoints and Testnet credentials. Failure must not fall back to production.
- Live execution remains off throughout initial development and paper testing. A future live pilot requires a separate readiness review and explicit authorization.
- Use exchange trade rules for precision and minimum notional checks.
- Reconcile actual order status, executed quantity, fees, and balances before treating live portfolio state as authoritative.
- Use idempotent client order IDs and documented retry logic to prevent duplicate orders after timeouts.
- Define emergency procedures for disconnects, gaps through stop levels, unresolved orders, and bot restarts with open positions.

## 15. Backtesting and evaluation

- Use chronological historical data and timestamped historical feature/signal series only.
- Never consume current live signals while replaying past candles.
- Use only information available at the simulated decision time; close-based signals should execute at a defensible subsequent price.
- Define warm-up handling, candle alignment, intrabar assumptions, fees, spread, slippage, order sizing, available capital, and competing opportunities.
- Trade IDs must match entries/exits even when positions overlap.
- Calculate equity curves, net return, maximum drawdown, win rate, profit factor, expectancy, average win/loss, turnover, cost totals, exposure, time in market, per-asset/style/regime results, and Sharpe/Sortino where appropriate.
- Compare to appropriate baselines and use development, validation, and untouched test periods.
- Avoid parameter tuning against the untouched test set. Use sensitivity and walk-forward evaluation.
- Backtest, paper, Testnet, and live performance must be reported separately.

## 16. Dashboard requirements

A responsive local dashboard should display:

- Operating mode, engine state, data/service health, active styles, and risk status.
- Watchlist, eligible pairs, prices, charts, candle intervals, volatility/regime, and relevant spread/liquidity values where available.
- Signal explanation, contributing data/timeframes, proposal status, proposed quantity, stop/target, estimated costs, and rejection reason.
- Cash, holdings, total equity, realized/unrealized P&L, open positions, pending orders, asset/style/correlation exposure, completed trades, and logs.
- Backtest and paper analytics and comparisons.
- Configurable settings with validation, change history, and safe rollback where practical.
- Pause new entries, stop a strategy, stop all strategies, and emergency halt, all enforced again in the backend.
- Clear visual distinction between BACKTEST, PAPER, BINANCE_SPOT_TESTNET, and BINANCE_SPOT_LIVE.

No secrets or exchange credentials may be placed in frontend code. Dashboard actions must be validated in the backend.

## 17. Telegram interface

Telegram can remotely show status and accept authorized commands while the dashboard stays local. Implement only through the official Bot API and verify current constraints during implementation.

Planned commands/features include status, operating mode, watchlist, enabled styles, market conditions, open paper positions, completed trades, equity/P&L, risk status, health/errors, reports, decision explanations, pause/resume, style enable/disable, and emergency halt.

Notifications should cover startup/shutdown, mode, opportunities, accepted/rejected/resized proposals, paper fills, risk events, data staleness, API failures, recovery, and summaries. Deduplicate repeated alerts and apply rate limits. All commands are authenticated by expected user/chat IDs and revalidated by the backend. Telegram alone cannot activate live trading or bypass risk controls.

## 18. Local persistence and operational recovery

Use SQLite initially to persist configuration versions, candles/features where appropriate, market assessments, strategy decisions, order intents, order updates, fills, position lifecycles, risk events, application state, and notification status. Define retention and backup behaviour. Use transactions for state changes that must be atomic.

On restart, recover application state and reconcile relevant market/exchange state before permitting new entries. In live mode, exchange account/order data is needed to reconcile real positions. In paper mode, restore the paper ledger. If reconciliation fails, keep new entries blocked and alert the user.

## 19. Testing and completion criteria

Tests are required for:

- Indicator values and warm-up behaviour.
- Signal rules and cross-mode parity.
- Candle completeness, timestamps, gaps, and stale data.
- Timeframe alignment and look-ahead prevention.
- Portfolio equity, realized/unrealized P&L, sizing, fee/slippage assumptions, precision, and exposure.
- Unique trade lifecycles and overlapping positions.
- Order partial fills, rejection, timeout, unknown status, retry and duplicate prevention.
- Startup recovery, state reconciliation, kill switch persistence, and emergency behaviour.
- Unauthorized Telegram commands and frontend/backend controls.
- Proof that backtest/paper modes cannot submit live orders and that live mode cannot activate by default.
- Full pipeline: data -> market intelligence -> strategy -> risk -> adapter -> ledger -> dashboard/Telegram.

A feature is complete only when its test evidence is recorded. Do not mark untested requirements as done.

## 20. Proposed repository structure

```text
crypto-trading-bot/
├── PROJECT_SPECIFICATION.md
├── README.md
├── .gitignore
├── .env.example
├── pyproject.toml or requirements files (choose during environment setup)
├── backend/
│   └── app/
│       ├── main.py
│       ├── config/
│       ├── market_data/
│       ├── indicators/
│       ├── market_intelligence/
│       ├── strategies/
│       ├── portfolio/
│       ├── risk/
│       ├── execution/
│       ├── backtesting/
│       ├── storage/
│       ├── api/
│       ├── telegram/
│       └── monitoring/
├── frontend/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── safety/
│   └── fixtures/
├── docs/
│   ├── architecture.md
│   ├── strategy-specifications.md
│   ├── risk-policy.md
│   ├── operations.md
│   └── testing-report.md
├── data/       # local generated data; apply suitable .gitignore rules
└── logs/       # generated logs; apply suitable .gitignore rules
```

This is a target structure, not a claim that these files exist yet. We will create it incrementally and avoid empty folders that have no role in the current step.

## 21. Implementation roadmap and gates

1. Prepare Windows development environment.
2. Approve and maintain this project specification and architecture.
3. Create secured repository and project skeleton.
4. Add configuration, logging, tests, and health validation.
5. Implement read-only Binance Spot market discovery.
6. Build market-data validation and live feeds.
7. Add multi-timeframe alignment.
8. Standardize indicators/features.
9. Build explainable market-regime assessment.
10. Evaluate free external data/sentiment sources.
11. Implement separate scalp/day/swing strategies.
12. Add bounded adaptive decisions and dynamic sizing.
13. Build portfolio accounting.
14. Implement central risk manager.
15. Implement persistent kill switch.
16. Build historical backtesting.
17. Validate performance and benchmarks.
18. Perform out-of-sample/walk-forward evaluation.
19. Implement paper trading with real public data and no real order path.
20. Implement separate Binance Spot Testnet adapter.
21. Add persistence and restart reconciliation.
22. Build local dashboard/backend integration.
23. Build authorized Telegram assistant.
24. Run integrated safety and failure tests.
25. Run and evaluate paper trading against predefined criteria.
26. Complete operational docs and assess future live readiness.

No phase may be skipped because a later feature appears more interesting. The user verifies every step and explicitly authorizes moving on.

## 22. Open decisions and approval gates

The following must be decided or approved at the relevant step rather than silently invented:

- Exact trading pairs and quote-asset policy beyond the initial BTCUSDT, ETHUSDT, and SOLUSDT research universe.
- Which trading styles and timeframes are enabled initially.
- The precise entry/exit rules and OR-versus-AND combination of signals.
- Risk percentages, exposure caps, maximum positions, daily loss and drawdown limits.
- Overnight holding policy for day trading.
- Whether validated external sentiment/on-chain sources are available and worthwhile.
- Package versions after compatibility testing.
- Paper fill/slippage model and acceptance criteria.
- Criteria and approvals for any future live pilot.

Reasonable baseline proposal: start read-only with Binance public Spot market data, test one strategy at a time, use deterministic and explainable methods, keep external sentiment/AI optional, and maintain the live mode locked. Do not create or enable trading API keys during initial setup.

## 23. Acceptance statement

This specification is ready for implementation only after the user reviews and approves it. Approval means agreement with the stated architecture and defaults, not a claim that the software is already built, tested, profitable, or safe for live trading. Any material later change must explain purpose, benefits, drawbacks, dependencies, architecture/trading/security/cost impact, affected completed work, and the recommended approach before implementation.

## Development Addendum — Step 6 Market Data

**Status at script execution: implementation files prepared; local and network verification still required.**

The market-data engine uses only public Binance Spot endpoints. REST candle retrieval obtains exchange server time, normalizes supported SDK response shapes, retains prices/volume as decimal values, rejects invalid/duplicate/out-of-order candles, excludes incomplete candles, detects candle gaps, and flags stale batches. A separate short WebSocket kline observer treats updates with `x=false` as forming-only and counts a candle as closed only when Binance sets `x=true`. Both commands verify current Spot eligibility and use no account credentials. No strategy or order execution is connected to this module.

Detailed implementation and commands: `docs/MARKET_DATA_ENGINE.md`.
### Step 6 WebSocket transport decision

A direct public WebSocket diagnostic succeeded on the development laptop while the SDK-based kline observer delivered no messages. The implementation therefore replaces only the SDK's WebSocket stream path with `aiohttp` against Binance's documented public raw Spot kline stream. The official Spot SDK remains in use for REST market discovery and historical candle retrieval. This change is limited to public data and does not add account access or order execution. It must be verified with Windows regression tests and a live read-only stream observation before Step 6 is considered complete.
## Step 7 implementation addendum â€” multi-timeframe alignment

Approved data intervals are configurable and default to `1m,3m,5m,15m,30m,1h,2h,4h,1d`. Provisional research mappings are scalp: entry `1m`, context `3m,5m`; day: entry `5m`, context `15m,1h`; swing: entry `1h`, context `4h,1d`. These are configuration defaults only, not validated strategy rules. Alignment must use only higher-timeframe candles closed strictly before the decision timestamp, explicitly represent missing context, reject duplicate/out-of-order series, and never use an unfinished entry candle for a live-style snapshot. Verify through offline regression tests and a read-only public candle/alignment inspection.

## Step 8 implementation addendum — shared indicator engine

A shared indicator module now provides deterministic EMA, MACD, RSI, Bollinger Band, and volume features for reuse across research and future paper paths. Its numerical conventions and warm-up behaviour are documented in `docs/INDICATOR_ENGINE.md`. It rejects gaps and invalid candle inputs and does not generate signals or orders. This is an implemented analytical component, not evidence that a strategy has an edge.
## Project workflow update — code review and GitHub milestones

- **Local source of truth:** Project files live in the local `Projects\crypto-trading-bot` workspace and are edited through VS Code. VS Code is the editor; the source files themselves are stored on disk.
- **Verified-step commits:** After each implementation step has passed its prescribed tests and inspection checks, create a local Git commit for that step. Do not commit an unfinished or failing step; resolve failures first.
- **Initial reviewed baseline:** Steps completed before adopting this commit cadence may be captured in a reviewed baseline commit before the first GitHub push. Review `git status --short` and inspect the complete staged file list before committing.
- **Private GitHub milestones:** The first remote push is planned after Phase 3 (Steps 8–12) has been completed and verified. Create/use a private repository. After that, push again at the end of each later completed phase. Local commits should retain individual verified-step history rather than squashing all later work into one commit.
- **Pre-push secret gate:** Before every push, inspect `.gitignore`, run `git status --short`, and review staged filenames and diffs. Confirm `.env`, API keys/secrets, Telegram tokens, credentials, virtual environments, logs, backups, caches, and runtime data are not tracked. Never paste secrets or `.env` contents into chat or commit them. If an ignore rule or tracking status is unclear, stop and resolve it before pushing.
- **No premature remote publication:** Do not create a public repository or push to any remote until the Phase 3 review and secret checks are complete. Remote creation/push remains a deliberate, user-approved action.

## Step 9 implementation note â€” explainable market regime assessment

The first regime implementation is a deterministic, explainable research baseline, not a trading signal. It classifies each symbol/interval as upward trend, downward trend, range, or uncertain; assigns relative volatility and a separate candle-volume activity label; and records data quality, timestamps, and human-readable reasons. Confirmed trends require EMA separation, close-versus-long-EMA position, and MACD-histogram agreement. Range requires neutral EMA separation, normalized momentum, and RSI. Relative volatility compares rolling log-return volatility with an earlier rolling median baseline. Initial thresholds are configurable research values, not validated parameters, and must be evaluated in later backtests before strategies rely on them.

Stale, gapped, invalid, empty, or insufficient inputs fail closed to `uncertain`. Passing a raw candle sequence cannot certify freshness and must be labelled freshness-unverified. The initial engine does not infer liquidity from candle volume; spread/order-book liquidity is explicitly not assessed until a validated source is implemented. Multi-timeframe agreement is summarized separately and conflicting or incomplete assessments remain uncertain. The regime module may inform later strategy selection but cannot send orders, size positions, bypass central risk, disable the kill switch, or change live-trading settings.

## Step 10 implementation note â€” optional external market context

Step 10 adds optional, read-only adapters for the Alternative.me Fear & Greed Index, Alternative.me global crypto market summary, and Binance public Spot 24-hour ticker. The adapters validate payloads, timestamps, finite numeric values, symbols, and observation freshness; each source fails independently and reports unavailable or stale states rather than fabricating a replacement value. The Binance breadth summary is explicitly limited to the configured watchlist, not the full crypto market. Fear & Greed output includes required provider attribution.

This is a reporting/context feature only. It does not generate an aggregate sentiment score, entries/exits, strategy selection, or position sizes; it cannot override regime logic, central risk limits, the persistent kill switch, or explicit live-trading authorization. HTTP access uses bounded HTTPS GETs with timeouts and response-size limits, no credentials, and no added third-party dependency. Freshness windows are conservative local engineering defaults, not provider guarantees. Provider outages and schema changes are expected failure cases and must not crash the rest of the report or imply trading permission.

## Step 11 — Independent strategy research baselines

Step 11 introduces pure, explainable scalp, day, and swing evaluators. Each strategy is **disabled by default** and emits only `BUY_CANDIDATE`, `EXIT_CANDIDATE`, or `NO_ACTION`; it cannot submit orders, short assets, size a position, or override the future central risk manager. Candidate entries require normalized usable `CandleBatch` inputs, warmed shared indicators, a usable regime assessment exactly aligned to the latest entry candle, valid closed context candles with no future leakage, and an explicit fresh spread/fee/slippage estimate. Missing, stale or economically inadequate cost estimates fail closed.

Defaults are research baselines, not validated or profitable rules. Scalp requires a fresh EMA cross, bullish MACD, RSI 50–68, above-average volume, bullish context EMA filters, upward regime, and cost gate; its initial stop uses the last 5 candles and target is 1.25R. Day requires a close crossing above Bollinger middle, bullish EMA/MACD, RSI 52–68, above-average volume, bullish 15m/1h context, upward regime, and cost gate; its initial stop uses the last 10 candles and target is 1.75R. Day overnight holding remains unresolved by default (`allow_overnight=None`), blocking new entries until explicitly set; a later session coordinator must enforce the selected policy. Swing requires a close above the prior 20-candle high, above Bollinger middle, bullish EMA/MACD, RSI 50–72, bullish EMA+MACD context, upward regime and cost gate; its initial stop uses the last 20 candles and target is 2R. These are reference levels, not guaranteed fills or live protective orders.

The existing Step 7 timeframe mappings remain the defaults: scalp 1m with 3m/5m context; day 5m with 15m/1h context; swing 1h with 4h/1d context. The evaluator timestamps each candidate no earlier than its candle close plus 1 ms and any input snapshot/assessment timestamp. It emits a stable closed-candle deduplication key, but a future coordinator must persist consumed keys, arbitrate cross-strategy conflicts, enforce frequency/holding constraints, manage portfolio overlap, and obtain central risk approval. The shared strategies document contains the exact AND rules, exit rules, cost gates, assumptions and limitations. New strategy activation is not authorized by this implementation; all config defaults remain disabled.
