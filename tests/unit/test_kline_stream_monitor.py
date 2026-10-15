from crypto_bot.market_data.stream_klines import KlineStreamMonitor

BASE = 1_700_000_000_000


def message(open_time, event_time, *, closed=False, symbol="BTCUSDT", interval="1m"):
    return {
        "e": "kline", "E": event_time, "s": symbol,
        "k": {
            "t": open_time, "T": open_time + 59_999,
            "s": symbol, "i": interval,
            "o": "100", "c": "100", "h": "101", "l": "99",
            "v": "2", "q": "200", "n": 12, "V": "1", "Q": "100",
            "x": closed,
        },
    }


def test_forming_update_is_not_counted_as_closed(monkeypatch, capsys):
    now = BASE + 1_000
    monitor = KlineStreamMonitor("BTCUSDT", "1m", clock_ms=lambda: now)
    monitor.handle_message(message(BASE, now, closed=False))
    assert monitor.stats.messages_received == 1
    assert monitor.stats.forming_updates == 1
    assert monitor.stats.closed_candles == 0
    assert "FORMING; not final data" in capsys.readouterr().out


def test_closed_update_is_counted_as_closed(capsys):
    now = BASE + 60_100
    monitor = KlineStreamMonitor("BTCUSDT", "1m", clock_ms=lambda: now)
    monitor.handle_message(message(BASE, BASE + 60_050, closed=True))
    assert monitor.stats.closed_candles == 1
    assert monitor.stats.last_closed_open_time_ms == BASE
    assert "[CLOSED]" in capsys.readouterr().out


def test_wrong_symbol_message_is_rejected():
    monitor = KlineStreamMonitor("BTCUSDT", "1m", clock_ms=lambda: BASE + 1_000)
    monitor.handle_message(message(BASE, BASE + 1_000, symbol="ETHUSDT"))
    assert monitor.stats.rejected_messages == 1
    assert not monitor.stats.is_healthy


def test_stale_message_is_rejected():
    monitor = KlineStreamMonitor("BTCUSDT", "1m", clock_ms=lambda: BASE + 400_000)
    monitor.handle_message(message(BASE, BASE + 1_000))
    assert monitor.stats.rejected_messages == 1


def test_duplicate_event_time_is_rejected():
    monitor = KlineStreamMonitor("BTCUSDT", "1m", clock_ms=lambda: BASE + 2_000)
    monitor.handle_message(message(BASE, BASE + 1_000))
    monitor.handle_message(message(BASE, BASE + 1_000))
    assert monitor.stats.rejected_messages == 1


def test_missed_previous_close_is_flagged():
    times = iter([BASE + 1_000, BASE + 62_000])
    monitor = KlineStreamMonitor("BTCUSDT", "1m", clock_ms=lambda: next(times))
    monitor.handle_message(message(BASE, BASE + 1_000, closed=False))
    monitor.handle_message(message(BASE + 60_000, BASE + 62_000, closed=False))
    assert monitor.stats.quality_issues
    assert not monitor.stats.is_healthy


def test_gap_between_closed_candles_is_flagged():
    times = iter([BASE + 60_100, BASE + 180_100])
    monitor = KlineStreamMonitor("BTCUSDT", "1m", clock_ms=lambda: next(times))
    monitor.handle_message(message(BASE, BASE + 60_050, closed=True))
    monitor.handle_message(message(BASE + 120_000, BASE + 180_050, closed=True))
    assert any("stream gap" in issue.lower() for issue in monitor.stats.quality_issues)


def test_combined_stream_wrapping_is_supported():
    now = BASE + 1_000
    monitor = KlineStreamMonitor("BTCUSDT", "1m", clock_ms=lambda: now)
    wrapped = {"stream": "btcusdt@kline_1m", "data": message(BASE, now)}
    monitor.handle_message(wrapped)
    assert monitor.stats.forming_updates == 1
from crypto_bot.market_data.stream_klines import _safe_exception_detail


def test_safe_exception_detail_redacts_secrets_and_urls():
    detail = _safe_exception_detail(
        ValueError("api_secret=DO_NOT_SHARE token:VERY_PRIVATE wss://example.invalid/path")
    )
    assert "DO_NOT_SHARE" not in detail
    assert "VERY_PRIVATE" not in detail
    assert "example.invalid" not in detail
    assert "<redacted>" in detail


def test_safe_exception_detail_preserves_useful_error_message():
    assert _safe_exception_detail(ValueError("Invalid websocket configuration")) == "Invalid websocket configuration"

from crypto_bot.market_data.stream_klines import _build_stream_url


def test_build_stream_url_uses_official_public_raw_kline_endpoint():
    assert _build_stream_url("BTCUSDT", "1m") == "wss://stream.binance.com:9443/ws/btcusdt@kline_1m"


def test_build_stream_url_normalizes_symbol_case_and_whitespace():
    assert _build_stream_url(" btcusdt ", "5m") == "wss://stream.binance.com:9443/ws/btcusdt@kline_5m"


def test_build_stream_url_rejects_invalid_symbol_and_interval():
    import pytest

    with pytest.raises(ValueError, match="Symbol"):
        _build_stream_url("BTC/USDT", "1m")
    with pytest.raises(ValueError, match="interval"):
        _build_stream_url("BTCUSDT", "not-an-interval")
