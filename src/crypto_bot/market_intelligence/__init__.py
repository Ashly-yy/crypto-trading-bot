"""Explainable market intelligence and regime classification."""

from crypto_bot.market_intelligence.regime import (
    AgreementStatus,
    DataQuality,
    RegimeAssessment,
    RegimeConfig,
    RegimeLabel,
    TimeframeAgreement,
    VolatilityLabel,
    VolumeActivity,
    assess_market_regime,
    required_candle_count,
    summarize_timeframe_agreement,
)

__all__ = [
    "AgreementStatus",
    "DataQuality",
    "RegimeAssessment",
    "RegimeConfig",
    "RegimeLabel",
    "TimeframeAgreement",
    "VolatilityLabel",
    "VolumeActivity",
    "assess_market_regime",
    "required_candle_count",
    "summarize_timeframe_agreement",
]

# Step 10 optional external context exports
from crypto_bot.market_intelligence.external_sources import (
    ExternalIntelligenceSnapshot,
    ExternalSourceError,
    FearGreedReading,
    GlobalMarketReading,
    MarketBreadthReading,
    SentimentLabel,
    SourceFailure,
    SourceStatus,
    Ticker24hReading,
    fetch_external_intelligence,
    parse_binance_24h_ticker,
    parse_fear_greed_payload,
    parse_global_market_payload,
    summarize_market_breadth,
)

__all__ += [
    "ExternalIntelligenceSnapshot",
    "ExternalSourceError",
    "FearGreedReading",
    "GlobalMarketReading",
    "MarketBreadthReading",
    "SentimentLabel",
    "SourceFailure",
    "SourceStatus",
    "Ticker24hReading",
    "fetch_external_intelligence",
    "parse_binance_24h_ticker",
    "parse_fear_greed_payload",
    "parse_global_market_payload",
    "summarize_market_breadth",
]
