from dataclasses import replace

import pytest

from crypto_bot.market_data.candles import INTERVAL_MILLISECONDS
from crypto_bot.market_intelligence.adaptive import (
    AdaptiveConfig,
    AdaptiveStatus,
    CandidateStatus,
    SizingConstraints,
    SizingStatus,
    assess_adaptive_market,
    assess_strategy_candidate,
    calculate_position_size,
)
from crypto_bot.market_intelligence.external_sources import (
    ExternalIntelligenceSnapshot,
    FearGreedReading,
    GlobalMarketReading,
    MarketBreadthReading,
    SentimentLabel,
    SourceFailure,
    SourceStatus,
)
from crypto_bot.market_intelligence.regime import (
    DataQuality,
    RegimeAssessment,
    RegimeLabel,
    VolatilityLabel,
    VolumeActivity,
)
from crypto_bot.strategies.common import StrategyAction, StrategySignal

NOW = 1_800_000_000_000 + 40_000
ENTRY_CLOSE = (NOW // 60_000) * 60_000 - 1


def close_for(interval: str, anchor_close: int = ENTRY_CLOSE) -> int:
    duration = INTERVAL_MILLISECONDS[interval]
    assert duration is not None
    return ((anchor_close + 1) // duration) * duration - 1


def regime(
    interval: str,
    *,
    close_ms: int | None = None,
    symbol: str = "BTCUSDT",
    label: RegimeLabel = RegimeLabel.UPWARD_TREND,
    vol: VolatilityLabel = VolatilityLabel.NORMAL,
    quality: DataQuality = DataQuality.USABLE,
    asof: int = NOW,
) -> RegimeAssessment:
    return RegimeAssessment(
        symbol=symbol,
        interval=interval,
        regime=label,
        volatility=vol,
        volume_activity=VolumeActivity.ABOVE_AVERAGE,
        data_quality=quality,
        assessment_time_ms=asof,
        latest_candle_close_time_ms=close_for(interval) if close_ms is None else close_ms,
        closed_candle_count=100,
        trend_separation_pct=0.002,
        volatility_ratio=1.0,
        liquidity_assessment="not_assessed",
        explanations=("test evidence",),
    )


def regimes_for(
    intervals=("1m", "3m", "5m"),
    *, entry_label=RegimeLabel.UPWARD_TREND,
    entry_vol=VolatilityLabel.NORMAL,
    context_label=RegimeLabel.UPWARD_TREND,
    context_vol=VolatilityLabel.NORMAL,
):
    result = {}
    for index, interval in enumerate(intervals):
        result[interval] = regime(
            interval,
            label=entry_label if index == 0 else context_label,
            vol=entry_vol if index == 0 else context_vol,
            close_ms=ENTRY_CLOSE if index == 0 else close_for(interval),
        )
    return result


def breadth(*, advancing=("BTCUSDT", "SOLUSDT"), declining=("ETHUSDT",), status=SourceStatus.AVAILABLE, when=NOW):
    requested = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    observed = tuple(dict.fromkeys((*advancing, *declining)))
    unchanged = tuple(item for item in requested if item not in observed)
    return MarketBreadthReading(
        requested_symbols=requested,
        observed_symbols=observed,
        advancing_symbols=tuple(advancing),
        declining_symbols=tuple(declining),
        unchanged_symbols=unchanged,
        retrieved_at_ms=when,
        status=status,
        explanations=(),
    )


def external(*, sentiment_label=SentimentLabel.NEUTRAL, sentiment_value=53, market_breadth=None, when=NOW):
    sentiment = FearGreedReading(
        value=sentiment_value,
        classification=sentiment_label,
        observed_at_ms=when - 1_000,
        retrieved_at_ms=when,
        status=SourceStatus.AVAILABLE,
    )
    global_market = GlobalMarketReading(
        total_market_cap_usd=2_000_000_000_000,
        total_volume_24h_usd=100_000_000_000,
        bitcoin_dominance_pct=65.0,
        observed_at_ms=when - 1_000,
        retrieved_at_ms=when,
        status=SourceStatus.AVAILABLE,
    )
    return ExternalIntelligenceSnapshot(
        sentiment=sentiment,
        global_market=global_market,
        market_breadth=market_breadth,
        failures=(),
        retrieved_at_ms=when,
    )


def market(*, intervals=("1m", "3m", "5m"), regimes=None, external_snapshot=None, now=NOW, config=AdaptiveConfig()):
    regime_map = regimes if regimes is not None else regimes_for(intervals)
    return assess_adaptive_market(
        "BTCUSDT", intervals, regime_map, now_ms=now,
        external_snapshot=external_snapshot, config=config,
    )


def signal(*, action=StrategyAction.BUY_CANDIDATE, intervals=("1m", "3m", "5m"), symbol="BTCUSDT", close_ms=ENTRY_CLOSE, decision_ms=NOW, entry=100.0, stop=95.0, target=110.0, costs=20.0, net_reward=800.0):
    return StrategySignal(
        style="scalp", symbol=symbol, entry_interval=intervals[0], action=action,
        decision_time_ms=decision_ms, latest_candle_close_time_ms=close_ms,
        reference_price=entry, stop_price=stop, target_price=target,
        reward_risk_ratio=2.0, gross_target_bps=1_000.0,
        estimated_round_trip_cost_bps=costs, estimated_net_reward_bps=net_reward,
        strategy_version="test-1", timeframes_used=tuple(intervals),
        deduplication_key=f"scalp:{symbol}:{intervals[0]}:{close_ms}:buy_candidate:v1",
        reasons=("test candidate",),
    )


def candidate_for(sig=None, mk=None, *, now=NOW):
    sig = sig or signal()
    mk = mk or market()
    return assess_strategy_candidate(sig, mk, now_ms=now)


def constraints(**overrides):
    values = dict(
        account_equity_quote=10_000.0,
        risk_budget_quote=100.0,
        available_cash_quote=5_000.0,
        max_position_notional_quote=10_000.0,
        current_symbol_exposure_quote=0.0,
        max_symbol_exposure_quote=10_000.0,
        current_portfolio_exposure_quote=0.0,
        max_portfolio_exposure_quote=10_000.0,
        current_correlated_exposure_quote=0.0,
        max_correlated_exposure_quote=10_000.0,
        minimum_order_notional_quote=10.0,
        quantity_step=0.001,
    )
    values.update(overrides)
    return SizingConstraints(**values)


def test_config_rejects_multiplier_above_one():
    with pytest.raises(ValueError, match="multiplier"):
        AdaptiveConfig(high_volatility_multiplier=1.1)


def test_config_rejects_negative_freshness():
    with pytest.raises(ValueError, match="future_skew"):
        AdaptiveConfig(future_skew_tolerance_ms=-1)


def test_no_timeframes_blocks_market():
    result = market(intervals=(), regimes={})
    assert result.status is AdaptiveStatus.BLOCKED
    assert result.risk_multiplier == 0


def test_missing_regime_blocks_market():
    result = market(regimes={"1m": regimes_for()["1m"]})
    assert not result.eligible_for_sizing


def test_mismatched_regime_identity_blocks_market():
    items = regimes_for()
    items["3m"] = regime("3m", symbol="ETHUSDT", close_ms=close_for("3m"))
    result = market(regimes=items)
    assert result.status is AdaptiveStatus.BLOCKED
    assert any("identity mismatch" in item for item in result.explanations)


def test_non_usable_regime_blocks_market():
    items = regimes_for()
    items["5m"] = regime("5m", quality=DataQuality.GAPPED, close_ms=close_for("5m"))
    assert not market(regimes=items).eligible_for_sizing


def test_future_entry_candle_blocks_market():
    items = regimes_for()
    items["1m"] = regime("1m", close_ms=NOW + 1)
    assert not market(regimes=items).eligible_for_sizing


def test_stale_entry_candle_blocks_market():
    items = regimes_for()
    items["1m"] = regime("1m", close_ms=NOW - 181_000)
    assert not market(regimes=items).eligible_for_sizing


def test_future_context_close_blocks_market():
    items = regimes_for()
    items["3m"] = regime("3m", close_ms=ENTRY_CLOSE + 1)
    assert not market(regimes=items).eligible_for_sizing


@pytest.mark.parametrize("label", [RegimeLabel.DOWNWARD_TREND, RegimeLabel.RANGE, RegimeLabel.UNCERTAIN])
def test_non_upward_entry_regime_blocks_market(label):
    items = regimes_for(entry_label=label)
    assert market(regimes=items).risk_multiplier == 0


def test_unknown_entry_volatility_blocks_market():
    assert not market(regimes=regimes_for(entry_vol=VolatilityLabel.UNKNOWN)).eligible_for_sizing


def test_aligned_upward_context_is_ready_at_full_multiplier():
    result = market()
    assert result.status is AdaptiveStatus.READY
    assert result.eligible_for_sizing
    assert result.risk_multiplier == 1.0
    assert result.evidence_score == 100


def test_high_volatility_reduces_multiplier():
    result = market(regimes=regimes_for(entry_vol=VolatilityLabel.HIGH))
    assert result.status is AdaptiveStatus.REDUCED
    assert result.risk_multiplier == pytest.approx(0.5)


def test_range_context_reduces_multiplier():
    result = market(regimes=regimes_for(context_label=RegimeLabel.RANGE))
    assert result.eligible_for_sizing
    assert result.risk_multiplier == pytest.approx(0.5)


def test_downward_context_blocks_new_entry():
    result = market(regimes=regimes_for(context_label=RegimeLabel.DOWNWARD_TREND))
    assert not result.eligible_for_sizing
    assert result.risk_multiplier == 0


def test_unknown_context_volatility_reduces_multiplier():
    result = market(regimes=regimes_for(context_vol=VolatilityLabel.UNKNOWN))
    assert result.risk_multiplier == pytest.approx(0.75)


def test_external_context_is_optional_and_never_boosts_multiplier():
    no_external = market()
    favorable_breadth = external(market_breadth=breadth(advancing=("BTCUSDT", "ETHUSDT", "SOLUSDT"), declining=()))
    with_external = market(external_snapshot=favorable_breadth)
    assert no_external.risk_multiplier == 1.0
    assert with_external.risk_multiplier == 1.0


def test_adverse_complete_breadth_reduces_multiplier():
    snapshot = external(market_breadth=breadth(advancing=("BTCUSDT",), declining=("ETHUSDT", "SOLUSDT")))
    result = market(external_snapshot=snapshot)
    assert result.risk_multiplier == pytest.approx(0.75)
    assert result.breadth_status == "adverse_watchlist_breadth"


def test_partial_breadth_is_ignored():
    snapshot = external(market_breadth=breadth(advancing=("BTCUSDT",), declining=("ETHUSDT", "SOLUSDT"), status=SourceStatus.PARTIAL))
    result = market(external_snapshot=snapshot)
    assert result.risk_multiplier == 1.0
    assert result.breadth_status == "stale_or_partial"


def test_extreme_sentiment_reduces_but_does_not_set_direction():
    snapshot = external(sentiment_label=SentimentLabel.EXTREME_FEAR, sentiment_value=10)
    result = market(external_snapshot=snapshot)
    assert result.risk_multiplier == pytest.approx(0.75)
    assert result.sentiment_label == "extreme_fear"
    assert any("without inferring direction" in item for item in result.explanations)


def test_stale_external_snapshot_is_ignored():
    old = NOW - 7 * 60 * 60 * 1000
    snapshot = external(when=old)
    result = market(external_snapshot=snapshot)
    assert result.risk_multiplier == 1.0
    assert result.sentiment_value is None


def test_external_snapshot_future_timestamp_is_ignored():
    snapshot = external(when=NOW + 500_000)
    result = market(external_snapshot=snapshot)
    assert result.risk_multiplier == 1.0
    assert result.sentiment_value is None


def test_candidate_must_be_buy_candidate():
    result = candidate_for(sig=signal(action=StrategyAction.NO_ACTION))
    assert result.status is CandidateStatus.BLOCKED


def test_valid_candidate_is_eligible_for_sizing():
    sig = signal()
    mk = market()
    result = candidate_for(sig=sig, mk=mk)
    assert result.status is CandidateStatus.ELIGIBLE_FOR_SIZING
    assert result.risk_multiplier == 1.0


def test_candidate_requires_cost_estimate():
    result = candidate_for(sig=signal(costs=None))
    assert not result.eligible_for_sizing


def test_candidate_requires_positive_net_reward():
    result = candidate_for(sig=signal(net_reward=0))
    assert not result.eligible_for_sizing


def test_candidate_requires_long_stop_and_target_ordering():
    result = candidate_for(sig=signal(stop=101.0))
    assert not result.eligible_for_sizing


def test_candidate_symbol_must_match_assessment():
    result = candidate_for(sig=signal(symbol="ETHUSDT"))
    assert not result.eligible_for_sizing


def test_candidate_timeframes_must_match_exactly():
    result = candidate_for(sig=signal(intervals=("1m", "3m")))
    assert not result.eligible_for_sizing


def test_old_candidate_is_blocked():
    result = candidate_for(sig=signal(decision_ms=NOW - 180_001))
    assert not result.eligible_for_sizing


def test_future_candidate_is_blocked():
    result = candidate_for(sig=signal(decision_ms=NOW + 500_000))
    assert not result.eligible_for_sizing


def test_size_is_provisional_and_bounded_by_risk_budget():
    sig = signal()
    decision = candidate_for(sig=sig, mk=market())
    size = calculate_position_size(sig, decision, constraints())
    assert size.status is SizingStatus.PROVISIONAL_ESTIMATE
    assert not size.execution_authorized
    assert size.estimated_stop_and_cost_loss_quote <= size.effective_risk_budget_quote
    assert size.quantity > 0


def test_adaptive_multiplier_reduces_quantity():
    sig = signal()
    full = calculate_position_size(sig, candidate_for(sig=sig, mk=market()), constraints())
    reduced_market = market(regimes=regimes_for(entry_vol=VolatilityLabel.HIGH))
    reduced = calculate_position_size(sig, candidate_for(sig=sig, mk=reduced_market), constraints())
    assert reduced.quantity < full.quantity
    assert reduced.effective_risk_budget_quote == pytest.approx(full.effective_risk_budget_quote * 0.5)


def test_round_trip_costs_are_included_in_projected_loss():
    sig = signal(costs=200.0)
    size = calculate_position_size(sig, candidate_for(sig=sig), constraints())
    assert size.status is SizingStatus.PROVISIONAL_ESTIMATE
    assert size.estimated_round_trip_cost_quote > 0
    assert size.estimated_stop_and_cost_loss_quote > size.quantity * (sig.reference_price - sig.stop_price)


def test_cash_cap_limits_quantity():
    sig = signal()
    size = calculate_position_size(sig, candidate_for(sig=sig), constraints(available_cash_quote=500.0))
    assert size.notional_quote <= 500.0


def test_single_position_cap_limits_quantity():
    sig = signal()
    size = calculate_position_size(sig, candidate_for(sig=sig), constraints(max_position_notional_quote=500.0))
    assert size.notional_quote <= 500.0
    assert size.limiting_constraint == "max_position_notional"


def test_symbol_exposure_remaining_cap_limits_quantity():
    sig = signal()
    size = calculate_position_size(sig, candidate_for(sig=sig), constraints(current_symbol_exposure_quote=9_500.0, max_symbol_exposure_quote=10_000.0))
    assert size.notional_quote <= 500.0
    assert size.limiting_constraint == "symbol_exposure"


def test_portfolio_exposure_remaining_cap_limits_quantity():
    sig = signal()
    size = calculate_position_size(sig, candidate_for(sig=sig), constraints(current_portfolio_exposure_quote=9_000.0, max_portfolio_exposure_quote=10_000.0))
    assert size.notional_quote <= 1_000.0
    assert size.limiting_constraint == "portfolio_exposure"


def test_correlated_exposure_remaining_cap_limits_quantity():
    sig = signal()
    size = calculate_position_size(sig, candidate_for(sig=sig), constraints(current_correlated_exposure_quote=9_000.0, max_correlated_exposure_quote=10_000.0))
    assert size.notional_quote <= 1_000.0
    assert size.limiting_constraint == "correlated_exposure"


def test_minimum_order_does_not_force_quantity_upward():
    sig = signal()
    size = calculate_position_size(sig, candidate_for(sig=sig), constraints(minimum_order_notional_quote=3_000.0))
    assert size.status is SizingStatus.REJECTED
    assert size.quantity == 0


def test_quantity_rounds_down_to_exchange_step():
    sig = signal()
    size = calculate_position_size(sig, candidate_for(sig=sig), constraints(quantity_step=2.0))
    assert size.status is SizingStatus.PROVISIONAL_ESTIMATE
    assert size.quantity % 2.0 == pytest.approx(0.0)


def test_blocked_adaptation_cannot_be_sized():
    sig = signal()
    decision = candidate_for(sig=sig, mk=market(regimes=regimes_for(entry_label=RegimeLabel.DOWNWARD_TREND)))
    size = calculate_position_size(sig, decision, constraints())
    assert size.status is SizingStatus.REJECTED
    assert size.quantity == 0


def test_sizer_rejects_exit_proposal_even_if_decision_is_fabricated():
    buy = signal()
    decision = candidate_for(sig=buy)
    exit_signal = signal(action=StrategyAction.EXIT_CANDIDATE)
    size = calculate_position_size(exit_signal, decision, constraints())
    assert size.status is SizingStatus.REJECTED


def test_sizer_rejects_boolean_prices_even_if_decision_is_fabricated():
    sig = signal()
    decision = candidate_for(sig=sig)
    malformed = replace(sig, reference_price=True)
    size = calculate_position_size(malformed, decision, constraints())
    assert size.status is SizingStatus.REJECTED


@pytest.mark.parametrize("field,value", [
    ("risk_budget_quote", -1.0),
    ("available_cash_quote", -1.0),
    ("quantity_step", 0.0),
    ("risk_budget_quote", 10_001.0),
])
def test_invalid_sizing_constraints_are_rejected(field, value):
    changes = {field: value}
    with pytest.raises(ValueError):
        constraints(**changes)


def test_zero_remaining_portfolio_exposure_rejects_size():
    sig = signal()
    size = calculate_position_size(
        sig, candidate_for(sig=sig),
        constraints(current_portfolio_exposure_quote=10_000.0, max_portfolio_exposure_quote=10_000.0),
    )
    assert size.status is SizingStatus.REJECTED
    assert size.quantity == 0


def test_risk_budget_is_an_explicit_quote_amount_not_a_default_percentage():
    assert "risk_budget_quote" in SizingConstraints.__dataclass_fields__
    assert "risk_budget_pct" not in SizingConstraints.__dataclass_fields__

@pytest.mark.parametrize("field,value", [
    ("stop_price", "not-a-price"),
    ("estimated_round_trip_cost_bps", "not-a-cost"),
    ("estimated_net_reward_bps", "not-a-reward"),
])
def test_malformed_candidate_numbers_fail_closed(field, value):
    result = candidate_for(sig=replace(signal(), **{field: value}))
    assert not result.eligible_for_sizing


def test_malformed_external_breadth_is_excluded_without_crashing():
    snapshot = replace(external(), market_breadth=object())
    result = market(external_snapshot=snapshot)
    assert result.risk_multiplier == 1.0
    assert result.breadth_status == "invalid"