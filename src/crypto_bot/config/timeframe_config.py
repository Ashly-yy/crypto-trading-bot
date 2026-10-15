"""Approved candle intervals and strategy-specific timeframe configuration.

These are configurable data-analysis defaults, not validated trading signals.
"""

from __future__ import annotations

from dataclasses import dataclass

SUPPORTED_CANDLE_INTERVALS: tuple[str, ...] = (
    "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d",
)

TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1_800,
    "1h": 3_600,
    "2h": 7_200,
    "4h": 14_400,
    "1d": 86_400,
}


def parse_interval_csv(value: str, *, field_name: str) -> tuple[str, ...]:
    """Validate a comma-separated, case-sensitive interval list."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must contain at least one interval.")
    values = tuple(part.strip() for part in value.split(","))
    if any(not part for part in values):
        raise ValueError(f"{field_name} must not contain empty interval entries.")
    invalid = tuple(part for part in values if part not in SUPPORTED_CANDLE_INTERVALS)
    if invalid:
        raise ValueError(
            f"{field_name} contains unsupported interval(s): {', '.join(invalid)}. "
            f"Supported intervals: {', '.join(SUPPORTED_CANDLE_INTERVALS)}."
        )
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name} must not contain duplicate intervals.")
    return values


def validate_single_interval(value: str, *, field_name: str) -> str:
    """Validate one supported, case-sensitive interval name."""
    if not isinstance(value, str) or value not in SUPPORTED_CANDLE_INTERVALS:
        raise ValueError(
            f"{field_name} must be one of: {', '.join(SUPPORTED_CANDLE_INTERVALS)}."
        )
    return value


@dataclass(frozen=True)
class StrategyTimeframeMapping:
    """Entry interval plus higher-timeframe context intervals for one style."""

    style: str
    entry_interval: str
    context_intervals: tuple[str, ...]