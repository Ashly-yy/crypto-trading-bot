"""Read-only Binance Spot public kline stream monitor.

REST market eligibility remains handled by the official Binance Spot SDK. This
short-lived public stream observer uses aiohttp directly against Binance's
published raw-stream endpoint, then passes messages through the same validator.
It never authenticates, reads account information, calculates strategy signals,
or submits orders.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from crypto_bot.config.settings import get_settings
from crypto_bot.market_data.binance_spot import (
    BinanceMarketDataError,
    BinanceSpotMarketDiscoveryClient,
    create_public_spot_client,
)
from crypto_bot.market_data.candles import (
    Candle,
    CandleDataError,
    INTERVAL_MILLISECONDS,
    _field,
    _next_open_time_ms,
    _plain,
    parse_candle_row,
    utc_iso,
)

_PUBLIC_STREAM_BASE = "wss://stream.binance.com:9443/ws"
_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]{5,20}$")


def _build_stream_url(symbol: str, interval: str) -> str:
    """Build a validated raw public Spot kline stream URL."""
    normalized_symbol = symbol.strip().upper()
    if not _SYMBOL_PATTERN.fullmatch(normalized_symbol):
        raise ValueError("Symbol must be a valid configured exchange symbol.")
    if interval not in INTERVAL_MILLISECONDS:
        raise ValueError("Unsupported kline interval.")
    return f"{_PUBLIC_STREAM_BASE}/{normalized_symbol.lower()}@kline_{interval}"


def _safe_exception_detail(exc: BaseException, limit: int = 240) -> str:
    """Return concise diagnostics with common credential patterns and URLs redacted."""
    message = str(exc).splitlines()[0].strip() if str(exc).strip() else "No detail supplied."
    message = re.sub(
        r"(?i)\b(api[_-]?(?:key|secret)|token|authorization|password)(\s*[:=]\s*)([^\s,;]+)",
        r"\1\2<redacted>",
        message,
    )
    message = re.sub(r"(?i)\bbearer\s+\S+", "Bearer <redacted>", message)
    message = re.sub(r"wss?://[^\s\"']+", "<websocket-url-redacted>", message)
    return message[:limit] or "No detail supplied."


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.strip().lower() in {"true", "1", "yes"}:
            return True
        if value.strip().lower() in {"false", "0", "no"}:
            return False
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


@dataclass
class KlineStreamStats:
    symbol: str
    interval: str
    messages_received: int = 0
    forming_updates: int = 0
    closed_candles: int = 0
    rejected_messages: int = 0
    quality_issues: list[str] = field(default_factory=list)
    latest_event_time_ms: int | None = None
    current_open_time_ms: int | None = None
    current_candle_closed: bool = False
    last_closed_open_time_ms: int | None = None

    @property
    def is_healthy(self) -> bool:
        return self.messages_received > 0 and self.rejected_messages == 0 and not self.quality_issues


class KlineStreamMonitor:
    """Validate public kline messages without executing any strategy."""

    def __init__(self, symbol: str, interval: str, *, clock_ms=None, max_event_age_ms: int = 120_000) -> None:
        self.symbol = symbol.upper()
        self.interval = interval
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._max_event_age_ms = max_event_age_ms
        self.stats = KlineStreamStats(self.symbol, interval)
        self._printed_forming_for_open: set[int] = set()
        self._last_closed: Candle | None = None

    def handle_error(self, error: Any) -> None:
        self.stats.quality_issues.append(f"WebSocket reported an error ({type(error).__name__}).")
        logging.getLogger("crypto_bot.market_data.stream").error(
            "Public kline stream reported an error (%s).", type(error).__name__
        )

    def handle_message(self, message: Any) -> None:
        self.stats.messages_received += 1
        try:
            payload = _plain(message)
            if isinstance(payload, dict) and "data" in payload and "stream" in payload:
                payload = payload["data"]
            kline_raw = _field(payload, "k", "kline", default=None)
            if kline_raw is None:
                raise CandleDataError("WebSocket event did not contain a kline payload.")
            kline = _plain(kline_raw)
            event_symbol = str(
                _field(payload, "s", "symbol", default=_field(kline, "s", "symbol", default=""))
            ).upper()
            event_interval = str(_field(kline, "i", "interval", default=""))
            if event_symbol != self.symbol:
                raise CandleDataError("WebSocket event symbol did not match the requested symbol.")
            if event_interval != self.interval:
                raise CandleDataError("WebSocket event interval did not match the requested interval.")
            event_time = _as_int(_field(payload, "E", "event_time", "eventTime", default=None))
            if event_time is None:
                raise CandleDataError("WebSocket event timestamp is missing or invalid.")
            now_ms = int(self._clock_ms())
            if abs(now_ms - event_time) > self._max_event_age_ms:
                raise CandleDataError("WebSocket event is stale or the local clock differs materially from exchange time.")
            if self.stats.latest_event_time_ms is not None and event_time <= self.stats.latest_event_time_ms:
                raise CandleDataError("Duplicate or out-of-order WebSocket event timestamp.")

            candle = parse_candle_row(kline, self.symbol, self.interval)
            closed = _as_bool(_field(kline, "x", "is_closed", "isKlineClosed", default=None))
            if closed is None:
                raise CandleDataError("WebSocket event omitted a valid candle-closed flag.")

            current_open = self.stats.current_open_time_ms
            if current_open is not None:
                if candle.open_time_ms < current_open:
                    raise CandleDataError("WebSocket candle arrived out of chronological order.")
                if candle.open_time_ms > current_open:
                    if not self.stats.current_candle_closed:
                        self.stats.quality_issues.append(
                            f"A new candle began at {utc_iso(candle.open_time_ms)} before the prior candle's close event was observed."
                        )
                    else:
                        expected = _next_open_time_ms(current_open, self.interval)
                        if candle.open_time_ms != expected:
                            self.stats.quality_issues.append(
                                f"Candle stream gap: expected {utc_iso(expected)}, received {utc_iso(candle.open_time_ms)}."
                            )
                    self.stats.current_open_time_ms = candle.open_time_ms
                    self.stats.current_candle_closed = False
                elif self.stats.current_candle_closed:
                    raise CandleDataError("A second update arrived for a candle already marked closed.")
            else:
                self.stats.current_open_time_ms = candle.open_time_ms

            self.stats.latest_event_time_ms = event_time
            if not closed:
                self.stats.forming_updates += 1
                if candle.close_time_ms < event_time:
                    raise CandleDataError("A forming candle has a close time earlier than the event timestamp.")
                if candle.open_time_ms not in self._printed_forming_for_open:
                    print(f"[FORMING; not final data] {self.symbol} {self.interval} open={candle.open_time_utc}")
                    self._printed_forming_for_open.add(candle.open_time_ms)
                return

            self.stats.current_candle_closed = True
            self.stats.closed_candles += 1
            self.stats.last_closed_open_time_ms = candle.open_time_ms
            self._last_closed = candle
            print(
                f"[CLOSED] {self.symbol} {self.interval} open={candle.open_time_utc} "
                f"close={candle.close} volume={candle.volume}"
            )
        except Exception as exc:
            self.stats.rejected_messages += 1
            reason = str(exc) if isinstance(exc, CandleDataError) else f"Unexpected parsing error ({type(exc).__name__})."
            logging.getLogger("crypto_bot.market_data.stream").warning("Rejected stream message: %s", reason)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Observe one public Binance Spot kline stream; no trading.")
    parser.add_argument("--symbol", default="BTCUSDT", help="Configured market to observe (default: BTCUSDT).")
    parser.add_argument(
        "--interval", default="1m", choices=tuple(INTERVAL_MILLISECONDS),
        help="Candle interval (default: 1m).",
    )
    parser.add_argument("--seconds", type=int, default=20, help="How long to observe the stream (5-300 seconds; default 20).")
    return parser


async def _run_stream(symbol: str, interval: str, seconds: int, monitor: KlineStreamMonitor) -> int:
    """Observe the official raw public stream; no credentials are accepted or used."""
    try:
        import aiohttp

        url = _build_stream_url(symbol, interval)
        timeout = aiohttp.ClientTimeout(total=seconds + 10, sock_connect=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.ws_connect(url, heartbeat=20, autoping=True) as websocket:
                print("WebSocket handshake: SUCCESS (public raw stream)")
                loop = asyncio.get_running_loop()
                deadline = loop.time() + seconds
                while loop.time() < deadline:
                    remaining = deadline - loop.time()
                    try:
                        message = await websocket.receive(timeout=remaining)
                    except asyncio.TimeoutError:
                        break
                    if message.type == aiohttp.WSMsgType.TEXT:
                        try:
                            payload = json.loads(message.data)
                        except (json.JSONDecodeError, TypeError):
                            monitor.stats.messages_received += 1
                            monitor.stats.rejected_messages += 1
                            logging.getLogger("crypto_bot.market_data.stream").warning(
                                "Rejected non-JSON WebSocket text message."
                            )
                            continue
                        monitor.handle_message(payload)
                    elif message.type == aiohttp.WSMsgType.ERROR:
                        monitor.handle_error(websocket.exception() or RuntimeError("WebSocket receive error"))
                        break
                    elif message.type in (
                        aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.CLOSE,
                        aiohttp.WSMsgType.CLOSING,
                    ):
                        monitor.stats.quality_issues.append("WebSocket closed before the observation window completed.")
                        break
                return 0 if monitor.stats.is_healthy else 2
    except Exception as exc:
        detail = _safe_exception_detail(exc)
        logging.getLogger("crypto_bot.market_data.stream").error(
            "Public kline stream failed safely (%s): %s", type(exc).__name__, detail
        )
        print(f"Public kline stream failed safely ({type(exc).__name__}): {detail}. No orders were submitted.")
        return 2


def main() -> int:
    args = build_parser().parse_args()
    settings = get_settings()
    symbol = args.symbol.strip().upper()
    if symbol not in set(settings.market_symbol_list):
        print("Refusing an unconfigured symbol. Add it to MARKET_SYMBOLS before observing it.")
        return 2
    if not 5 <= args.seconds <= 300:
        print("--seconds must be between 5 and 300; no connection was made.")
        return 2
    try:
        # Public REST discovery verifies the pair before opening the public stream.
        discovery = BinanceSpotMarketDiscoveryClient(
            symbols=(symbol,), quote_asset=settings.market_quote_asset,
            excluded_symbols=settings.excluded_market_symbol_list,
            sdk_client=create_public_spot_client(),
        )
        eligibility = discovery.discover()[0]
    except BinanceMarketDataError as exc:
        print(f"Market eligibility check failed safely: {_safe_exception_detail(exc)}")
        return 2
    if not eligibility.eligible:
        print(f"Refusing to subscribe to {symbol}: market is not confirmed eligible. {eligibility.reason}")
        return 2

    monitor = KlineStreamMonitor(symbol, args.interval)
    print("\nBINANCE SPOT PUBLIC KLINE STREAM")
    print(f"Symbol: {symbol}; interval: {args.interval}; duration: {args.seconds}s")
    print("Authentication: none; this command observes data only and cannot submit orders.")
    result = asyncio.run(_run_stream(symbol, args.interval, args.seconds, monitor))
    print("\nSTREAM SUMMARY")
    print(f"Messages received: {monitor.stats.messages_received}")
    print(f"Forming updates observed (not treated as closed candles): {monitor.stats.forming_updates}")
    print(f"Closed candles observed: {monitor.stats.closed_candles}")
    print(f"Rejected messages: {monitor.stats.rejected_messages}")
    print(f"Data-quality warnings: {len(monitor.stats.quality_issues)}")
    for issue in monitor.stats.quality_issues[:5]:
        print(f"WARNING: {issue}")
    if monitor.stats.messages_received == 0:
        print("No valid market messages were received during the observation window.")
    elif monitor.stats.messages_received and not monitor.stats.closed_candles:
        print("No candle closed during this short window; forming updates were not treated as final data.")
    print("Stream observation finished. No account data or orders were accessed.")
    return 0 if result == 0 and monitor.stats.is_healthy else 2


if __name__ == "__main__":
    raise SystemExit(main())