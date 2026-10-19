"""Explainable adaptive market context and provisional spot position sizing.

This module is a research adapter, not an execution or centralized risk manager.
It only reduces the caller-supplied risk budget; it never invents a risk percentage,
raises the budget, approves an order, or bypasses later account/exchange controls.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from enum import Enum
from typing import Mapping, TYPE_CHECKING

from crypto_bot.market_data.candles import INTERVAL_MILLISECONDS
from crypto_bot.config.timeframe_config import validate_single_interval
from crypto_bot.market_intelligence.regime import (
    DataQuality,
    RegimeAssessment,
    RegimeLabel,
    VolatilityLabel,
)
from crypto_bot.market_intelligence.external_sources import (
    ExternalIntelligenceSnapshot,
    FearGreedReading,
    GlobalMarketReading,
    MarketBreadthReading,
    SentimentLabel,
    SourceStatus,
)
if TYPE_CHECKING:
    from crypto_bot.strategies.common import StrategySignal

_SYMBOL_RE = re.compile(r"^[A-Z0-9]{5,20}$")
_LOGGER = logging.getLogger(__name__)


class AdaptiveStatus(str, Enum):
    READY = "ready"
    REDUCED = "reduced"
    BLOCKED = "blocked"


class CandidateStatus(str, Enum):
    ELIGIBLE_FOR_SIZING = "eligible_for_sizing"
    BLOCKED = "blocked"


class SizingStatus(str, Enum):
    PROVISIONAL_ESTIMATE = "provisional_estimate"
    REJECTED = "rejected"


@dataclass(frozen=True)
class AdaptiveConfig:
    """Provisional evidence haircuts, never monetary risk percentages.

    Multipliers can only preserve or reduce the caller-supplied risk budget.
    Values are uncalibrated research baselines and must be reviewed before use in
    an actual central risk manager.
    """

    max_candle_age_intervals: float = 3.0
    max_signal_age_intervals: float = 2.0
    future_skew_tolerance_ms: int = 120_000
    max_external_snapshot_age_ms: int = 6 * 60 * 60 * 1000
    max_sentiment_age_ms: int = 36 * 60 * 60 * 1000
    max_global_market_age_ms: int = 6 * 60 * 60 * 1000
    max_breadth_age_ms: int = 15 * 60 * 1000
    high_volatility_multiplier: float = 0.50
    mixed_context_multiplier: float = 0.50
    unknown_context_volatility_multiplier: float = 0.75
    adverse_breadth_multiplier: float = 0.75
    extreme_sentiment_multiplier: float = 0.75

    def __post_init__(self) -> None:
        for name in ("max_candle_age_intervals", "max_signal_age_intervals"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value <= 0:
                raise ValueError(f"{name} must be a finite positive number.")
        for name in (
            "future_skew_tolerance_ms", "max_external_snapshot_age_ms",
            "max_sentiment_age_ms", "max_global_market_age_ms", "max_breadth_age_ms",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer.")
        for name in (
            "high_volatility_multiplier", "mixed_context_multiplier",
            "unknown_context_volatility_multiplier", "adverse_breadth_multiplier",
            "extreme_sentiment_multiplier",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be a finite multiplier from 0 through 1.")


@dataclass(frozen=True)
class RegimeEvidence:
    interval: str
    regime: str
    volatility: str
    latest_candle_close_time_ms: int
    candle_age_ms: int


@dataclass(frozen=True)
class AdaptiveMarketAssessment:
    """Cross-timeframe context; evidence score is not a probability of profit."""

    symbol: str
    intervals: tuple[str, ...]
    assessment_time_ms: int
    entry_close_time_ms: int | None
    status: AdaptiveStatus
    eligible_for_sizing: bool
    evidence_score: int
    risk_multiplier: float
    regime_evidence: tuple[RegimeEvidence, ...]
    sentiment_value: int | None
    sentiment_label: str | None
    bitcoin_dominance_pct: float | None
    breadth_status: str
    breadth_advancing: int | None
    breadth_declining: int | None
    explanations: tuple[str, ...]


@dataclass(frozen=True)
class CandidateAdaptationDecision:
    symbol: str | None
    style: str | None
    status: CandidateStatus
    eligible_for_sizing: bool
    risk_multiplier: float
    evidence_score: int
    decision_time_ms: int | None
    deduplication_key: str | None
    explanations: tuple[str, ...]


@dataclass(frozen=True)
class SizingConstraints:
    """Explicit caller-provided limits, all quote values in one currency.

    risk_budget_quote is a monetary budget supplied by a future central risk
    manager/operator. This object does not decide what that budget should be.
    quantity_step and minimum_order_notional_quote must come from verified exchange
    filters before any future execution path can consider the proposal.
    """

    account_equity_quote: float
    risk_budget_quote: float
    available_cash_quote: float
    max_position_notional_quote: float
    current_symbol_exposure_quote: float
    max_symbol_exposure_quote: float
    current_portfolio_exposure_quote: float
    max_portfolio_exposure_quote: float
    current_correlated_exposure_quote: float
    max_correlated_exposure_quote: float
    minimum_order_notional_quote: float
    quantity_step: float

    def __post_init__(self) -> None:
        nonnegative = (
            "account_equity_quote", "risk_budget_quote", "available_cash_quote",
            "max_position_notional_quote", "current_symbol_exposure_quote",
            "max_symbol_exposure_quote", "current_portfolio_exposure_quote",
            "max_portfolio_exposure_quote", "current_correlated_exposure_quote",
            "max_correlated_exposure_quote", "minimum_order_notional_quote",
            "quantity_step",
        )
        for name in nonnegative:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
                raise ValueError(f"{name} must be a finite non-negative number.")
        if self.account_equity_quote <= 0:
            raise ValueError("account_equity_quote must be greater than zero.")
        if self.risk_budget_quote <= 0:
            raise ValueError("risk_budget_quote must be greater than zero; no default risk budget is set.")
        if self.risk_budget_quote > self.account_equity_quote:
            raise ValueError("risk_budget_quote cannot exceed account_equity_quote.")
        if self.quantity_step <= 0:
            raise ValueError("quantity_step must be greater than zero and come from exchange rules.")


@dataclass(frozen=True)
class PositionSizeProposal:
    status: SizingStatus
    symbol: str | None
    style: str | None
    quantity: float
    notional_quote: float
    effective_risk_budget_quote: float
    estimated_stop_and_cost_loss_quote: float
    estimated_round_trip_cost_quote: float
    risk_multiplier: float
    limiting_constraint: str | None
    execution_authorized: bool
    explanations: tuple[str, ...]


def _integer_timestamp(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _blocked_market(
    symbol: str,
    intervals: tuple[str, ...],
    now_ms: int,
    reasons: tuple[str, ...],
    *,
    evidence: tuple[RegimeEvidence, ...] = (),
    entry_close: int | None = None,
    sentiment_value: int | None = None,
    sentiment_label: str | None = None,
    dominance: float | None = None,
    breadth_status: str = "not_assessed",
    breadth_advancing: int | None = None,
    breadth_declining: int | None = None,
) -> AdaptiveMarketAssessment:
    _LOGGER.info(
        "adaptive_market_blocked symbol=%s intervals=%s reasons=%s",
        symbol, ",".join(intervals), " | ".join(reasons),
    )
    return AdaptiveMarketAssessment(
        symbol=symbol, intervals=intervals, assessment_time_ms=now_ms,
        entry_close_time_ms=entry_close, status=AdaptiveStatus.BLOCKED,
        eligible_for_sizing=False, evidence_score=0, risk_multiplier=0.0,
        regime_evidence=evidence, sentiment_value=sentiment_value,
        sentiment_label=sentiment_label, bitcoin_dominance_pct=dominance,
        breadth_status=breadth_status, breadth_advancing=breadth_advancing,
        breadth_declining=breadth_declining, explanations=reasons,
    )


def assess_adaptive_market(
    symbol: str,
    intervals: tuple[str, ...] | list[str],
    regimes_by_interval: Mapping[str, RegimeAssessment],
    *,
    now_ms: int,
    external_snapshot: ExternalIntelligenceSnapshot | None = None,
    config: AdaptiveConfig = AdaptiveConfig(),
) -> AdaptiveMarketAssessment:
    """Assess entry/context alignment; optional providers can only reduce exposure.

    The first interval is the entry timeframe; remaining intervals are context.
    All must contain a usable, fresh assessment for the same symbol. Context candles
    may be older than the entry candle but can never close after it.
    """
    if not isinstance(config, AdaptiveConfig):
        raise TypeError("config must be AdaptiveConfig.")
    if not _integer_timestamp(now_ms):
        raise ValueError("now_ms must be a non-negative integer timestamp in milliseconds.")
    if not isinstance(symbol, str) or not _SYMBOL_RE.fullmatch(symbol):
        raise ValueError("symbol must be uppercase exchange format such as BTCUSDT.")
    if isinstance(intervals, (str, bytes)) or not isinstance(intervals, (tuple, list)) or not intervals:
        return _blocked_market(symbol, (), now_ms, ("No entry/context intervals were supplied.",))
    try:
        checked = tuple(validate_single_interval(item, field_name="intervals") for item in intervals)
    except (TypeError, ValueError, AttributeError) as exc:
        return _blocked_market(symbol, (), now_ms, (f"Invalid timeframe configuration: {type(exc).__name__}.",))
    if len(set(checked)) != len(checked):
        return _blocked_market(symbol, checked, now_ms, ("Timeframe list contains duplicate intervals.",))
    if not isinstance(regimes_by_interval, Mapping):
        return _blocked_market(symbol, checked, now_ms, ("Regime inputs must be mapped by interval.",))

    reasons: list[str] = []
    evidence: list[RegimeEvidence] = []
    assessments: list[RegimeAssessment] = []
    for interval in checked:
        assessment = regimes_by_interval.get(interval)
        if not isinstance(assessment, RegimeAssessment):
            reasons.append(f"Required regime assessment for {interval} is missing or has an invalid type.")
            continue
        if assessment.symbol != symbol or assessment.interval != interval:
            reasons.append(f"Regime identity mismatch for {interval}; expected {symbol} {interval}.")
            continue
        if assessment.data_quality is not DataQuality.USABLE:
            reasons.append(f"Regime data for {interval} is not marked USABLE ({assessment.data_quality}).")
            continue
        if assessment.regime not in tuple(RegimeLabel) or assessment.volatility not in tuple(VolatilityLabel):
            reasons.append(f"Regime or volatility label for {interval} is invalid.")
            continue
        close_ms = assessment.latest_candle_close_time_ms
        asof_ms = assessment.assessment_time_ms
        if not _integer_timestamp(close_ms) or not _integer_timestamp(asof_ms):
            reasons.append(f"Required timestamps for {interval} are missing or invalid.")
            continue
        if close_ms > now_ms:
            reasons.append(f"Latest candle for {interval} closes after the as-of time; future data is blocked.")
            continue
        if asof_ms > now_ms + config.future_skew_tolerance_ms:
            reasons.append(f"Assessment timestamp for {interval} is too far in the future.")
            continue
        interval_ms = INTERVAL_MILLISECONDS.get(interval)
        if interval_ms is None or interval_ms <= 0:
            reasons.append(f"Timeframe {interval} does not have a supported fixed duration for freshness checks.")
            continue
        age_ms = now_ms - close_ms
        if age_ms > int(interval_ms * config.max_candle_age_intervals):
            reasons.append(f"Latest candle for {interval} is stale ({age_ms} ms old).")
            continue
        assessments.append(assessment)
        evidence.append(RegimeEvidence(interval, assessment.regime.value, assessment.volatility.value, close_ms, age_ms))

    if reasons or len(assessments) != len(checked):
        if not reasons:
            reasons.append("One or more required timeframe assessments were unavailable.")
        return _blocked_market(symbol, checked, now_ms, tuple(reasons), evidence=tuple(evidence))

    entry = assessments[0]
    entry_close = entry.latest_candle_close_time_ms
    assert entry_close is not None
    if any(item.latest_candle_close_time_ms is None or item.latest_candle_close_time_ms > entry_close for item in assessments[1:]):
        return _blocked_market(
            symbol, checked, now_ms,
            ("A context timeframe contains a candle later than the entry timeframe close; future leakage is blocked.",),
            evidence=tuple(evidence), entry_close=entry_close,
        )
    if entry.regime is not RegimeLabel.UPWARD_TREND:
        return _blocked_market(
            symbol, checked, now_ms,
            (f"Entry regime is {entry.regime.value}; Step 11 candidates are long-only upward-trend research rules, so new-entry sizing is blocked.",),
            evidence=tuple(evidence), entry_close=entry_close,
        )
    if entry.volatility is VolatilityLabel.UNKNOWN:
        return _blocked_market(
            symbol, checked, now_ms,
            ("Entry-timeframe volatility is unknown; adaptive sizing is blocked until it is assessed.",),
            evidence=tuple(evidence), entry_close=entry_close,
        )

    multiplier = 1.0
    score = 100
    if any(item.volatility is VolatilityLabel.HIGH for item in assessments):
        multiplier *= config.high_volatility_multiplier
        score -= 20
        reasons.append("High relative volatility applies the configured exposure haircut; no size increase is allowed.")
    if any(item.volatility is VolatilityLabel.UNKNOWN for item in assessments[1:]):
        multiplier *= config.unknown_context_volatility_multiplier
        score -= 10
        reasons.append("A context timeframe has unknown volatility; its configured uncertainty haircut is applied.")
    context_regimes = tuple(item.regime for item in assessments[1:])
    if any(item is RegimeLabel.DOWNWARD_TREND for item in context_regimes):
        return _blocked_market(
            symbol, checked, now_ms,
            ("A higher-timeframe context is in a downward trend; conflicting direction blocks new-entry sizing.",),
            evidence=tuple(evidence), entry_close=entry_close,
        )
    if any(item in (RegimeLabel.RANGE, RegimeLabel.UNCERTAIN) for item in context_regimes):
        multiplier *= config.mixed_context_multiplier
        score -= 25
        reasons.append("Context regimes are not uniformly upward; the configured mixed-timeframe haircut is applied.")
    if not context_regimes:
        reasons.append("Only the entry timeframe was assessed; confidence is lower and higher-timeframe confirmation is absent.")
        multiplier *= config.mixed_context_multiplier
        score -= 25

    sentiment_value: int | None = None
    sentiment_label: str | None = None
    dominance: float | None = None
    breadth_status = "unavailable"
    breadth_advancing: int | None = None
    breadth_declining: int | None = None
    if external_snapshot is None:
        reasons.append("Optional external context was not supplied; no values were invented and no sentiment bonus is applied.")
    elif not isinstance(external_snapshot, ExternalIntelligenceSnapshot):
        reasons.append("Optional external context had an invalid type and was ignored.")
    elif not _integer_timestamp(external_snapshot.retrieved_at_ms) or external_snapshot.retrieved_at_ms > now_ms + config.future_skew_tolerance_ms:
        reasons.append("Optional external snapshot timestamp was invalid or too far in the future; snapshot ignored.")
    elif now_ms - external_snapshot.retrieved_at_ms > config.max_external_snapshot_age_ms:
        reasons.append("Optional external snapshot is stale and was ignored.")
    else:
        sentiment = external_snapshot.sentiment
        if sentiment is not None:
            if not isinstance(sentiment, FearGreedReading):
                reasons.append("Fear & Greed payload had an invalid type and was excluded.")
            elif (_integer_timestamp(sentiment.observed_at_ms)
                    and sentiment.status is SourceStatus.AVAILABLE
                    and isinstance(sentiment.classification, SentimentLabel)
                    and isinstance(sentiment.value, int) and not isinstance(sentiment.value, bool)
                    and 0 <= sentiment.value <= 100):
                age = now_ms - sentiment.observed_at_ms
                if 0 <= age <= config.max_sentiment_age_ms:
                    sentiment_value = sentiment.value
                    sentiment_label = sentiment.classification.value
                    if sentiment.classification in (SentimentLabel.EXTREME_FEAR, SentimentLabel.EXTREME_GREED):
                        multiplier *= config.extreme_sentiment_multiplier
                        score -= 10
                        reasons.append("Extreme broad-market sentiment increases uncertainty; a conservative haircut is applied without inferring direction.")
                    else:
                        reasons.append("Fear & Greed is recorded as descriptive context only; it does not increase exposure or create a directional signal.")
                else:
                    reasons.append("Fear & Greed was stale or future-dated and did not influence sizing.")
            else:
                reasons.append("Fear & Greed was malformed, stale, future-dated, or unavailable and did not influence sizing.")
        global_market = external_snapshot.global_market
        if global_market is not None:
            if not isinstance(global_market, GlobalMarketReading) or not _integer_timestamp(global_market.observed_at_ms):
                reasons.append("Global-market context had an invalid structure and was excluded.")
            else:
                age = now_ms - global_market.observed_at_ms
                if global_market.status is SourceStatus.AVAILABLE and 0 <= age <= config.max_global_market_age_ms:
                    value = global_market.bitcoin_dominance_pct
                    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) and 0 <= value <= 100:
                        dominance = float(value)
                        reasons.append("Global market capitalization/Bitcoin dominance is reported as context only; it is not treated as pair liquidity.")
                    else:
                        reasons.append("Global-market dominance was invalid and excluded.")
                else:
                    reasons.append("Global-market context was stale or unavailable and did not influence sizing.")
        breadth = external_snapshot.market_breadth
        if breadth is not None:
            breadth_status = breadth.status.value if isinstance(breadth, MarketBreadthReading) and isinstance(breadth.status, SourceStatus) else "invalid"
            if not isinstance(breadth, MarketBreadthReading) or not _integer_timestamp(breadth.retrieved_at_ms):
                breadth_status = "invalid"
                reasons.append("Market breadth had an invalid structure and was excluded.")
            else:
                breadth_age = now_ms - breadth.retrieved_at_ms
                if breadth.status is SourceStatus.AVAILABLE and 0 <= breadth_age <= config.max_breadth_age_ms:
                    sequence_fields = (breadth.requested_symbols, breadth.observed_symbols, breadth.advancing_symbols, breadth.declining_symbols, breadth.unchanged_symbols)
                    structurally_valid = all(isinstance(items, tuple) and all(isinstance(item, str) for item in items) for items in sequence_fields)
                    if structurally_valid:
                        requested = set(breadth.requested_symbols)
                        observed = set(breadth.observed_symbols)
                        advance = set(breadth.advancing_symbols)
                        decline = set(breadth.declining_symbols)
                        unchanged = set(breadth.unchanged_symbols)
                        structurally_valid = (
                            bool(requested) and len(requested) == len(breadth.requested_symbols)
                            and len(observed) == len(breadth.observed_symbols)
                            and len(advance) == len(breadth.advancing_symbols)
                            and len(decline) == len(breadth.declining_symbols)
                            and len(unchanged) == len(breadth.unchanged_symbols)
                            and observed <= requested and not (advance & decline or advance & unchanged or decline & unchanged)
                            and observed == advance | decline | unchanged
                        )
                    if not structurally_valid:
                        breadth_status = "invalid"
                        reasons.append("Market breadth symbol sets were malformed and excluded.")
                    else:
                        breadth_advancing = len(breadth.advancing_symbols)
                        breadth_declining = len(breadth.declining_symbols)
                        if breadth_declining > breadth_advancing:
                            breadth_status = "adverse_watchlist_breadth"
                            multiplier *= config.adverse_breadth_multiplier
                            score -= 10
                            reasons.append("Declining symbols outnumber advancing symbols in the configured watchlist; exposure is reduced. This is not a whole-market statistic.")
                        else:
                            breadth_status = "watchlist_breadth_no_penalty"
                            reasons.append("Configured-watchlist breadth did not add a penalty; favorable breadth never increases exposure.")
                else:
                    breadth_status = "stale_or_partial"
                    reasons.append("Market breadth was stale, partial, or unavailable and did not influence sizing.")

    multiplier = min(1.0, max(0.0, float(multiplier)))
    score = min(100, max(0, score))
    if multiplier <= 0:
        status = AdaptiveStatus.BLOCKED
        eligible = False
    elif multiplier < 1.0:
        status = AdaptiveStatus.REDUCED
        eligible = True
    else:
        status = AdaptiveStatus.READY
        eligible = True
    if not reasons:
        reasons.append("Required timeframe evidence agrees and no configured haircut applies.")
    reasons.append("Evidence score represents rule-based evidence consistency, not win probability or expected profitability.")
    reasons.append("This assessment never authorizes an order; a future central risk manager must independently approve any proposal.")
    _LOGGER.info(
        "adaptive_market_assessed symbol=%s intervals=%s status=%s evidence_score=%d risk_multiplier=%.3f reasons=%s",
        symbol, ",".join(checked), status.value, score, multiplier, " | ".join(reasons),
    )
    return AdaptiveMarketAssessment(
        symbol=symbol, intervals=checked, assessment_time_ms=now_ms,
        entry_close_time_ms=entry_close, status=status, eligible_for_sizing=eligible,
        evidence_score=score, risk_multiplier=multiplier, regime_evidence=tuple(evidence),
        sentiment_value=sentiment_value, sentiment_label=sentiment_label,
        bitcoin_dominance_pct=dominance, breadth_status=breadth_status,
        breadth_advancing=breadth_advancing, breadth_declining=breadth_declining,
        explanations=tuple(reasons),
    )


def assess_strategy_candidate(
    signal: StrategySignal,
    market: AdaptiveMarketAssessment,
    *,
    now_ms: int,
    config: AdaptiveConfig = AdaptiveConfig(),
) -> CandidateAdaptationDecision:
    """Apply adaptive gates to a strategy's advisory BUY candidate only."""
    from crypto_bot.strategies.common import StrategyAction, StrategySignal

    reasons: list[str] = []
    style = getattr(signal, "style", None)
    symbol = getattr(signal, "symbol", None)
    decision_time = getattr(signal, "decision_time_ms", None)
    dedupe = getattr(signal, "deduplication_key", None)
    if not isinstance(signal, StrategySignal):
        reasons.append("Signal input is not a normalized StrategySignal.")
    elif signal.action is not StrategyAction.BUY_CANDIDATE:
        reasons.append("Only BUY_CANDIDATE proposals can be sized; exit/no-action proposals do not open positions.")
    if not isinstance(market, AdaptiveMarketAssessment):
        reasons.append("Adaptive market assessment has an invalid type.")
    else:
        if not market.eligible_for_sizing or market.status is AdaptiveStatus.BLOCKED or market.risk_multiplier <= 0:
            reasons.append("Adaptive market assessment blocks new-entry sizing.")
        if symbol != market.symbol:
            reasons.append("Signal symbol does not match the adaptive market assessment.")
        if not isinstance(signal, StrategySignal) or not signal.timeframes_used or tuple(signal.timeframes_used) != market.intervals:
            reasons.append("Signal timeframes do not exactly match the assessed entry/context timeframes.")
        if isinstance(signal, StrategySignal) and signal.latest_candle_close_time_ms != market.entry_close_time_ms:
            reasons.append("Signal candle close does not match the adaptive entry-timeframe close.")
    if not _integer_timestamp(now_ms):
        reasons.append("now_ms is invalid.")
    if not isinstance(signal, StrategySignal):
        _LOGGER.info("adaptive_candidate_blocked symbol=%s style=%s reasons=%s", symbol, style, " | ".join(reasons))
        return CandidateAdaptationDecision(symbol if isinstance(symbol, str) else None, style if isinstance(style, str) else None, CandidateStatus.BLOCKED, False, 0.0, 0, decision_time if _integer_timestamp(decision_time) else None, dedupe if isinstance(dedupe, str) else None, tuple(reasons))
    if signal.style not in ("scalp", "day", "swing"):
        reasons.append("Signal strategy style is not one of the configured Step 11 styles.")
    if not isinstance(signal.symbol, str) or not _SYMBOL_RE.fullmatch(signal.symbol):
        reasons.append("Signal symbol is invalid.")
    for name in ("decision_time_ms", "latest_candle_close_time_ms"):
        if not _integer_timestamp(getattr(signal, name)):
            reasons.append(f"Signal {name} is missing or invalid.")
    for name in ("reference_price", "stop_price", "target_price", "estimated_round_trip_cost_bps", "estimated_net_reward_bps"):
        value = getattr(signal, name)
        if value is None or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            reasons.append(f"Signal {name} is missing or non-finite.")
    valid_prices = all(
        value is not None and not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))
        for value in (signal.reference_price, signal.stop_price, signal.target_price)
    )
    if valid_prices and not (signal.stop_price > 0 and signal.stop_price < signal.reference_price < signal.target_price):
        reasons.append("Long-only entry must satisfy 0 < stop < reference entry < target.")
    if (signal.estimated_round_trip_cost_bps is not None
            and not isinstance(signal.estimated_round_trip_cost_bps, bool)
            and isinstance(signal.estimated_round_trip_cost_bps, (int, float))
            and signal.estimated_round_trip_cost_bps < 0):
        reasons.append("Estimated round-trip cost cannot be negative.")
    if (signal.estimated_net_reward_bps is not None
            and not isinstance(signal.estimated_net_reward_bps, bool)
            and isinstance(signal.estimated_net_reward_bps, (int, float))
            and signal.estimated_net_reward_bps <= 0):
        reasons.append("Estimated net reward after costs must be positive.")
    if _integer_timestamp(now_ms) and _integer_timestamp(signal.decision_time_ms):
        if signal.decision_time_ms > now_ms + config.future_skew_tolerance_ms:
            reasons.append("Signal decision timestamp is too far in the future.")
        elif signal.decision_time_ms < now_ms:
            interval = signal.entry_interval
            interval_ms = INTERVAL_MILLISECONDS.get(interval) if isinstance(interval, str) else None
            if interval_ms is None or now_ms - signal.decision_time_ms > int(interval_ms * config.max_signal_age_intervals):
                reasons.append("Strategy candidate is too old to adapt or size safely.")
    else:
        reasons.append("A valid current timestamp is required to check signal freshness.")
    if reasons:
        _LOGGER.info(
            "adaptive_candidate_blocked symbol=%s style=%s risk_multiplier=0.000 reasons=%s",
            signal.symbol, signal.style, " | ".join(reasons),
        )
        return CandidateAdaptationDecision(
            signal.symbol, signal.style, CandidateStatus.BLOCKED, False, 0.0,
            market.evidence_score if isinstance(market, AdaptiveMarketAssessment) else 0,
            signal.decision_time_ms if _integer_timestamp(signal.decision_time_ms) else None,
            signal.deduplication_key, tuple(reasons),
        )
    _LOGGER.info(
        "adaptive_candidate_eligible symbol=%s style=%s evidence_score=%d risk_multiplier=%.3f deduplication_key=%s",
        signal.symbol, signal.style, market.evidence_score, market.risk_multiplier, signal.deduplication_key,
    )
    return CandidateAdaptationDecision(
        symbol=signal.symbol, style=signal.style, status=CandidateStatus.ELIGIBLE_FOR_SIZING,
        eligible_for_sizing=True, risk_multiplier=market.risk_multiplier,
        evidence_score=market.evidence_score, decision_time_ms=signal.decision_time_ms,
        deduplication_key=signal.deduplication_key,
        explanations=(
            "Candidate passed this advisory adaptive layer and may be presented to the future central risk manager.",
            f"The caller-supplied risk budget would be multiplied by {market.risk_multiplier:.3f}; no budget percentage is inferred.",
            "Passing this layer is not order authorization.",
        ),
    )


