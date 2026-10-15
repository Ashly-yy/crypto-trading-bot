from enum import Enum
from types import SimpleNamespace

import pytest

from crypto_bot.market_data.candles import (
    BinanceSpotCandleClient,
    CandleDataError,
    _next_open_time_ms,
    _resolve_interval_enum,
    parse_candle_row,
)

BASE = 1_700_000_000_000


class FakeInterval(Enum):
    ONE_MINUTE = "1m"
    FIVE_MINUTES = "5m"


def row(open_time=BASE, interval_ms=60_000, *, close="100", high="101", low="99", volume="2"):
    return [
        open_time,
        "100",
        high,
        low,
        close,
        volume,
        open_time + interval_ms - 1,
        "200",
        12,
        "1.0",
        "100",
        "0",
    ]


class FakeRest:
    def __init__(self, rows=None, error=None, server_time=None):
        self.rows = rows or []
        self.error = error
        self.server_time = server_time
        self.last_request = None

    def klines(self, **kwargs):
        self.last_request = kwargs
        if self.error:
            raise self.error
        return SimpleNamespace(data=lambda: self.rows)

    def time(self):
        return SimpleNamespace(data=lambda: SimpleNamespace(server_time=self.server_time))


class FakeSDK:
    def __init__(self, rows=None, error=None, server_time=None):
        self.rest_api = FakeRest(rows, error, server_time)


def client(rows, *, now_enum=FakeInterval, error=None):
    return BinanceSpotCandleClient(
        sdk_client=FakeSDK(rows=rows, error=error),
        interval_enum_type=now_enum,
    )


def test_parse_binance_array_row_and_decimal_prices():
    candle = parse_candle_row(row(), "BTCUSDT", "1m")
    assert candle.symbol == "BTCUSDT"
    assert str(candle.close) == "100"
    assert candle.number_of_trades == 12
    assert candle.open_time_utc.endswith("Z")


def test_parse_mapping_row_with_api_aliases():
    candle = parse_candle_row({
        "t": BASE, "o": "100", "h": "101", "l": "99", "c": "100",
        "v": "2", "T": BASE + 59_999, "q": "200", "n": 12,
        "V": "1", "Q": "100",
    }, "BTCUSDT", "1m")
    assert candle.close == 100


def test_incomplete_latest_candle_is_excluded():
    rows = [row(BASE), row(BASE + 60_000)]
    batch = client(rows).fetch_closed_candles(
        "BTCUSDT", "1m", 2, as_of_time_ms=BASE + 61_000
    )
    assert len(batch.candles) == 1
    assert batch.incomplete_candles_dropped == 1
    assert batch.is_usable


def test_detects_missing_candle_gap():
    rows = [row(BASE), row(BASE + 120_000)]
    batch = client(rows).fetch_closed_candles(
        "BTCUSDT", "1m", 2, as_of_time_ms=BASE + 181_000
    )
    assert len(batch.gaps) == 1
    assert batch.gaps[0].missing_candle_count == 1
    assert not batch.is_usable


def test_duplicate_open_time_is_rejected():
    with pytest.raises(CandleDataError, match="duplicate"):
        client([row(BASE), row(BASE)]).fetch_closed_candles(
            "BTCUSDT", "1m", 2, as_of_time_ms=BASE + 181_000
        )


def test_out_of_order_candles_are_rejected():
    with pytest.raises(CandleDataError, match="chronological"):
        client([row(BASE + 60_000), row(BASE)]).fetch_closed_candles(
            "BTCUSDT", "1m", 2, as_of_time_ms=BASE + 181_000
        )


def test_invalid_ohlc_is_rejected():
    with pytest.raises(CandleDataError, match="OHLC"):
        parse_candle_row(row(high="98", low="99"), "BTCUSDT", "1m")


def test_negative_volume_is_rejected():
    with pytest.raises(CandleDataError, match="cannot be negative"):
        parse_candle_row(row(volume="-1"), "BTCUSDT", "1m")


def test_nan_price_is_rejected():
    with pytest.raises(CandleDataError, match="finite"):
        parse_candle_row(row(close="NaN"), "BTCUSDT", "1m")


def test_limit_bounds_are_checked_before_api_call():
    fake = FakeSDK(rows=[row()])
    candle_client = BinanceSpotCandleClient(fake, FakeInterval)
    with pytest.raises(ValueError, match="limit"):
        candle_client.fetch_closed_candles("BTCUSDT", "1m", 1001, as_of_time_ms=BASE + 61_000)
    assert fake.rest_api.last_request is None


def test_unknown_symbol_format_is_rejected():
    with pytest.raises(ValueError, match="Symbol"):
        client([row()]).fetch_closed_candles("BTC/USDT", "1m", 1, as_of_time_ms=BASE + 61_000)


def test_stale_data_is_not_usable():
    batch = client([row()]).fetch_closed_candles(
        "BTCUSDT", "1m", 1, as_of_time_ms=BASE + 5 * 60_000
    )
    assert batch.is_stale
    assert not batch.is_usable


def test_empty_response_is_not_usable():
    batch = client([]).fetch_closed_candles("BTCUSDT", "1m", 1, as_of_time_ms=BASE + 61_000)
    assert not batch.candles
    assert not batch.is_usable


def test_sdk_request_error_is_sanitized():
    error = RuntimeError("private-response-secret=DO_NOT_LEAK")
    with pytest.raises(CandleDataError) as captured:
        client([], error=error).fetch_closed_candles("BTCUSDT", "1m", 1, as_of_time_ms=BASE + 61_000)
    assert "DO_NOT_LEAK" not in str(captured.value)


def test_interval_enum_is_resolved_by_value_not_member_name():
    assert _resolve_interval_enum("5m", FakeInterval) is FakeInterval.FIVE_MINUTES


def test_monthly_interval_advances_to_next_calendar_month():
    jan = 1_704_067_200_000  # 2024-01-01T00:00:00Z
    nxt = _next_open_time_ms(jan, "1M")
    assert nxt == 1_706_745_600_000  # 2024-02-01T00:00:00Z


def test_server_time_method_parses_exchange_time():
    fake = FakeSDK(server_time=BASE + 123)
    candle_client = BinanceSpotCandleClient(fake, FakeInterval)
    assert candle_client.server_time_ms() == BASE + 123


def test_missing_server_time_fails_safely():
    fake = FakeSDK(server_time=None)
    candle_client = BinanceSpotCandleClient(fake, FakeInterval)
    with pytest.raises(CandleDataError, match="omitted"):
        candle_client.server_time_ms()