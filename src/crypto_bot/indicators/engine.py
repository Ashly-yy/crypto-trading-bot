"""One causal, deterministic indicator implementation for all trading modes.

This module calculates features only. It does not create BUY/SELL signals,
connect to an exchange, change portfolio state, or submit orders.

Indicator conventions:
- EMA uses an initial-period simple average, then alpha=2/(period+1).
- MACD uses EMA(fast) - EMA(slow); its signal EMA is SMA-seeded from valid MACD values.
- RSI uses Wilder smoothing. A fully flat series maps to RSI 50; zero-loss-only maps to 100.
- Bollinger standard deviation uses the population divisor (N), not sample (N-1).
- Volume SMA includes the current candle, matching the simple rolling-volume feature
  used in the previous prototype. Strategy rules can later choose a lagged baseline.
- Values are None until their complete warm-up period has passed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from crypto_bot.market_data.candles import Candle, INTERVAL_MILLISECONDS


class IndicatorInputError(ValueError):
    """Raised when candle data is not safe for indicator calculation."""


@dataclass(frozen=True)
class IndicatorConfig:
    """Indicator parameters; pass the same config to backtest and paper paths."""

    ema_short_period: int = 5
    ema_long_period: int = 10
    macd_fast_period: int = 12
    macd_slow_period: int = 26
    macd_signal_period: int = 9
    rsi_period: int = 14
    bollinger_period: int = 20
    bollinger_std_dev: float = 2.0
    volume_period: int = 20

    def __post_init__(self) -> None:
        int_fields = (
            "ema_short_period", "ema_long_period", "macd_fast_period",
            "macd_slow_period", "macd_signal_period", "rsi_period",
            "bollinger_period", "volume_period",
        )
        for name in int_fields:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer.")
        if self.ema_short_period >= self.ema_long_period:
            raise ValueError("ema_short_period must be less than ema_long_period.")
        if self.macd_fast_period >= self.macd_slow_period:
            raise ValueError("macd_fast_period must be less than macd_slow_period.")
        if isinstance(self.bollinger_std_dev, bool) or not isinstance(self.bollinger_std_dev, (int, float)):
            raise ValueError("bollinger_std_dev must be a finite positive number.")
        if not math.isfinite(float(self.bollinger_std_dev)) or float(self.bollinger_std_dev) <= 0:
            raise ValueError("bollinger_std_dev must be a finite positive number.")


@dataclass(frozen=True)
class IndicatorSnapshot:
    """Indicator values computed using only candles up to this candle."""

    symbol: str
    interval: str
    open_time_ms: int
    close_time_ms: int
    close: float
    ema_short: float | None
    ema_long: float | None
    macd: float | None
    macd_signal: float | None
    macd_histogram: float | None
    rsi: float | None
    bollinger_upper: float | None
    bollinger_middle: float | None
    bollinger_lower: float | None
    volume_sma: float | None
    volume_above_average: bool | None

    @property
    def is_warmed_up(self) -> bool:
        """Whether all default strategy-facing features have valid values."""
        required = (
            self.ema_short, self.ema_long, self.macd, self.macd_signal,
            self.macd_histogram, self.rsi, self.bollinger_upper,
            self.bollinger_middle, self.bollinger_lower, self.volume_sma,
            self.volume_above_average,
        )
        return all(value is not None for value in required)


def required_warmup_candles(config: IndicatorConfig = IndicatorConfig()) -> int:
    """Minimum history needed for every configured indicator to be available."""
    return max(
        config.ema_long_period,
        config.macd_slow_period + config.macd_signal_period - 1,
        config.rsi_period + 1,
        config.bollinger_period,
        config.volume_period,
    )


def _finite_float(value: object, field_name: str) -> float:
    try:
        number = float(value)  # Decimal values from Candle are converted explicitly.
    except (TypeError, ValueError, OverflowError):
        raise IndicatorInputError(f"Candle {field_name} is not numeric.") from None
    if not math.isfinite(number):
        raise IndicatorInputError(f"Candle {field_name} must be finite.")
    return number


def _validate_candles(candles: Sequence[Candle]) -> tuple[list[float], list[float]]:
    if isinstance(candles, (str, bytes)):
        raise IndicatorInputError("candles must be a sequence of normalized Candle objects.")
    if not candles:
        return [], []

    # Check every element before accessing attributes. Malformed caller input should
    # always raise the engine's documented input error, not leak AttributeError.
    for index, candle in enumerate(candles):
        if not isinstance(candle, Candle):
            raise IndicatorInputError(
                f"Candle at index {index} is not a normalized Candle object from candles.py."
            )

    first = candles[0]
    symbol, interval = first.symbol, first.interval
    if not isinstance(symbol, str) or not symbol:
        raise IndicatorInputError("Candle symbol must be a non-empty string.")
    if not isinstance(interval, str) or not interval:
        raise IndicatorInputError("Candle interval must be a non-empty string.")
    interval_ms = INTERVAL_MILLISECONDS.get(interval)
    if interval_ms is None:
        raise IndicatorInputError(f"Interval {interval!r} is not supported by the indicator engine.")

    # Validate timestamp types and each candle's own boundaries before comparisons.
    # This prevents malformed values from escaping as TypeError during ordering checks.
    for index, candle in enumerate(candles):
        if candle.symbol != symbol or candle.interval != interval:
            raise IndicatorInputError("One indicator series must contain exactly one symbol and interval.")
        if isinstance(candle.open_time_ms, bool) or not isinstance(candle.open_time_ms, int):
            raise IndicatorInputError("Candle open timestamps must be integer milliseconds.")
        if isinstance(candle.close_time_ms, bool) or not isinstance(candle.close_time_ms, int):
            raise IndicatorInputError("Candle close timestamps must be integer milliseconds.")
        if candle.open_time_ms < 0:
            raise IndicatorInputError("Candle timestamps must be non-negative integer milliseconds.")
        # Binance weekly candles start on Monday 00:00 UTC. Since Unix epoch starts
        # on Thursday, a valid weekly open is offset by four days within each week.
        alignment_offset_ms = 4 * 86_400_000 if interval == "1w" else 0
        if candle.open_time_ms % interval_ms != alignment_offset_ms:
            raise IndicatorInputError(
                f"Candle at index {index} open timestamp is not aligned to its interval boundary."
            )

    # Validate ordering before gaps so swapped candles report the most useful error.
    for index in range(1, len(candles)):
        previous_open = candles[index - 1].open_time_ms
        current_open = candles[index].open_time_ms
        if current_open <= previous_open:
            raise IndicatorInputError("Candle timestamps must be strictly increasing without duplicates.")

    # Once types and ordering are known to be valid, enforce exact candle close times.
    for index, candle in enumerate(candles):
        expected_close_time_ms = candle.open_time_ms + interval_ms - 1
        if candle.close_time_ms != expected_close_time_ms:
            raise IndicatorInputError(
                f"Candle at index {index} close timestamp must equal interval close "
                f"({expected_close_time_ms} ms)."
            )

    closes: list[float] = []
    volumes: list[float] = []
    previous_open: int | None = None
    for candle in candles:
        if previous_open is not None:
            if candle.open_time_ms - previous_open != interval_ms:
                raise IndicatorInputError(
                    "Candle series contains a gap or irregular interval; validate/fill data before calculating indicators."
                )
        previous_open = candle.open_time_ms

        open_price = _finite_float(candle.open, "open")
        high = _finite_float(candle.high, "high")
        low = _finite_float(candle.low, "low")
        close = _finite_float(candle.close, "close")
        volume = _finite_float(candle.volume, "volume")
        if min(open_price, high, low, close) <= 0:
            raise IndicatorInputError("OHLC prices must be greater than zero.")
        if low > min(open_price, close) or high < max(open_price, close) or low > high:
            raise IndicatorInputError("Candle OHLC values are internally inconsistent.")
        if volume < 0:
            raise IndicatorInputError("Candle volume cannot be negative.")
        closes.append(close)
        volumes.append(volume)
    return closes, volumes
def _ema_series(values: Sequence[float | None], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    alpha = 2.0 / (period + 1.0)
    seed: list[float] = []
    current_ema: float | None = None
    for index, value in enumerate(values):
        if value is None:
            seed = []
            current_ema = None
            continue
        if not math.isfinite(value):
            raise IndicatorInputError("Indicator input contains a non-finite value.")
        if current_ema is None:
            seed.append(value)
            if len(seed) == period:
                current_ema = sum(seed) / period
                result[index] = current_ema
                seed = []
        else:
            current_ema = alpha * value + (1.0 - alpha) * current_ema
            result[index] = current_ema
    return result


def _rsi_series(closes: Sequence[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return result
    gains = [0.0] * len(closes)
    losses = [0.0] * len(closes)
    for index in range(1, len(closes)):
        change = closes[index] - closes[index - 1]
        gains[index] = max(change, 0.0)
        losses[index] = max(-change, 0.0)

    avg_gain = sum(gains[1:period + 1]) / period
    avg_loss = sum(losses[1:period + 1]) / period

    def to_rsi(gain: float, loss: float) -> float:
        if gain == 0.0 and loss == 0.0:
            return 50.0
        if loss == 0.0:
            return 100.0
        if gain == 0.0:
            return 0.0
        relative_strength = gain / loss
        return 100.0 - (100.0 / (1.0 + relative_strength))

    result[period] = to_rsi(avg_gain, avg_loss)
    for index in range(period + 1, len(closes)):
        avg_gain = ((period - 1) * avg_gain + gains[index]) / period
        avg_loss = ((period - 1) * avg_loss + losses[index]) / period
        result[index] = to_rsi(avg_gain, avg_loss)
    return result


def _rolling_sma(values: Sequence[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    running_sum = 0.0
    for index, value in enumerate(values):
        running_sum += value
        if index >= period:
            running_sum -= values[index - period]
        if index >= period - 1:
            result[index] = running_sum / period
    return result


def _bollinger_series(closes: Sequence[float], period: int, standard_deviations: float) -> tuple[list[float | None], list[float | None], list[float | None]]:
    upper: list[float | None] = [None] * len(closes)
    middle: list[float | None] = [None] * len(closes)
    lower: list[float | None] = [None] * len(closes)
    for index in range(period - 1, len(closes)):
        window = closes[index - period + 1:index + 1]
        mean = sum(window) / period
        variance = sum((value - mean) ** 2 for value in window) / period
        standard_deviation = math.sqrt(variance)
        middle[index] = mean
        upper[index] = mean + standard_deviations * standard_deviation
        lower[index] = mean - standard_deviations * standard_deviation
    return upper, middle, lower


def calculate_indicator_series(
    candles: Sequence[Candle], config: IndicatorConfig = IndicatorConfig()
) -> tuple[IndicatorSnapshot, ...]:
    """Return one causal indicator snapshot per validated, closed candle.

    Callers must pass closed, normalized candles from CandleBatch.candles (or an
    equivalently validated historical sequence). This function never consults
    future rows to calculate a snapshot. Gaps and mixed-symbol/interval data are
    rejected instead of silently interpolated.
    """
    closes, volumes = _validate_candles(candles)
    if not candles:
        return ()

    ema_short = _ema_series(closes, config.ema_short_period)
    ema_long = _ema_series(closes, config.ema_long_period)
    macd: list[float | None] = [
        (fast - slow) if fast is not None and slow is not None else None
        for fast, slow in zip(_ema_series(closes, config.macd_fast_period), _ema_series(closes, config.macd_slow_period))
    ]
    macd_signal = _ema_series(macd, config.macd_signal_period)
    macd_histogram = [
        (line - signal) if line is not None and signal is not None else None
        for line, signal in zip(macd, macd_signal)
    ]
    rsi = _rsi_series(closes, config.rsi_period)
    bb_upper, bb_middle, bb_lower = _bollinger_series(closes, config.bollinger_period, float(config.bollinger_std_dev))
    volume_sma = _rolling_sma(volumes, config.volume_period)

    output: list[IndicatorSnapshot] = []
    for index, candle in enumerate(candles):
        avg_volume = volume_sma[index]
        output.append(IndicatorSnapshot(
            symbol=candle.symbol,
            interval=candle.interval,
            open_time_ms=candle.open_time_ms,
            close_time_ms=candle.close_time_ms,
            close=closes[index],
            ema_short=ema_short[index],
            ema_long=ema_long[index],
            macd=macd[index],
            macd_signal=macd_signal[index],
            macd_histogram=macd_histogram[index],
            rsi=rsi[index],
            bollinger_upper=bb_upper[index],
            bollinger_middle=bb_middle[index],
            bollinger_lower=bb_lower[index],
            volume_sma=avg_volume,
            volume_above_average=(volumes[index] > avg_volume) if avg_volume is not None else None,
        ))
    return tuple(output)


def calculate_latest_indicators(
    candles: Sequence[Candle], config: IndicatorConfig = IndicatorConfig()
) -> IndicatorSnapshot | None:
    """Return the latest indicator snapshot, or None for empty input."""
    series = calculate_indicator_series(candles, config)
    return series[-1] if series else None