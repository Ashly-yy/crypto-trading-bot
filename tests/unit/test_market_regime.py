from dataclasses import replace
from decimal import Decimal
import math

import pytest

from crypto_bot.market_data.candles import Candle, CandleBatch, CandleGap
from crypto_bot.market_intelligence.regime import (
    AgreementStatus,
    DataQuality,
    RegimeConfig,
    RegimeLabel,
    VolatilityLabel,
    assess_market_regime,
    required_candle_count,
    summarize_timeframe_agreement,
)


def make_closing_prices(returns: list[float], initial: float = 100.0) -> tuple[float, ...]:
    prices = [initial]
    for value in returns:
        prices.append(prices[-1] * math.exp(value))
    return tuple(prices)


def make_candles(closes: tuple[float, ...], *, symbol: str = "BTCUSDT", interval: str = "5m", volumes: tuple[float, ...] | None = None) -> tuple[Candle, ...]:
    duration_by_interval = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "1d": 86_400_000}
    duration_ms = duration_by_interval[interval]
    items: list[Candle] = []
    for index, close in enumerate(closes):
        open_price = closes[index - 1] if index else close
        high = max(open_price, close) * 1.001
        low = min(open_price, close) * 0.999
        volume = volumes[index] if volumes is not None else 100.0
        items.append(Candle(
            symbol=symbol,
            interval=interval,
            open_time_ms=index * duration_ms,
            open=Decimal(str(open_price)),
            high=Decimal(str(high)),
            low=Decimal(str(low)),
            close=Decimal(str(close)),
            volume=Decimal(str(volume)),
            close_time_ms=(index + 1) * duration_ms - 1,
            quote_asset_volume=Decimal(str(close * volume)),
            number_of_trades=10,
            taker_buy_base_volume=Decimal(str(volume / 2)),
            taker_buy_quote_volume=Decimal(str(close * volume / 2)),
        ))
    return tuple(items)


def make_batch(candles: tuple[Candle, ...], *, stale: bool = False, gaps: tuple[CandleGap, ...] = ()) -> CandleBatch:
    last_close = candles[-1].close_time_ms if candles else None
    return CandleBatch(
        symbol=candles[-1].symbol if candles else "BTCUSDT",
        interval=candles[-1].interval if candles else "5m",
        requested_limit=len(candles),
        received_count=len(candles),
        candles=candles,
        incomplete_candles_dropped=0,
        gaps=gaps,
        is_stale=stale,
        latest_candle_age_ms=999_999 if stale else 1_000,
        as_of_time_ms=(last_close or 0) + (999_999 if stale else 1_000),
    )


def stable_trend(direction: int = 1, count: int = 140) -> tuple[Candle, ...]:
    returns = [direction * 0.002] * (count - 1)
    return make_candles(make_closing_prices(returns))


def test_required_history_covers_indicator_warmup_and_volatility_baseline() -> None:
    assert required_candle_count() == max(34, 20 + 20 + 1)


def test_classifies_confirmed_upward_trend() -> None:
    result = assess_market_regime(make_batch(stable_trend(1)))
    assert result.regime is RegimeLabel.UPWARD_TREND
    assert result.data_quality is DataQuality.USABLE
    assert result.trend_separation_pct is not None and result.trend_separation_pct > 0
    assert result.explanations


def test_classifies_confirmed_downward_trend() -> None:
    result = assess_market_regime(make_batch(stable_trend(-1)))
    assert result.regime is RegimeLabel.DOWNWARD_TREND
    assert result.trend_separation_pct is not None and result.trend_separation_pct < 0


def test_classifies_flat_market_as_range_and_zero_volatility_is_handled() -> None:
    candles = make_candles(tuple([100.0] * 140))
    result = assess_market_regime(make_batch(candles))
    assert result.regime is RegimeLabel.RANGE
    assert result.volatility is VolatilityLabel.NORMAL
    assert result.volatility_ratio == 1.0


def test_detects_relative_volatility_spike() -> None:
    returns = [0.001] * 100 + [0.006 if index % 2 == 0 else -0.006 for index in range(25)]
    result = assess_market_regime(make_batch(make_candles(make_closing_prices(returns))))
    assert result.volatility is VolatilityLabel.HIGH
    assert result.volatility_ratio is not None and result.volatility_ratio >= 1.5


def test_detects_relative_volatility_contraction() -> None:
    returns = [0.002] * 100 + [0.0] * 25
    result = assess_market_regime(make_batch(make_candles(make_closing_prices(returns))))
    assert result.volatility is VolatilityLabel.LOW
    assert result.volatility_ratio is not None and result.volatility_ratio <= 0.67


def test_volume_activity_is_reported_separately_from_liquidity() -> None:
    closes = make_closing_prices([0.002] * 139)
    volumes = tuple([100.0] * (len(closes) - 1) + [1000.0])
    result = assess_market_regime(make_batch(make_candles(closes, volumes=volumes)))
    assert result.volume_activity.value == "above_average"
    assert result.liquidity_assessment.startswith("not_assessed")


