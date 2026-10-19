# Step 12 — Adaptive market context and provisional position sizing

## Purpose

Step 12 adds a deterministic, explainable adapter between the existing Step 9 regime assessments, Step 10 optional external context and Step 11 advisory strategy candidates. It can apply a bounded exposure haircut and calculate a provisional long Spot quantity from explicit monetary and exposure constraints.

## Market assessment inputs

`assess_adaptive_market()` requires the entry timeframe first, followed by every configured context timeframe. Each timeframe must have a `RegimeAssessment` for the same symbol, marked `USABLE`, timestamped, fresh for its interval, and not later than the entry candle close. Missing, stale, gapped, invalid, wrong-symbol, future-dated or mismatched inputs block sizing. These Step 11 candidates are long-only/uptrend research rules, so an entry regime that is not `UPWARD_TREND` blocks sizing, as does a downward context regime.

Each market assessment, candidate block/eligibility decision, and provisional size/rejection also emits a sanitized logger record with reasons and the computed multiplier/quantity; later central risk/audit integration must persist the full lifecycle. The rule-based evidence-consistency score is not a probability of profit. The market multiplier is never greater than `1.0`: stable aligned context does not increase the supplied risk budget. Initial configurable research haircuts are:

- High relative volatility in any required timeframe: multiplier `0.50`.
- Range/uncertain context or no higher-timeframe context: multiplier `0.50`.
- Unknown context-timeframe volatility: multiplier `0.75`.
- Adverse, complete configured-watchlist breadth (decliners exceed advancers): multiplier `0.75`.
- Extreme Fear & Greed classification: multiplier `0.75`; this reflects uncertainty and does not infer a buy/sell direction.

Multipliers combine multiplicatively and can only preserve or reduce exposure. These values are uncalibrated research defaults, **not approved production risk settings**. They must be evaluated through chronological backtesting, walk-forward analysis and paper trading before promotion. A missing/partial/stale external source does not create synthetic values or a positive confidence bonus. Watchlist breadth is not represented as whole-market breadth. Market capitalization, volume and Bitcoin dominance are descriptive context, not executable liquidity.

## Candidate adaptation

`assess_strategy_candidate()` only permits an existing `BUY_CANDIDATE` for further sizing if its symbol, decision time, latest entry candle close, style and complete timeframe list match the market assessment. It also requires finite positive entry/stop/target ordering, a fresh decision timestamp, an explicit non-negative round-trip cost estimate and positive estimated net reward. Exit and no-action results cannot open or size a position. Passing these checks is **not** an order authorization.

## Provisional quantity formula

`calculate_position_size()` requires explicit `SizingConstraints` supplied by its caller:

- Account equity and a positive monetary `risk_budget_quote` (there is no default risk percentage).
- Available cash and maximum single-position notional.
- Current/max symbol, portfolio and correlated exposure in the same quote currency.
- Verified quantity step and minimum order notional from current exchange filters.

The effective monetary budget is `risk_budget_quote × adaptive_multiplier`. Cost-adjusted loss per base unit is estimated as `(entry − stop) + entry × (round_trip_cost_bps / 10,000)`. The theoretical quantity is capped by effective risk budget divided by cost-adjusted loss per unit, available cash including an estimated cost buffer, remaining position/symbol/portfolio/correlated exposure caps, and then rounded **down** to the provided quantity step. If the rounded quantity is zero or falls below minimum notional, the proposal is rejected rather than rounded upward. No martingale or loss-recovery rule exists.

## Explicit boundaries

Every returned `PositionSizeProposal` has `execution_authorized=False`. The sizing calculator does not fetch account balances, decide the approved risk budget, calculate live portfolio exposures, choose quantity filters, submit orders or replace the central risk manager. The central risk manager and exchange adapter planned in later steps must independently validate account state, all caps, current exchange filters, min/max quantity, notional, step/tick precision, fees and the persistent kill switch before any future order could be considered. A stop price is an estimate, not a guaranteed maximum loss.

`inspect_adaptive` is read-only and uses public Binance Spot candle endpoints plus optional public context sources. It reports context and the potential multiplier only; it does not construct a trading candidate, calculate an order quantity, use account endpoints, load trading credentials or submit orders. Network and provider failures are reported and should not be treated as a valid market opportunity.