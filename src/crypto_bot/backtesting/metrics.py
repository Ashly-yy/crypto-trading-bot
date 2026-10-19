"""Transparent performance reporting for completed historical replay trades.

Returns are fractions (0.10 means 10%). Drawdown is based on realized
trade-close equity, not intratrade mark-to-market equity. This module does
not place orders and does not claim that backtested results predict profits.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from crypto_bot.backtesting.engine import ReplayResult, SimulatedTrade


class PerformanceMetricsError(ValueError):
    """Raised when performance inputs are incomplete or invalid."""


@dataclass(frozen=True)
class PerformanceReport:
    initial_equity: Decimal
    final_equity: Decimal
    net_pnl: Decimal
    net_return: Decimal
    max_drawdown: Decimal
    completed_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: Decimal
    profit_factor: Decimal | None
    expectancy_per_trade: Decimal
    average_winning_trade: Decimal | None
    average_losing_trade: Decimal | None
    trading_frequency_per_day: Decimal
    turnover_quote: Decimal
    transaction_costs_quote: Decimal
    time_in_market: Decimal
    net_pnl_by_asset: dict[str, Decimal]
    benchmark_return: Decimal | None
    excess_return_vs_benchmark: Decimal | None


def _decimal(value: Any, name: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise PerformanceMetricsError(f"{name} must be a finite decimal")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise PerformanceMetricsError(
            f"{name} must be a finite decimal"
        ) from None

    if not result.is_finite():
        raise PerformanceMetricsError(f"{name} must be a finite decimal")
    if positive and result <= 0:
        raise PerformanceMetricsError(f"{name} must be positive")
    return result


def calculate_performance(
    replay: ReplayResult,
    *,
    initial_equity: Decimal,
    period_start_ms: int,
    period_end_ms: int,
    benchmark_start_price: Decimal | None = None,
    benchmark_end_price: Decimal | None = None,
) -> PerformanceReport:
    """Calculate metrics for a completed replay and a declared evaluation period.

    A replay ending with an open position is rejected because this engine does
    not provide a final mark-to-market value for that position.
    """
    if not isinstance(replay, ReplayResult):
        raise PerformanceMetricsError("replay must be a ReplayResult")
    if replay.ending_position_open:
        raise PerformanceMetricsError(
            "Cannot report final equity while the replay has an open position"
        )

    initial = _decimal(initial_equity, "initial_equity", positive=True)

    for name, value in (
        ("period_start_ms", period_start_ms),
        ("period_end_ms", period_end_ms),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise PerformanceMetricsError(f"{name} must be a non-negative integer")
    if period_end_ms <= period_start_ms:
        raise PerformanceMetricsError("period_end_ms must be after period_start_ms")

    if (benchmark_start_price is None) != (benchmark_end_price is None):
        raise PerformanceMetricsError(
            "Supply both benchmark prices or neither"
        )

    benchmark_return = None
    if benchmark_start_price is not None:
        start_price = _decimal(
            benchmark_start_price, "benchmark_start_price", positive=True
        )
        end_price = _decimal(
            benchmark_end_price, "benchmark_end_price", positive=True
        )
        benchmark_return = (end_price - start_price) / start_price

    trades = tuple(replay.trades)
    duration_ms = period_end_ms - period_start_ms
    duration_days = Decimal(duration_ms) / Decimal(86_400_000)

    net_pnls: list[Decimal] = []
    winning: list[Decimal] = []
    losing: list[Decimal] = []
    net_by_asset: dict[str, Decimal] = {}
    turnover = Decimal("0")
    costs = Decimal("0")
    invested_time_ms = 0
    previous_exit_ms: int | None = None

    # This engine permits one position at a time. Validate that the supplied
    # completed trade records preserve that invariant and belong to the period.
    for trade in trades:
        if not isinstance(trade, SimulatedTrade):
            raise PerformanceMetricsError("Every trade must be a SimulatedTrade")

        entry = trade.entry
        exit_fill = trade.exit
        if entry.timestamp_ms < period_start_ms or exit_fill.timestamp_ms > period_end_ms:
            raise PerformanceMetricsError("Trade timestamps fall outside the evaluation period")
        if exit_fill.timestamp_ms <= entry.timestamp_ms:
            raise PerformanceMetricsError("Trade exit must occur after entry")
        if previous_exit_ms is not None and entry.timestamp_ms < previous_exit_ms:
            raise PerformanceMetricsError("Trades overlap or are not chronological")
        previous_exit_ms = exit_fill.timestamp_ms

        pnl = _decimal(trade.net_pnl_quote, "trade net PnL")
        entry_value = _decimal(entry.fill_price, "entry fill price", positive=True) * _decimal(
            entry.quantity, "entry quantity", positive=True
        )
        exit_value = _decimal(exit_fill.fill_price, "exit fill price", positive=True) * _decimal(
            exit_fill.quantity, "exit quantity", positive=True
        )
        if entry.quantity != exit_fill.quantity:
            raise PerformanceMetricsError("Entry and exit quantities do not match")

        entry_fee = _decimal(entry.fee_quote, "entry fee")
        exit_fee = _decimal(exit_fill.fee_quote, "exit fee")
        entry_slippage = _decimal(entry.slippage_cost_quote, "entry slippage")
        exit_slippage = _decimal(exit_fill.slippage_cost_quote, "exit slippage")
        if min(entry_fee, exit_fee, entry_slippage, exit_slippage) < 0:
            raise PerformanceMetricsError("Fees and slippage costs cannot be negative")

        net_pnls.append(pnl)
        if pnl > 0:
            winning.append(pnl)
        elif pnl < 0:
            losing.append(pnl)

        symbol = str(trade.symbol).strip().upper()
        if not symbol:
            raise PerformanceMetricsError("Trade symbol cannot be empty")
        net_by_asset[symbol] = net_by_asset.get(symbol, Decimal("0")) + pnl

        turnover += entry_value + exit_value
        costs += entry_fee + exit_fee + entry_slippage + exit_slippage
        invested_time_ms += exit_fill.timestamp_ms - entry.timestamp_ms

    if invested_time_ms > duration_ms:
        raise PerformanceMetricsError("Total time in market exceeds evaluation period")

    net_pnl = sum(net_pnls, Decimal("0"))
    final_equity = initial + net_pnl

    # Use realized equity after each completed trade. This is not a substitute
    # for mark-to-market drawdown during a trade.
    equity = initial
    peak = initial
    max_drawdown = Decimal("0")
    for pnl in net_pnls:
        equity += pnl
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)

    total_wins = sum(winning, Decimal("0"))
    total_losses_abs = abs(sum(losing, Decimal("0")))
    profit_factor = (
        total_wins / total_losses_abs if total_losses_abs > 0 else None
    )
    count = len(net_pnls)
    win_rate = Decimal(len(winning)) / Decimal(count) if count else Decimal("0")
    expectancy = net_pnl / Decimal(count) if count else Decimal("0")

    return PerformanceReport(
        initial_equity=initial,
        final_equity=final_equity,
        net_pnl=net_pnl,
        net_return=net_pnl / initial,
        max_drawdown=max_drawdown,
        completed_trades=count,
        winning_trades=len(winning),
        losing_trades=len(losing),
        win_rate=win_rate,
        profit_factor=profit_factor,
        expectancy_per_trade=expectancy,
        average_winning_trade=(
            total_wins / Decimal(len(winning)) if winning else None
        ),
        average_losing_trade=(
            sum(losing, Decimal("0")) / Decimal(len(losing)) if losing else None
        ),
        trading_frequency_per_day=Decimal(count) / duration_days,
        turnover_quote=turnover,
        transaction_costs_quote=costs,
        time_in_market=Decimal(invested_time_ms) / Decimal(duration_ms),
        net_pnl_by_asset=net_by_asset,
        benchmark_return=benchmark_return,
        excess_return_vs_benchmark=(
            net_pnl / initial - benchmark_return
            if benchmark_return is not None
            else None
        ),
    )