def _rejected_size(signal: StrategySignal | None, multiplier: float, reason: str) -> PositionSizeProposal:
    from crypto_bot.strategies.common import StrategySignal

    _LOGGER.info(
        "provisional_position_size_rejected symbol=%s style=%s risk_multiplier=%s reason=%s",
        signal.symbol if isinstance(signal, StrategySignal) else None,
        signal.style if isinstance(signal, StrategySignal) else None,
        multiplier, reason,
    )
    return PositionSizeProposal(
        status=SizingStatus.REJECTED,
        symbol=signal.symbol if isinstance(signal, StrategySignal) else None,
        style=signal.style if isinstance(signal, StrategySignal) else None,
        quantity=0.0, notional_quote=0.0, effective_risk_budget_quote=0.0,
        estimated_stop_and_cost_loss_quote=0.0, estimated_round_trip_cost_quote=0.0,
        risk_multiplier=max(0.0, min(1.0, multiplier)) if isinstance(multiplier, (int, float)) and math.isfinite(float(multiplier)) else 0.0,
        limiting_constraint=None, execution_authorized=False, explanations=(reason, "Rejected proposals cannot be submitted; central risk approval and adapter checks remain mandatory."),
    )


def calculate_position_size(
    signal: StrategySignal,
    candidate_decision: CandidateAdaptationDecision,
    constraints: SizingConstraints,
) -> PositionSizeProposal:
    """Compute a provisional long Spot quantity, capped by risk, costs, cash, and exposures.

    This is deliberately not a risk manager. The budget and all caps are mandatory
    caller inputs; no percent-of-equity or loss-recovery default is ever inferred.
    """
    from crypto_bot.strategies.common import StrategyAction, StrategySignal

    if not isinstance(signal, StrategySignal):
        return _rejected_size(None, 0, "Signal is not a normalized StrategySignal.")
    if (not isinstance(candidate_decision, CandidateAdaptationDecision)
            or candidate_decision.status is not CandidateStatus.ELIGIBLE_FOR_SIZING
            or not candidate_decision.eligible_for_sizing):
        return _rejected_size(signal, 0, "Adaptive candidate decision is missing or blocks sizing.")
    if not isinstance(constraints, SizingConstraints):
        return _rejected_size(signal, candidate_decision.risk_multiplier, "Explicit validated sizing constraints are required.")
    if signal.action is not StrategyAction.BUY_CANDIDATE:
        return _rejected_size(signal, candidate_decision.risk_multiplier, "Only a long BUY_CANDIDATE may be provisionally sized.")
    if (candidate_decision.symbol != signal.symbol or candidate_decision.style != signal.style
            or candidate_decision.decision_time_ms != signal.decision_time_ms
            or candidate_decision.deduplication_key != signal.deduplication_key):
        return _rejected_size(signal, candidate_decision.risk_multiplier, "Adaptive decision identity/timestamp does not match the strategy proposal.")
    if candidate_decision.risk_multiplier <= 0 or candidate_decision.risk_multiplier > 1:
        return _rejected_size(signal, 0, "Adaptive multiplier must be in the interval (0, 1].")
    if signal.reference_price is None or signal.stop_price is None or signal.target_price is None:
        return _rejected_size(signal, candidate_decision.risk_multiplier, "Entry, stop and target are required for stop-based sizing.")
    if not all(not isinstance(item, bool) and isinstance(item, (int, float)) and math.isfinite(float(item)) and float(item) > 0 for item in (signal.reference_price, signal.stop_price, signal.target_price)):
        return _rejected_size(signal, candidate_decision.risk_multiplier, "Entry, stop and target must be finite positive numbers.")
    if not signal.stop_price < signal.reference_price < signal.target_price:
        return _rejected_size(signal, candidate_decision.risk_multiplier, "Invalid long-only stop/entry/target ordering.")
    if signal.estimated_round_trip_cost_bps is None or isinstance(signal.estimated_round_trip_cost_bps, bool) or not isinstance(signal.estimated_round_trip_cost_bps, (int, float)) or not math.isfinite(float(signal.estimated_round_trip_cost_bps)) or signal.estimated_round_trip_cost_bps < 0:
        return _rejected_size(signal, candidate_decision.risk_multiplier, "A finite non-negative round-trip cost estimate is required.")
    if signal.estimated_net_reward_bps is None or isinstance(signal.estimated_net_reward_bps, bool) or not isinstance(signal.estimated_net_reward_bps, (int, float)) or not math.isfinite(float(signal.estimated_net_reward_bps)) or signal.estimated_net_reward_bps <= 0:
        return _rejected_size(signal, candidate_decision.risk_multiplier, "A finite positive estimated net reward is required.")

    # Decimal from strings avoids binary-float upward rounding of quantities/caps.
    entry = Decimal(str(signal.reference_price))
    stop = Decimal(str(signal.stop_price))
    cost_rate = Decimal(str(signal.estimated_round_trip_cost_bps)) / Decimal("10000")
    risk_budget = Decimal(str(constraints.risk_budget_quote)) * Decimal(str(candidate_decision.risk_multiplier))
    loss_per_unit = (entry - stop) + entry * cost_rate
    if risk_budget <= 0 or loss_per_unit <= 0:
        return _rejected_size(signal, candidate_decision.risk_multiplier, "Effective risk budget or cost-adjusted loss per unit is not positive.")

    caps = {
        "risk_budget": risk_budget / loss_per_unit,
        "available_cash": Decimal(str(constraints.available_cash_quote)) / (entry * (Decimal("1") + cost_rate)),
        "max_position_notional": Decimal(str(constraints.max_position_notional_quote)) / entry,
        "symbol_exposure": max(Decimal("0"), Decimal(str(constraints.max_symbol_exposure_quote)) - Decimal(str(constraints.current_symbol_exposure_quote))) / entry,
        "portfolio_exposure": max(Decimal("0"), Decimal(str(constraints.max_portfolio_exposure_quote)) - Decimal(str(constraints.current_portfolio_exposure_quote))) / entry,
        "correlated_exposure": max(Decimal("0"), Decimal(str(constraints.max_correlated_exposure_quote)) - Decimal(str(constraints.current_correlated_exposure_quote))) / entry,
    }
    limiting = min(caps, key=caps.get)
    max_quantity = caps[limiting]
    step = Decimal(str(constraints.quantity_step))
    quantity = (max_quantity / step).to_integral_value(rounding=ROUND_FLOOR) * step
    if quantity <= 0:
        return _rejected_size(signal, candidate_decision.risk_multiplier, f"Size is zero after applying the {limiting} constraint.")
    notional = quantity * entry
    if notional < Decimal(str(constraints.minimum_order_notional_quote)):
        return _rejected_size(signal, candidate_decision.risk_multiplier, "Minimum order notional would require increasing quantity beyond the risk/capital-capped estimate; trade skipped.")
    estimated_cost = notional * cost_rate
    estimated_loss = quantity * loss_per_unit
    if estimated_loss > risk_budget:
        # Defensive last check against decimal/step implementation regressions.
        return _rejected_size(signal, candidate_decision.risk_multiplier, "Calculated quantity breaches the effective monetary risk budget; trade skipped.")
    _LOGGER.info(
        "provisional_position_size symbol=%s style=%s quantity=%s notional_quote=%s projected_loss_quote=%s effective_budget_quote=%s limiting_constraint=%s execution_authorized=False",
        signal.symbol, signal.style, quantity, notional, estimated_loss, risk_budget, limiting,
    )
    return PositionSizeProposal(
        status=SizingStatus.PROVISIONAL_ESTIMATE, symbol=signal.symbol, style=signal.style,
        quantity=float(quantity), notional_quote=float(notional),
        effective_risk_budget_quote=float(risk_budget),
        estimated_stop_and_cost_loss_quote=float(estimated_loss),
        estimated_round_trip_cost_quote=float(estimated_cost),
        risk_multiplier=candidate_decision.risk_multiplier, limiting_constraint=limiting,
        execution_authorized=False,
        explanations=(
            f"Quantity is capped by {limiting} after applying the caller-supplied monetary risk budget and adaptive haircut.",
            "The loss estimate includes entry-to-stop distance plus estimated round-trip costs; stop fills are not guaranteed.",
            "Quantity step and minimum notional were caller-supplied and must be revalidated against current exchange filters.",
            "PROVISIONAL ONLY: the future central risk manager, portfolio accounting, kill switch and execution adapter must independently approve/reject; this result cannot place an order.",
        ),
    )