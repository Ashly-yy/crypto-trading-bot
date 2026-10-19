from decimal import Decimal
import pytest
from crypto_bot.portfolio import Portfolio, Position

D = Decimal

def test_initial_portfolio_is_all_cash():
    p = Portfolio(D("1000")); s = p.snapshot({})
    assert (s.cash, s.equity, s.total_exposure) == (D("1000"), D("1000"), D("0"))

def test_buy_includes_fee_in_cost_basis_and_cash_debit():
    p = Portfolio(D("1000")); pos = p.buy("BTC", "2", "100", "2")
    assert (pos.quantity, pos.cost_basis, pos.average_entry_price, p.cash, p.fees_paid) == (D("2"), D("202"), D("101"), D("798"), D("2"))

def test_buy_adds_to_existing_position_with_weighted_cost():
    p = Portfolio(D("1000")); p.buy("BTC", "1", "100", "1"); pos = p.buy("BTC", "2", "120", "2")
    assert (pos.quantity, pos.cost_basis, p.cash) == (D("3"), D("343"), D("657"))

def test_snapshot_equity_unrealized_pnl_and_exposure():
    p = Portfolio(D("1000")); p.buy("BTC", "2", "100", "2"); s = p.snapshot({"BTC": "110"})
    assert (s.market_values["BTC"], s.unrealized_pnl, s.total_exposure, s.equity) == (D("220"), D("18"), D("220"), D("1018"))

def test_partial_sell_realizes_pnl_and_keeps_remaining_cost_basis():
    p = Portfolio(D("1000")); p.buy("BTC", "2", "100", "2"); pnl = p.sell("BTC", "1", "120", "1")
    assert (pnl, p.realized_pnl, p.cash, p.positions["BTC"].quantity, p.positions["BTC"].cost_basis, p.fees_paid) == (D("18"), D("18"), D("917"), D("1"), D("101"), D("3"))
    s = p.snapshot({"BTC": "120"}); assert (s.unrealized_pnl, s.equity) == (D("19"), D("1037"))

def test_full_sell_closes_position_and_equity_includes_fees_once():
    p = Portfolio(D("1000")); p.buy("ETH", "2", "100", "2"); p.sell("ETH", "2", "110", "2"); s = p.snapshot({})
    assert p.positions == {}; assert (p.cash, p.realized_pnl, p.fees_paid, s.equity) == (D("1016"), D("16"), D("4"), D("1016"))

@pytest.mark.parametrize(("qty", "price", "fee"), [("0", "10", "0"), ("1", "0", "0"), ("1", "10", "-1")])
def test_buy_rejects_invalid_values(qty, price, fee):
    with pytest.raises(ValueError): Portfolio(D("100")).buy("BTC", qty, price, fee)

def test_buy_rejects_insufficient_cash_without_mutation():
    p = Portfolio(D("100"))
    with pytest.raises(ValueError, match="insufficient cash"): p.buy("BTC", "1", "100", "1")
    assert p.cash == D("100") and p.positions == {}

def test_sell_rejects_missing_or_oversized_position_without_mutation():
    p = Portfolio(D("100"))
    with pytest.raises(ValueError, match="available spot position"): p.sell("BTC", "1", "10")
    p.buy("BTC", "2", "10"); cash = p.cash
    with pytest.raises(ValueError, match="available spot position"): p.sell("BTC", "3", "10")
    assert p.cash == cash and p.positions["BTC"].quantity == D("2")

def test_snapshot_requires_marks_for_open_positions():
    p = Portfolio(D("100")); p.buy("BTC", "1", "10")
    with pytest.raises(ValueError, match="missing mark price"): p.snapshot({})

def test_invalid_position_and_initial_cash_are_rejected():
    with pytest.raises(ValueError): Position("BTC", D("0"), D("10"))
    with pytest.raises(ValueError): Portfolio(D("-1"))
    with pytest.raises(ValueError): Portfolio(D("10"), quote_currency=" USDT")

def test_asset_exposure_is_reported_per_asset():
    p = Portfolio(D("1000")); p.buy("BTC", "1", "100"); p.buy("ETH", "2", "50")
    s = p.snapshot({"BTC": "110", "ETH": "60"})
    assert s.exposure_by_asset == {"BTC": D("110"), "ETH": D("120")}
    assert s.total_exposure == D("230")
