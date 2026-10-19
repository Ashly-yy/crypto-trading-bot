from decimal import Decimal

import pytest

from crypto_bot.backtesting.engine import ReplayResult, SimulatedTrade
from crypto_bot.backtesting.fills import (
    IntrabarExit,
    OrderSide,
    SimulatedFill,
)
from crypto_bot.backtesting.metrics import (
    PerformanceMetricsError,
    calculate_performance,
)


def fill(side, timestamp, price, fee="1", slippage="0.5"):
    return SimulatedFill(
        symbol="BTCUSDT",
        side=side,
        timestamp_ms=timestamp,
        reference_price=Decimal(price),
        fill_price=Decimal(price),
        quantity=Decimal("1"),
        fee_quote=Decimal(fee),
        slippage_cost_quote=Decimal(slippage),
    )


def trade(entry_time, exit_time, pnl, symbol="BTCUSDT"):
    entry = fill(OrderSide.BUY, entry_time, "100")
    exit_fill = fill(OrderSide.SELL, exit_time, "100")
    return SimulatedTrade(
        symbol=symbol,
        entry=entry,
        exit=exit_fill,
        exit_reason=IntrabarExit.STOP,
        gross_pnl_quote=Decimal(pnl) + Decimal("2"),
        net_pnl_quote=Decimal(pnl),
    )


def replay(*trades, open_position=False):
    return ReplayResult(
        trades=tuple(trades),
        rejected_intents=(),
        skipped_busy_intents=0,
        ending_position_open=open_position,
    )


def report(result, **kwargs):
    defaults = {
        "initial_equity": Decimal("1000"),
        "period_start_ms": 0,
        "period_end_ms": 86_400_000 * 10,
    }
    defaults.update(kwargs)
    return calculate_performance(result, **defaults)


def test_empty_replay_reports_zero_return_and_no_trades():
    result = report(replay())
    assert result.initial_equity == Decimal("1000")
    assert result.final_equity == Decimal("1000")
    assert result.net_return == 0
    assert result.completed_trades == 0
    assert result.profit_factor is None


def test_core_metrics_include_net_pnl_and_costs():
    result = report(replay(
        trade(100, 200, "10"),
        trade(300, 400, "-4"),
        trade(500, 600, "6"),
    ))
    assert result.net_pnl == Decimal("12")
    assert result.final_equity == Decimal("1012")
    assert result.net_return == Decimal("12") / Decimal("1000")
    assert result.completed_trades == 3
    assert result.winning_trades == 2
    assert result.losing_trades == 1
    assert result.win_rate == Decimal("2") / Decimal("3")
    assert result.profit_factor == Decimal("16") / Decimal("4")
    assert result.expectancy_per_trade == Decimal("4")
    assert result.average_winning_trade == Decimal("8")
    assert result.average_losing_trade == Decimal("-4")
    assert result.transaction_costs_quote == Decimal("9")
    assert result.turnover_quote == Decimal("600")


def test_drawdown_uses_realized_trade_close_equity():
    result = report(replay(
        trade(100, 200, "100"),
        trade(300, 400, "-50"),
    ))
    assert result.max_drawdown == Decimal("50") / Decimal("1100")


def test_benchmark_comparison_is_explicit():
    result = report(
        replay(trade(100, 200, "100")),
        benchmark_start_price=Decimal("100"),
        benchmark_end_price=Decimal("110"),
    )
    assert result.benchmark_return == Decimal("0.1")
    assert result.excess_return_vs_benchmark == Decimal("0")


def test_per_asset_pnl_is_grouped():
    result = report(replay(
        trade(100, 200, "10", "BTCUSDT"),
        trade(300, 400, "-3", "ETHUSDT"),
    ))
    assert result.net_pnl_by_asset == {
        "BTCUSDT": Decimal("10"),
        "ETHUSDT": Decimal("-3"),
    }


def test_time_in_market_and_frequency():
    result = report(replay(trade(100, 200, "5")))
    assert result.time_in_market == Decimal("100") / Decimal(864_000_000)
    assert result.trading_frequency_per_day == Decimal("0.1")


def test_open_position_is_rejected():
    with pytest.raises(PerformanceMetricsError, match="open position"):
        report(replay(open_position=True))


def test_invalid_period_is_rejected():
    with pytest.raises(PerformanceMetricsError, match="after"):
        report(replay(), period_start_ms=100, period_end_ms=100)


def test_one_missing_benchmark_price_is_rejected():
    with pytest.raises(PerformanceMetricsError, match="both benchmark"):
        report(replay(), benchmark_start_price=Decimal("100"))


def test_nonpositive_benchmark_price_is_rejected():
    with pytest.raises(PerformanceMetricsError, match="positive"):
        report(
            replay(),
            benchmark_start_price=Decimal("0"),
            benchmark_end_price=Decimal("100"),
        )


def test_out_of_period_trade_is_rejected():
    with pytest.raises(PerformanceMetricsError, match="outside"):
        report(replay(trade(1, 900_000_000, "5")))


def test_overlapping_trades_are_rejected():
    with pytest.raises(PerformanceMetricsError, match="overlap"):
        report(replay(
            trade(100, 400, "5"),
            trade(300, 500, "2"),
        ))


def test_negative_costs_are_rejected():
    bad_entry = SimulatedFill(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        timestamp_ms=100,
        reference_price=Decimal("100"),
        fill_price=Decimal("100"),
        quantity=Decimal("1"),
        fee_quote=Decimal("-1"),
        slippage_cost_quote=Decimal("0"),
    )
    exit_fill = fill(OrderSide.SELL, 200, "100")
    bad_trade = SimulatedTrade(
        symbol="BTCUSDT",
        entry=bad_entry,
        exit=exit_fill,
        exit_reason=IntrabarExit.STOP,
        gross_pnl_quote=Decimal("0"),
        net_pnl_quote=Decimal("0"),
    )
    with pytest.raises(PerformanceMetricsError, match="cannot be negative"):
        report(replay(bad_trade))


def test_profit_factor_is_none_when_there_are_no_losses():
    result = report(replay(trade(100, 200, "5")))
    assert result.profit_factor is None
    assert result.average_losing_trade is None
