"""Deterministic, explainable market-regime assessment; never an order/signal engine.

The initial thresholds are research baselines, not validated trading rules. This module
uses only normalized closed candles and the shared Step 8 indicator implementation.
It does not infer liquidity from candle volume and cannot override centralized risk.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from statistics import median

from crypto_bot.indicators.engine import (
    IndicatorConfig,
    IndicatorSnapshot,
    calculate_latest_indicators,
    required_warmup_candles,
)
from crypto_bot.market_data.candles import Candle, CandleBatch


class RegimeLabel(str, Enum):
    UPWARD_TREND = "upward_trend"
    DOWNWARD_TREND = "downward_trend"
    RANGE = "range"
    UNCERTAIN = "uncertain"


class VolatilityLabel(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    UNKNOWN = "unknown"


class VolumeActivity(str, Enum):
    ABOVE_AVERAGE = "above_average"
    AT_OR_BELOW_AVERAGE = "at_or_below_average"
    UNKNOWN = "unknown"


class DataQuality(str, Enum):
    USABLE = "usable"
    FRESHNESS_UNVERIFIED = "freshness_unverified"
    INSUFFICIENT = "insufficient"
    EMPTY = "empty"
    STALE = "stale"
    GAPPED = "gapped"
    STALE_AND_GAPPED = "stale_and_gapped"
    INVALID = "invalid"


class AgreementStatus(str, Enum):
    AGREED = "agreed"
    MIXED = "mixed"
    INCOMPLETE = "incomplete"
    SINGLE_TIMEFRAME = "single_timeframe"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class RegimeConfig:
    """Transparent research thresholds; not calibrated or production-approved."""

    trend_separation_pct: float = 0.0015
    neutral_momentum_pct: float = 0.0005
    neutral_rsi_low: float = 40.0
    neutral_rsi_high: float = 60.0
    volatility_lookback: int = 20
    volatility_baseline_lookback: int = 60
    minimum_volatility_baseline_samples: int = 20
    high_volatility_ratio: float = 1.5
    low_volatility_ratio: float = 0.67

    def __post_init__(self) -> None:
        positive_float_fields = (
            "trend_separation_pct", "neutral_momentum_pct",
            "high_volatility_ratio", "low_volatility_ratio",
        )
        for name in positive_float_fields:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value <= 0:
                raise ValueError(f"{name} must be a finite positive number.")
        if self.high_volatility_ratio <= 1:
            raise ValueError("high_volatility_ratio must be greater than 1.")
        if not 0 < self.low_volatility_ratio < 1:
            raise ValueError("low_volatility_ratio must be between 0 and 1.")
        if self.low_volatility_ratio >= self.high_volatility_ratio:
            raise ValueError("low_volatility_ratio must be below high_volatility_ratio.")
        for name in ("volatility_lookback", "volatility_baseline_lookback", "minimum_volatility_baseline_samples"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 2:
                raise ValueError(f"{name} must be an integer of at least 2.")
        if self.minimum_volatility_baseline_samples > self.volatility_baseline_lookback:
            raise ValueError("minimum_volatility_baseline_samples cannot exceed volatility_baseline_lookback.")
        for name in ("neutral_rsi_low", "neutral_rsi_high"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"{name} must be a finite number.")
        if not (0 <= self.neutral_rsi_low < self.neutral_rsi_high <= 100):
            raise ValueError("neutral RSI bounds must satisfy 0 <= low < high <= 100.")


@dataclass(frozen=True)
class RegimeAssessment:
    """A timestamped market-condition assessment with explicit evidence and limitations."""

    symbol: str | None
    interval: str | None
    regime: RegimeLabel
    volatility: VolatilityLabel
    volume_activity: VolumeActivity
    data_quality: DataQuality
    assessment_time_ms: int | None
    latest_candle_close_time_ms: int | None
    closed_candle_count: int
    trend_separation_pct: float | None
    volatility_ratio: float | None
    liquidity_assessment: str
    explanations: tuple[str, ...]


@dataclass(frozen=True)
class TimeframeAgreement:
    """Conservative agreement summary for separately calculated timeframes."""

    status: AgreementStatus
    direction: RegimeLabel
    intervals: tuple[str, ...]
    explanations: tuple[str, ...]


def required_candle_count(
    config: RegimeConfig = RegimeConfig(),
    indicator_config: IndicatorConfig = IndicatorConfig(),
) -> int:
    """History needed for warmed indicators and a minimally populated vol baseline."""
    volatility_requirement = config.volatility_lookback + config.minimum_volatility_baseline_samples + 1
    return max(required_warmup_candles(indicator_config), volatility_requirement)


def _empty_assessment(
    *,
    symbol: str | None,
    interval: str | None,
    quality: DataQuality,
    assessment_time_ms: int | None,
    latest_close_time_ms: int | None,
    count: int,
    explanations: tuple[str, ...],
) -> RegimeAssessment:
    return RegimeAssessment(
        symbol=symbol,
        interval=interval,
        regime=RegimeLabel.UNCERTAIN,
        volatility=VolatilityLabel.UNKNOWN,
        volume_activity=VolumeActivity.UNKNOWN,
        data_quality=quality,
        assessment_time_ms=assessment_time_ms,
        latest_candle_close_time_ms=latest_close_time_ms,
        closed_candle_count=count,
        trend_separation_pct=None,
        volatility_ratio=None,
        liquidity_assessment="not_assessed: spread/depth data is not part of this step",
        explanations=explanations,
    )


def _volatility_label(
    closes: Sequence[float], config: RegimeConfig
) -> tuple[VolatilityLabel, float | None, tuple[str, ...]]:
    log_returns: list[float] = []
    for previous, current in zip(closes, closes[1:]):
        if previous <= 0 or current <= 0:
            return VolatilityLabel.UNKNOWN, None, ("Volatility unavailable because a close price is not positive.",)
        value = math.log(current / previous)
        if not math.isfinite(value):
            return VolatilityLabel.UNKNOWN, None, ("Volatility unavailable because a return is non-finite.",)
        log_returns.append(value)

    lookback = config.volatility_lookback
    rolling: list[float] = []
    for end in range(lookback, len(log_returns) + 1):
        window = log_returns[end - lookback:end]
        rolling.append(math.sqrt(math.fsum(item * item for item in window) / lookback))
    if len(rolling) < config.minimum_volatility_baseline_samples + 1:
        return VolatilityLabel.UNKNOWN, None, ("Insufficient historical rolling-volatility samples for a relative comparison.",)

    current_vol = rolling[-1]
    baseline_values = rolling[:-1][-config.volatility_baseline_lookback:]
    if len(baseline_values) < config.minimum_volatility_baseline_samples:
        return VolatilityLabel.UNKNOWN, None, ("Insufficient historical volatility baseline samples.",)
    baseline = float(median(baseline_values))
    epsilon = 1e-12
    if baseline <= epsilon:
        if current_vol <= epsilon:
            ratio = 1.0
            label = VolatilityLabel.NORMAL
            detail = "Current and baseline realized volatility are both effectively zero."
        else:
            ratio = None
            label = VolatilityLabel.HIGH
            detail = "Current realized volatility increased from an effectively zero baseline."
    else:
        ratio = current_vol / baseline
        if ratio >= config.high_volatility_ratio:
            label = VolatilityLabel.HIGH
        elif ratio <= config.low_volatility_ratio:
            label = VolatilityLabel.LOW
        else:
            label = VolatilityLabel.NORMAL
        detail = (
            f"Realized volatility ratio is {ratio:.3f} versus the rolling median baseline; "
            f"high threshold={config.high_volatility_ratio:.3f}, low threshold={config.low_volatility_ratio:.3f}."
        )
    return label, ratio, (detail,)


def _trend_label(
    snapshot: IndicatorSnapshot, config: RegimeConfig
) -> tuple[RegimeLabel, float | None, tuple[str, ...]]:
    required = (snapshot.ema_short, snapshot.ema_long, snapshot.macd, snapshot.macd_histogram, snapshot.rsi)
    if any(value is None for value in required) or snapshot.close <= 0:
        return RegimeLabel.UNCERTAIN, None, ("Trend classification unavailable: indicator warm-up is incomplete.",)

    assert snapshot.ema_short is not None
    assert snapshot.ema_long is not None
    assert snapshot.macd is not None
    assert snapshot.macd_histogram is not None
    assert snapshot.rsi is not None
    separation = (snapshot.ema_short - snapshot.ema_long) / snapshot.close
    macd_line_pct = snapshot.macd / snapshot.close
    histogram_pct = snapshot.macd_histogram / snapshot.close
    if not all(math.isfinite(value) for value in (separation, macd_line_pct, histogram_pct)):
        return RegimeLabel.UNCERTAIN, None, ("Trend classification unavailable: normalized features are non-finite.",)

    upward = (
        separation >= config.trend_separation_pct
        and snapshot.close >= snapshot.ema_long
        and macd_line_pct > 0
    )
    downward = (
        separation <= -config.trend_separation_pct
        and snapshot.close <= snapshot.ema_long
        and macd_line_pct < 0
    )
    if upward:
        return RegimeLabel.UPWARD_TREND, separation, (
            f"EMA separation is +{separation * 100:.3f}% and exceeds the {config.trend_separation_pct * 100:.3f}% threshold.",
            "Close position, short/long EMA relationship, and MACD line agree upward.",
        )
    if downward:
        return RegimeLabel.DOWNWARD_TREND, separation, (
            f"EMA separation is {separation * 100:.3f}% and exceeds the negative trend threshold.",
            "Close position, short/long EMA relationship, and MACD line agree downward.",
        )
    neutral = (
        abs(separation) < config.trend_separation_pct
        and abs(histogram_pct) <= config.neutral_momentum_pct
        and config.neutral_rsi_low <= snapshot.rsi <= config.neutral_rsi_high
    )
    if neutral:
        return RegimeLabel.RANGE, separation, (
            f"EMA separation ({separation * 100:.3f}%) and normalized MACD histogram ({histogram_pct * 100:.3f}%) are inside neutral bands.",
            f"RSI {snapshot.rsi:.2f} is inside the configured neutral band {config.neutral_rsi_low:.1f}â€“{config.neutral_rsi_high:.1f}.",
        )
    return RegimeLabel.UNCERTAIN, separation, (
        "Trend evidence is mixed or does not meet the configured confirmation rules.",
        f"Normalized EMA separation={separation * 100:.3f}%, normalized MACD line={macd_line_pct * 100:.3f}%, normalized MACD histogram={histogram_pct * 100:.3f}%, RSI={snapshot.rsi:.2f}.",
    )


def assess_market_regime(
    data: Sequence[Candle] | CandleBatch,
    config: RegimeConfig = RegimeConfig(),
    indicator_config: IndicatorConfig = IndicatorConfig(),
) -> RegimeAssessment:
    """Classify trend/range and relative volatility from closed candles only.

    Passing a CandleBatch preserves freshness/gap metadata. Passing a raw sequence
    validates candle structure but cannot prove market-data freshness. Invalid,
    stale, gapped, or insufficient input returns UNCERTAIN instead of a trade signal.
    """
    batch: CandleBatch | None = data if isinstance(data, CandleBatch) else None
    if batch is not None:
        candles = tuple(batch.candles)
        symbol, interval = batch.symbol, batch.interval
        assessment_time_ms = batch.as_of_time_ms
        quality = DataQuality.USABLE
        if not candles:
            return _empty_assessment(
                symbol=symbol, interval=interval, quality=DataQuality.EMPTY,
                assessment_time_ms=assessment_time_ms, latest_close_time_ms=None,
                count=0, explanations=("No closed candles are available.",),
            )
        latest_close_time_ms = candles[-1].close_time_ms
        has_gaps = bool(batch.gaps)
        if has_gaps and batch.is_stale:
            quality = DataQuality.STALE_AND_GAPPED
        elif has_gaps:
            quality = DataQuality.GAPPED
        elif batch.is_stale:
            quality = DataQuality.STALE
        if quality in (DataQuality.STALE, DataQuality.GAPPED, DataQuality.STALE_AND_GAPPED):
            reasons: list[str] = []
            if batch.is_stale:
                reasons.append("Latest market data is marked stale.")
            if has_gaps:
                reasons.append(f"Candle batch contains {len(batch.gaps)} gap(s).")
            reasons.append("Regime is UNCERTAIN because data-quality gates failed.")
            return _empty_assessment(
                symbol=symbol, interval=interval, quality=quality,
                assessment_time_ms=assessment_time_ms, latest_close_time_ms=latest_close_time_ms,
                count=len(candles), explanations=tuple(reasons),
            )
    else:
        if isinstance(data, (str, bytes)) or not isinstance(data, Sequence):
            return _empty_assessment(
                symbol=None, interval=None, quality=DataQuality.INVALID,
                assessment_time_ms=None, latest_close_time_ms=None, count=0,
                explanations=("Input must be a CandleBatch or sequence of normalized Candle objects.",),
            )
        candles = tuple(data)
        first = candles[0] if candles and isinstance(candles[0], Candle) else None
        symbol = first.symbol if first else None
        interval = first.interval if first else None
        latest_close_time_ms = candles[-1].close_time_ms if candles and isinstance(candles[-1], Candle) else None
        assessment_time_ms = latest_close_time_ms
        quality = DataQuality.FRESHNESS_UNVERIFIED
        if not candles:
            return _empty_assessment(
                symbol=None, interval=None, quality=DataQuality.EMPTY,
                assessment_time_ms=None, latest_close_time_ms=None, count=0,
                explanations=("No candles were supplied.",),
            )

    required = required_candle_count(config, indicator_config)
    if len(candles) < required:
        return _empty_assessment(
            symbol=symbol, interval=interval, quality=DataQuality.INSUFFICIENT,
            assessment_time_ms=assessment_time_ms, latest_close_time_ms=latest_close_time_ms,
            count=len(candles), explanations=(f"Insufficient closed candles: {len(candles)} supplied, {required} required for indicators and volatility baseline.",),
        )

    try:
        snapshot = calculate_latest_indicators(candles, indicator_config)
    except (ValueError, TypeError, AttributeError) as exc:
        return _empty_assessment(
            symbol=symbol, interval=interval, quality=DataQuality.INVALID,
            assessment_time_ms=assessment_time_ms, latest_close_time_ms=latest_close_time_ms,
            count=len(candles), explanations=(f"Candle validation failed ({type(exc).__name__}); regime forced to UNCERTAIN.",),
        )
    if snapshot is None:
        return _empty_assessment(
            symbol=symbol, interval=interval, quality=DataQuality.INSUFFICIENT,
            assessment_time_ms=assessment_time_ms, latest_close_time_ms=latest_close_time_ms,
            count=len(candles), explanations=("No indicator snapshot is available.",),
        )

    regime, separation, trend_explanations = _trend_label(snapshot, config)
    closes = tuple(float(candle.close) for candle in candles)
    volatility, ratio, volatility_explanations = _volatility_label(closes, config)
    if snapshot.volume_above_average is True:
        volume_activity = VolumeActivity.ABOVE_AVERAGE
        volume_explanations = ("Latest candle volume is above the shared indicator engine's current-inclusive volume average.",)
    elif snapshot.volume_above_average is False:
        volume_activity = VolumeActivity.AT_OR_BELOW_AVERAGE
        volume_explanations = ("Latest candle volume is at or below the shared indicator engine's current-inclusive volume average.",)
    else:
        volume_activity = VolumeActivity.UNKNOWN
        volume_explanations = ("Volume activity is unavailable during indicator warm-up.",)

    if batch is None and quality == DataQuality.FRESHNESS_UNVERIFIED:
        freshness_note = "Raw Candle sequence supplied; freshness cannot be verified without CandleBatch metadata."
    else:
        freshness_note = "CandleBatch passed its stale/gap gates."
    explanations = trend_explanations + volatility_explanations + volume_explanations + (freshness_note,)
    return RegimeAssessment(
        symbol=snapshot.symbol,
        interval=snapshot.interval,
        regime=regime,
        volatility=volatility,
        volume_activity=volume_activity,
        data_quality=quality,
        assessment_time_ms=assessment_time_ms,
        latest_candle_close_time_ms=snapshot.close_time_ms,
        closed_candle_count=len(candles),
        trend_separation_pct=separation,
        volatility_ratio=ratio,
        liquidity_assessment="not_assessed: spread/depth data is not part of this step",
        explanations=explanations,
    )


def summarize_timeframe_agreement(assessments: Sequence[RegimeAssessment]) -> TimeframeAgreement:
    """Summarize direction agreement without turning uncertainty into confirmation."""
    items = tuple(assessments)
    intervals = tuple(item.interval or "unknown" for item in items)
    if not items:
        return TimeframeAgreement(
            status=AgreementStatus.UNAVAILABLE,
            direction=RegimeLabel.UNCERTAIN,
            intervals=(),
            explanations=("No timeframe assessments were provided.",),
        )

    classified = [
        item.regime for item in items
        if item.regime != RegimeLabel.UNCERTAIN
        and item.data_quality in (DataQuality.USABLE, DataQuality.FRESHNESS_UNVERIFIED)
    ]
    unique = set(classified)
    if len(unique) > 1:
        return TimeframeAgreement(
            status=AgreementStatus.MIXED,
            direction=RegimeLabel.UNCERTAIN,
            intervals=intervals,
            explanations=("Timeframes disagree on trend/range classification; do not treat direction as confirmed.",),
        )
    all_usable = len(classified) == len(items)
    if len(items) == 1:
        status = AgreementStatus.SINGLE_TIMEFRAME if all_usable else AgreementStatus.INCOMPLETE
    elif all_usable and len(unique) == 1:
        status = AgreementStatus.AGREED
    else:
        status = AgreementStatus.INCOMPLETE
    direction = next(iter(unique)) if all_usable and len(unique) == 1 else RegimeLabel.UNCERTAIN
    explanation = (
        "All supplied timeframes agree." if status == AgreementStatus.AGREED
        else "One timeframe is available." if status == AgreementStatus.SINGLE_TIMEFRAME
        else "At least one timeframe is uncertain or fails data-quality requirements." if status == AgreementStatus.INCOMPLETE
        else "Timeframe agreement is unavailable."
    )
    return TimeframeAgreement(status, direction, intervals, (explanation,))