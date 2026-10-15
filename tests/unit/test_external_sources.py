from dataclasses import replace

import pytest

from crypto_bot.market_intelligence.external_sources import (
    ExternalSourceError,
    FearGreedReading,
    GlobalMarketReading,
    MarketBreadthReading,
    SentimentLabel,
    SourceStatus,
    Ticker24hReading,
    fetch_external_intelligence,
    parse_binance_24h_ticker,
    parse_fear_greed_payload,
    parse_global_market_payload,
    summarize_market_breadth,
)

NOW_MS = 1_800_000_000_000
FNG_PAYLOAD = {
    "name": "Fear and Greed Index",
    "data": [{"value": "40", "value_classification": "Fear", "timestamp": str((NOW_MS // 1000) - 3600)}],
    "metadata": {"error": None},
}
GLOBAL_PAYLOAD = {
    "data": {
        "bitcoin_percentage_of_market_cap": 56.2,
        "last_updated": str((NOW_MS // 1000) - 120),
        "quotes": {"USD": {"total_market_cap": 2_000_000_000_000, "total_volume_24h": 80_000_000_000}},
    },
    "metadata": {"error": None},
}


def test_parse_fear_greed_observation_and_normalize_classification() -> None:
    result = parse_fear_greed_payload(FNG_PAYLOAD, retrieved_at_ms=NOW_MS)
    assert isinstance(result, FearGreedReading)
    assert result.value == 40
    assert result.classification is SentimentLabel.FEAR
    assert result.status is SourceStatus.AVAILABLE
    assert result.observed_at_ms == (NOW_MS // 1000 - 3600) * 1000
    assert result.explanations


def test_fear_greed_stale_observation_is_retained_but_marked_stale() -> None:
    payload = {"data": [{"value": "25", "value_classification": "Fear", "timestamp": str((NOW_MS // 1000) - 50 * 3600)}]}
    result = parse_fear_greed_payload(payload, retrieved_at_ms=NOW_MS)
    assert result.value == 25
    assert result.status is SourceStatus.STALE
    assert any("do not treat it as current" in item for item in result.explanations)


@pytest.mark.parametrize("label", ["Extreme Fear", "Fear", "Neutral", "Greed", "Extreme Greed"])
def test_fear_greed_recognizes_supported_provider_labels(label: str) -> None:
    payload = {"data": [{"value": "50", "value_classification": label, "timestamp": str(NOW_MS // 1000)}]}
    result = parse_fear_greed_payload(payload, retrieved_at_ms=NOW_MS)
    assert result.classification.value == label.lower().replace(" ", "_")


@pytest.mark.parametrize("bad_value", ["101", "-1", "1.5", True, "NaN", None])
def test_fear_greed_rejects_invalid_values(bad_value: object) -> None:
    payload = {"data": [{"value": bad_value, "value_classification": "Fear", "timestamp": str(NOW_MS // 1000)}]}
    with pytest.raises(ExternalSourceError):
        parse_fear_greed_payload(payload, retrieved_at_ms=NOW_MS)


def test_fear_greed_rejects_future_timestamp() -> None:
    payload = {"data": [{"value": "55", "value_classification": "Neutral", "timestamp": str(NOW_MS // 1000 + 600)}]}
    with pytest.raises(ExternalSourceError, match="future"):
        parse_fear_greed_payload(payload, retrieved_at_ms=NOW_MS)


def test_fear_greed_rejects_provider_api_error_or_missing_data() -> None:
    with pytest.raises(ExternalSourceError, match="API error"):
        parse_fear_greed_payload({"data": [], "metadata": {"error": "rate limit"}}, retrieved_at_ms=NOW_MS)
    with pytest.raises(ExternalSourceError, match="no latest"):
        parse_fear_greed_payload({"data": []}, retrieved_at_ms=NOW_MS)


def test_global_market_parses_finite_bounded_usd_values() -> None:
    result = parse_global_market_payload(GLOBAL_PAYLOAD, retrieved_at_ms=NOW_MS)
    assert isinstance(result, GlobalMarketReading)
    assert result.total_market_cap_usd == 2_000_000_000_000
    assert result.total_volume_24h_usd == 80_000_000_000
    assert result.bitcoin_dominance_pct == 56.2
    assert result.status is SourceStatus.AVAILABLE
    assert len(result.explanations) >= 2


def test_global_market_stale_data_is_marked_stale() -> None:
    payload = {
        "data": {"bitcoin_percentage_of_market_cap": 40, "last_updated": str(NOW_MS // 1000 - 12 * 3600),
                 "quotes": {"USD": {"total_market_cap": 1, "total_volume_24h": 0}}}
    }
    assert parse_global_market_payload(payload, retrieved_at_ms=NOW_MS).status is SourceStatus.STALE


@pytest.mark.parametrize("dominance,cap,volume", [(101, 5, 1), (-1, 5, 1), (50, 0, 1), (50, 5, -1)])
def test_global_market_rejects_invalid_bounds(dominance: float, cap: float, volume: float) -> None:
    payload = {"data": {"bitcoin_percentage_of_market_cap": dominance, "last_updated": str(NOW_MS // 1000),
                         "quotes": {"USD": {"total_market_cap": cap, "total_volume_24h": volume}}}}
    with pytest.raises(ExternalSourceError):
        parse_global_market_payload(payload, retrieved_at_ms=NOW_MS)


def test_global_market_rejects_missing_usd_quote() -> None:
    with pytest.raises(ExternalSourceError, match="USD quote"):
        parse_global_market_payload({"data": {"quotes": {}, "last_updated": str(NOW_MS // 1000), "bitcoin_percentage_of_market_cap": 50}}, retrieved_at_ms=NOW_MS)


def test_binance_ticker_parses_requested_symbol_and_percentage() -> None:
    ticker = parse_binance_24h_ticker({"symbol": "btcusdt", "priceChangePercent": "2.5"}, expected_symbol="BTCUSDT", retrieved_at_ms=NOW_MS)
    assert ticker.symbol == "BTCUSDT"
    assert ticker.price_change_pct_24h == 2.5


def test_binance_ticker_rejects_symbol_mismatch_or_nonfinite_change() -> None:
    with pytest.raises(ExternalSourceError, match="does not match"):
        parse_binance_24h_ticker({"symbol": "ETHUSDT", "priceChangePercent": "1"}, expected_symbol="BTCUSDT", retrieved_at_ms=NOW_MS)
    with pytest.raises(ExternalSourceError):
        parse_binance_24h_ticker({"symbol": "BTCUSDT", "priceChangePercent": "NaN"}, expected_symbol="BTCUSDT", retrieved_at_ms=NOW_MS)


def ticker(symbol: str, change: float) -> Ticker24hReading:
    return Ticker24hReading(symbol, change, NOW_MS)


def test_breadth_summarizes_only_configured_watchlist() -> None:
    result = summarize_market_breadth(
        ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        [ticker("BTCUSDT", 2), ticker("ETHUSDT", -1), ticker("SOLUSDT", 0)],
        retrieved_at_ms=NOW_MS,
    )
    assert result.status is SourceStatus.AVAILABLE
    assert result.advancing_symbols == ("BTCUSDT",)
    assert result.declining_symbols == ("ETHUSDT",)
    assert result.unchanged_symbols == ("SOLUSDT",)
    assert any("configured watchlist" in item for item in result.explanations)


def test_breadth_partial_response_is_not_misrepresented_as_complete() -> None:
    result = summarize_market_breadth(["BTCUSDT", "ETHUSDT"], [ticker("BTCUSDT", 1)], retrieved_at_ms=NOW_MS)
    assert result.status is SourceStatus.PARTIAL
    assert result.observed_symbols == ("BTCUSDT",)
    assert "ETHUSDT" in result.explanations[0]


def test_breadth_with_no_tickers_is_unavailable() -> None:
    result = summarize_market_breadth(["BTCUSDT"], [], retrieved_at_ms=NOW_MS)
    assert result.status is SourceStatus.UNAVAILABLE
    assert result.observed_symbols == ()


@pytest.mark.parametrize("symbols", [[], ["BTCUSDT", "BTCUSDT"], ["../fng"], ["A" * 21]])
def test_breadth_rejects_empty_duplicate_or_invalid_symbols(symbols: list[str]) -> None:
    with pytest.raises(ExternalSourceError):
        summarize_market_breadth(symbols, [], retrieved_at_ms=NOW_MS)


def test_breadth_rejects_duplicate_or_unrequested_tickers() -> None:
    with pytest.raises(ExternalSourceError, match="duplicate"):
        summarize_market_breadth(["BTCUSDT"], [ticker("BTCUSDT", 1), ticker("BTCUSDT", -1)], retrieved_at_ms=NOW_MS)
    with pytest.raises(ExternalSourceError, match="match a requested"):
        summarize_market_breadth(["BTCUSDT"], [ticker("ETHUSDT", 1)], retrieved_at_ms=NOW_MS)


def test_fetch_configuration_validates_timeout_and_symbol_list_before_network(monkeypatch: pytest.MonkeyPatch) -> None:
    from crypto_bot.market_intelligence import external_sources

    monkeypatch.setattr(external_sources, "_fetch_source", lambda *args, **kwargs: (None, None))
    with pytest.raises(ValueError, match="timeout_seconds"):
        fetch_external_intelligence(["BTCUSDT"], timeout_seconds=0)
    with pytest.raises(ValueError, match="unique list"):
        fetch_external_intelligence(["BTCUSDT", "BTCUSDT"])


def test_unavailable_sources_are_independently_reportable(monkeypatch: pytest.MonkeyPatch) -> None:
    from crypto_bot.market_intelligence import external_sources

    def unavailable(name, url, parse, **kwargs):
        from crypto_bot.market_intelligence.external_sources import SourceFailure
        return None, SourceFailure(name, "network request failed (URLError)")

    monkeypatch.setattr(external_sources, "_fetch_source", unavailable)
    result = fetch_external_intelligence(["BTCUSDT", "ETHUSDT"], timeout_seconds=0.1)
    assert result.sentiment is None and result.global_market is None
    assert result.market_breadth is not None
    assert result.market_breadth.status is SourceStatus.UNAVAILABLE
    assert len(result.failures) == 4
    assert "does not create trading signals" in result.disclaimer
# Regression coverage for Alternative.me dominance ratio/percentage scale drift
@pytest.mark.parametrize(
    ("raw_dominance", "expected_pct"),
    [
        (0.649963500324002, 64.9963500324002),
        (0.65, 65.0),
        (0.99, 99.0),
    ],
)
def test_global_market_normalizes_fractional_dominance_to_percentage(
    raw_dominance: float, expected_pct: float
) -> None:
    data = dict(GLOBAL_PAYLOAD["data"])
    data["bitcoin_percentage_of_market_cap"] = raw_dominance
    payload = {**GLOBAL_PAYLOAD, "data": data}

    result = parse_global_market_payload(payload, retrieved_at_ms=NOW_MS)

    assert result.bitcoin_dominance_pct == pytest.approx(expected_pct)
    assert any("fractional ratio" in item.lower() for item in result.explanations)


def test_global_market_keeps_documented_percent_scale_unchanged() -> None:
    result = parse_global_market_payload(GLOBAL_PAYLOAD, retrieved_at_ms=NOW_MS)

    assert result.bitcoin_dominance_pct == 56.2
    assert not any("fractional ratio" in item.lower() for item in result.explanations)