def test_insufficient_history_fails_closed_as_uncertain() -> None:
    result = assess_market_regime(make_candles(tuple([100.0] * 30)))
    assert result.regime is RegimeLabel.UNCERTAIN
    assert result.volatility is VolatilityLabel.UNKNOWN
    assert result.data_quality is DataQuality.INSUFFICIENT
    assert result.explanations


def test_empty_sequence_fails_closed() -> None:
    result = assess_market_regime(())
    assert result.regime is RegimeLabel.UNCERTAIN
    assert result.data_quality is DataQuality.EMPTY


def test_stale_batch_fails_closed() -> None:
    result = assess_market_regime(make_batch(stable_trend(), stale=True))
    assert result.regime is RegimeLabel.UNCERTAIN
    assert result.data_quality is DataQuality.STALE
    assert result.volatility is VolatilityLabel.UNKNOWN


def test_gapped_batch_fails_closed() -> None:
    gap = CandleGap(previous_open_time_ms=0, next_open_time_ms=600_000, expected_next_open_time_ms=300_000, missing_candle_count=1)
    result = assess_market_regime(make_batch(stable_trend(), gaps=(gap,)))
    assert result.regime is RegimeLabel.UNCERTAIN
    assert result.data_quality is DataQuality.GAPPED


def test_invalid_gap_in_raw_sequence_fails_closed() -> None:
    candles = list(stable_trend())
    candles[50] = replace(candles[50], open_time_ms=candles[50].open_time_ms + 300_000, close_time_ms=candles[50].close_time_ms + 300_000)
    result = assess_market_regime(tuple(candles))
    assert result.regime is RegimeLabel.UNCERTAIN
    assert result.data_quality is DataQuality.INVALID


def test_sequence_assessment_discloses_unverified_freshness() -> None:
    result = assess_market_regime(stable_trend())
    assert result.data_quality is DataQuality.FRESHNESS_UNVERIFIED
    assert any("freshness cannot be verified" in detail for detail in result.explanations)


def test_liquidity_is_not_inferred_from_volume() -> None:
    result = assess_market_regime(make_batch(stable_trend()))
    assert result.liquidity_assessment.startswith("not_assessed")


def test_assessment_is_timestamped_and_tied_to_last_closed_candle() -> None:
    batch = make_batch(stable_trend())
    result = assess_market_regime(batch)
    assert result.assessment_time_ms == batch.as_of_time_ms
    assert result.latest_candle_close_time_ms == batch.candles[-1].close_time_ms
    assert result.closed_candle_count == len(batch.candles)


def test_same_input_produces_same_assessment() -> None:
    data = make_batch(stable_trend())
    assert assess_market_regime(data) == assess_market_regime(data)


def test_invalid_regime_thresholds_are_rejected() -> None:
    with pytest.raises(ValueError, match="high_volatility_ratio must be greater than 1"):
        RegimeConfig(high_volatility_ratio=1.0)
    with pytest.raises(ValueError, match="minimum_volatility_baseline_samples"):
        RegimeConfig(volatility_baseline_lookback=10, minimum_volatility_baseline_samples=11)


def test_aligned_timeframes_report_agreement() -> None:
    fast = assess_market_regime(make_batch(stable_trend(1)))
    slow_candles = make_candles(make_closing_prices([0.002] * 139), interval="1h")
    slow = assess_market_regime(make_batch(slow_candles))
    result = summarize_timeframe_agreement((fast, slow))
    assert result.status is AgreementStatus.AGREED
    assert result.direction is RegimeLabel.UPWARD_TREND


def test_conflicting_timeframes_are_mixed_and_uncertain() -> None:
    upward = assess_market_regime(make_batch(stable_trend(1)))
    downward_candles = make_candles(make_closing_prices([-0.002] * 139), interval="1h")
    downward = assess_market_regime(make_batch(downward_candles))
    result = summarize_timeframe_agreement((upward, downward))
    assert result.status is AgreementStatus.MIXED
    assert result.direction is RegimeLabel.UNCERTAIN


def test_uncertain_timeframe_makes_agreement_incomplete() -> None:
    upward = assess_market_regime(make_batch(stable_trend(1)))
    short = assess_market_regime(make_candles(tuple([100.0] * 10), interval="1h"))
    result = summarize_timeframe_agreement((upward, short))
    assert result.status is AgreementStatus.INCOMPLETE
    assert result.direction is RegimeLabel.UNCERTAIN


def test_no_timeframe_inputs_is_unavailable() -> None:
    result = summarize_timeframe_agreement(())
    assert result.status is AgreementStatus.UNAVAILABLE
    assert result.direction is RegimeLabel.UNCERTAIN