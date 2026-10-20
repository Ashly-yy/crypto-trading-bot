"""Chronological coordinator for prepared historical strategy decision points.

This module does not fetch market data or submit exchange orders.
Decision points must be prepared from information available at their
respective historical decision times. The supplied replay callable must
be the project's simulation-only replay implementation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from .engine import EntryIntent
from .pipeline import evaluate_authorized_entry


class BacktestOrchestrationError(ValueError):
    """Raised when historical orchestration inputs are unsafe or invalid."""


@dataclass(frozen=True)
class DecisionPoint:
    """One strategy evaluation prepared for a historical decision time."""

    decision_time_ms: int
    style: str
    entry_batch: Any
    context_batches: Mapping[str, Any]
    regime: Any
    config: Any
    cost_estimate: Any
    portfolio: Any
    risk_manager: Any
    risk_context: Any
    position_open: bool = False


@dataclass(frozen=True)
class OrchestrationReport:
    """Results from evaluating decision points and running simulated replay."""

    replay_result: Any
    evaluated_points: int
    approved_entries: int
    rejected_entries: int
    skipped_non_entry_signals: int
    approved_intents: tuple[EntryIntent, ...]


def _valid_timestamp(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def run_historical_orchestration(
    *,
    candles: Sequence[Any],
    decision_points: Iterable[DecisionPoint],
    replay_callable: Callable[..., Any],
    replay_kwargs: Mapping[str, Any] | None = None,
) -> OrchestrationReport:
    """Evaluate prepared decisions chronologically, then call simulation replay.

    Safety properties:
    - Decision points must be strictly chronological.
    - Every entry candidate goes through evaluate_authorized_entry.
    - Only risk-approved intents are passed to replay.
    - Replay is injected by the caller; no exchange client is created here.
    - Invalid inputs fail closed rather than being silently repaired.
    """
    from .history import validate_historical_candles

    if not callable(replay_callable):
        raise BacktestOrchestrationError("A simulation replay callable is required.")

    if not isinstance(candles, (tuple, list)) or not candles:
        raise BacktestOrchestrationError("Historical candles must be a non-empty list or tuple.")

    if not isinstance(decision_points, Iterable):
        raise BacktestOrchestrationError("Decision points must be iterable.")

    if replay_kwargs is not None and not isinstance(replay_kwargs, Mapping):
        raise BacktestOrchestrationError("replay_kwargs must be a mapping.")

    # Validate ordering and structure using the project's canonical validator.
    first = candles[0]
    symbol = getattr(first, "symbol", None)
    interval = getattr(first, "interval", None)
    if not isinstance(symbol, str) or not symbol or not isinstance(interval, str) or not interval:
        raise BacktestOrchestrationError("Historical candle symbol/interval is invalid.")

    try:
        validated_candles = validate_historical_candles(
            candles,
            symbol=symbol,
            interval=interval,
            require_contiguous=True,
        )
    except Exception as exc:
        raise BacktestOrchestrationError(
            f"Historical candle validation failed: {exc}"
        ) from exc

    candle_start = getattr(validated_candles[0], "open_time_ms", None)
    candle_end = getattr(validated_candles[-1], "close_time_ms", None)
    if not _valid_timestamp(candle_start) or not _valid_timestamp(candle_end):
        raise BacktestOrchestrationError("Historical candle timestamps are invalid.")

    points = list(decision_points)
    previous_time = -1

    for point in points:
        if not isinstance(point, DecisionPoint):
            raise BacktestOrchestrationError(
                "Each decision point must be a DecisionPoint instance."
            )
        if not _valid_timestamp(point.decision_time_ms):
            raise BacktestOrchestrationError("Decision timestamp is invalid.")
        if point.decision_time_ms <= previous_time:
            raise BacktestOrchestrationError(
                "Decision points must be strictly chronological with no duplicate timestamps."
            )
        if point.decision_time_ms < candle_start or point.decision_time_ms > candle_end + 1:
            raise BacktestOrchestrationError(
                "Decision timestamp falls outside the supplied historical candle range."
            )
        if not isinstance(point.context_batches, Mapping):
            raise BacktestOrchestrationError("Context batches must be a mapping.")
        previous_time = point.decision_time_ms

    approved_intents: list[EntryIntent] = []
    approved = 0
    rejected = 0
    skipped = 0

    for point in points:
        try:
            evaluation = evaluate_authorized_entry(
                point.style,
                point.entry_batch,
                point.context_batches,
                point.regime,
                config=point.config,
                cost_estimate=point.cost_estimate,
                portfolio=point.portfolio,
                risk_manager=point.risk_manager,
                risk_context=point.risk_context,
                position_open=point.position_open,
            )
        except Exception as exc:
            # An evaluation failure must never turn into an entry.
            raise BacktestOrchestrationError(
                f"Decision at {point.decision_time_ms} failed closed: {exc}"
            ) from exc

        intent = getattr(evaluation, "intent", None)
        authorization = getattr(evaluation, "authorization", None)
        signal = getattr(evaluation, "signal", None)

        if signal is None:
            raise BacktestOrchestrationError(
                f"Decision at {point.decision_time_ms} returned no signal."
            )

        action = getattr(
            getattr(signal, "action", None),
            "name",
            getattr(getattr(signal, "action", None), "value", ""),
        )
        action = str(action).strip().upper()

        if action not in ("BUY_CANDIDATE",):
            skipped += 1
            continue

        if authorization is None:
            rejected += 1
            continue

        is_approved = getattr(authorization, "approved", False)
        if (
            is_approved is not True
            or not isinstance(intent, EntryIntent)
            or intent.decision_time_ms != point.decision_time_ms
            or intent.symbol.upper() != symbol.upper()
        ):
            rejected += 1
            continue

        approved_intents.append(intent)
        approved += 1

    # The replay callable must be a simulation-only function supplied by
    # the caller. It receives only approved intents, never rejected signals.
    kwargs = dict(replay_kwargs or {})
    if "candles" in kwargs or "entry_intents" in kwargs:
        raise BacktestOrchestrationError(
            "replay_kwargs must not override the validated candles or approved intents."
        )

    try:
        replay_result = replay_callable(
            candles=validated_candles,
            entry_intents=tuple(approved_intents),
            **kwargs,
        )
    except Exception as exc:
        raise BacktestOrchestrationError(
            f"Simulation replay failed: {exc}"
        ) from exc

    return OrchestrationReport(
        replay_result=replay_result,
        evaluated_points=len(points),
        approved_entries=approved,
        rejected_entries=rejected,
        skipped_non_entry_signals=skipped,
        approved_intents=tuple(approved_intents),
    )
