"""Mock-only tests for the Testnet execution safety gate; no network/orders."""
import asyncio
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from crypto_bot.config.settings import Settings, TradingMode
from crypto_bot.execution.adapters.binance_spot_testnet import (
    BinanceSpotTestnetAdapter,
    TestnetConfigurationError,
)

from crypto_bot.control import KillSwitch, KillSwitchState
from crypto_bot.risk import RiskContext, RiskLimits, RiskManager, TradeProposal
from crypto_bot.execution._gate_authority import _ORDER_GATE_AUTHORITY
from crypto_bot.execution.testnet_executor import (
    TestnetExecutionBlocked,
    TestnetOrderExecutor,
)
from crypto_bot.execution.adapters.binance_spot_testnet import SpotOrderRequest
from crypto_bot.storage.order_ledger import OrderLedger, OrderState


def valid_exchange_info():
    return {
        "symbols": [{
            "symbol": "BTCUSDT",
            "status": "TRADING",
            "filters": [
                {"filterType": "LOT_SIZE", "minQty": "0.001", "maxQty": "100", "stepSize": "0.001"},
                {"filterType": "PRICE_FILTER", "minPrice": "0.01", "maxPrice": "1000000", "tickSize": "0.01"},
                {"filterType": "MIN_NOTIONAL", "minNotional": "10"},
            ],
        }]
    }


class FakeAdapter:
    def __init__(self, exchange_info=None, exchange_info_error=None):
        self.calls = []
        self.exchange_info = valid_exchange_info() if exchange_info is None else exchange_info
        self.exchange_info_error = exchange_info_error
        self.exchange_info_calls = []

    async def get_exchange_info(self, symbol=None):
        self.exchange_info_calls.append(symbol)
        if self.exchange_info_error is not None:
            raise self.exchange_info_error
        return self.exchange_info

    async def submit_order(self, order, *, _authorization=None):
        self.calls.append((order, _authorization))
        return {
            "orderId": 12345,
            "clientOrderId": order.client_order_id,
            "symbol": order.symbol.upper(),
            "side": order.side.upper(),
            "type": order.order_type.upper(),
            "origQty": order.quantity,
            "price": order.price,
            "status": "NEW",
        }


def make_proposal(**changes):
    values = dict(
        asset="BTC",
        quantity=D("1"),
        entry_price=D("100"),
        stop_price=D("90"),
        estimated_fees_quote=D("1"),
        estimated_slippage_quote=D("1"),
    )
    values.update(changes)
    return TradeProposal(**values)


def make_context(**changes):
    values = dict(
        current_daily_loss_quote=D("0"),
        open_position_count=0,
        data_is_fresh=True,
        account_state_is_valid=True,
        exchange_filters_are_valid=True,
    )
    values.update(changes)
    return RiskContext(**values)


@pytest.fixture
def setup_gate(tmp_path):
    switch = KillSwitch(tmp_path / "switch.sqlite3")
    switch.transition(KillSwitchState.MONITORING, actor="test", reason="setup", authorized=True)
    switch.transition(KillSwitchState.RUNNING, actor="test", reason="setup", authorized=True)
    limits = RiskLimits(
        max_order_quote=D("500"),
        max_asset_exposure_quote=D("700"),
        max_total_exposure_quote=D("900"),
        max_open_positions=3,
        max_trade_loss_quote=D("30"),
        max_daily_loss_quote=D("100"),
    )
    manager = RiskManager(limits, kill_switch=switch)
    adapter = FakeAdapter()
    portfolio = SimpleNamespace(
        cash=D("1000"),
        equity=D("1000"),
        exposure_by_asset={},
        total_exposure=D("0"),
    )
    gate = TestnetOrderExecutor(
        adapter,
        manager,
        switch,
        portfolio_provider=lambda: portfolio,
        context_provider=lambda: make_context(),
        symbol_to_asset={"BTCUSDT": "BTC"},
        ledger=OrderLedger(tmp_path / "orders.sqlite3"),
        allow_testnet_submission=True,  # Explicitly enabled only for this fake-adapter unit test.
    )
    return gate, adapter, switch


def test_approved_exact_order_uses_gate_capability(setup_gate):
    gate, adapter, _ = setup_gate
    result = asyncio.run(gate.execute_buy(
        SpotOrderRequest("BTCUSDT", "BUY", "LIMIT", "1", "100"),
        make_proposal(),
    ))
    assert result["orderId"] == 12345
    assert adapter.exchange_info_calls == ["BTCUSDT"]
    assert len(adapter.calls) == 1
    assert adapter.calls[0][1] is _ORDER_GATE_AUTHORITY
    submitted = adapter.calls[0][0]
    assert submitted.client_order_id
    record = setup_gate[0]._ledger.get(submitted.client_order_id)
    assert record is not None
    assert record.state is OrderState.ACKNOWLEDGED
    assert record.exchange_order_id == "12345"


def test_submission_is_disabled_by_default(setup_gate, tmp_path):
    enabled_gate, adapter, switch = setup_gate
    disabled_gate = TestnetOrderExecutor(
        adapter,
        enabled_gate._risk_manager,
        switch,
        portfolio_provider=enabled_gate._portfolio_provider,
        context_provider=enabled_gate._context_provider,
        symbol_to_asset={"BTCUSDT": "BTC"},
        ledger=OrderLedger(tmp_path / "disabled-orders.sqlite3"),
    )

    with pytest.raises(TestnetExecutionBlocked, match="disabled by default"):
        asyncio.run(disabled_gate.execute_buy(
            SpotOrderRequest("BTCUSDT", "BUY", "LIMIT", "1", "100"),
            make_proposal(),
        ))

    assert adapter.calls == []
    assert adapter.exchange_info_calls == []


def test_duplicate_client_order_id_is_blocked_before_second_submission(setup_gate):
    gate, adapter, _ = setup_gate
    order = SpotOrderRequest(
        "BTCUSDT", "BUY", "LIMIT", "1", "100",
        client_order_id="repeat-test-order",
    )

    asyncio.run(gate.execute_buy(order, make_proposal()))
    assert len(adapter.calls) == 1

    with pytest.raises(TestnetExecutionBlocked, match="intent could not be safely recorded"):
        asyncio.run(gate.execute_buy(order, make_proposal()))

    assert len(adapter.calls) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("symbol", "ETHUSDT"),
        ("side", "SELL"),
        ("type", "MARKET"),
        ("origQty", "2"),
        ("price", "101"),
        ("status", "REJECTED"),
        ("status", "CANCELED"),
    ],
)
def test_inconsistent_acknowledgement_is_left_unresolved(setup_gate, field, value):
    gate, adapter, _ = setup_gate

    async def inconsistent_submit(order, *, _authorization=None):
        adapter.calls.append((order, _authorization))
        response = {
            "orderId": 54321,
            "clientOrderId": order.client_order_id,
            "symbol": order.symbol.upper(),
            "side": order.side.upper(),
            "type": order.order_type.upper(),
            "origQty": order.quantity,
            "price": order.price,
            "status": "NEW",
        }
        response[field] = value
        return response

    adapter.submit_order = inconsistent_submit
    with pytest.raises(TestnetExecutionBlocked, match="requires reconciliation"):
        asyncio.run(gate.execute_buy(
            SpotOrderRequest("BTCUSDT", "BUY", "LIMIT", "1", "100"),
            make_proposal(),
        ))

    assert len(adapter.calls) == 1
    client_id = adapter.calls[0][0].client_order_id
    record = gate._ledger.get(client_id)
    assert record is not None
    assert record.state is OrderState.SUBMISSION_UNKNOWN


def test_malformed_acknowledgement_is_left_unresolved(setup_gate):
    gate, adapter, _ = setup_gate

    async def malformed_submit(order, *, _authorization=None):
        adapter.calls.append((order, _authorization))
        return {"orderId": 999}

    adapter.submit_order = malformed_submit
    order = SpotOrderRequest("BTCUSDT", "BUY", "LIMIT", "1", "100")

    with pytest.raises(TestnetExecutionBlocked, match="requires reconciliation"):
        asyncio.run(gate.execute_buy(order, make_proposal()))

    client_id = adapter.calls[0][0].client_order_id
    record = gate._ledger.get(client_id)
    assert record is not None
    assert record.state is OrderState.SUBMISSION_UNKNOWN


@pytest.mark.parametrize(
    "order,proposal",
    [
        (SpotOrderRequest("BTCUSDT", "BUY", "LIMIT", "2", "100"), make_proposal()),
        (SpotOrderRequest("BTCUSDT", "BUY", "LIMIT", "1", "101"), make_proposal()),
        (SpotOrderRequest("ETHUSDT", "BUY", "LIMIT", "1", "100"), make_proposal()),
        (SpotOrderRequest("BTCUSDT", "SELL", "LIMIT", "1", "100"), make_proposal()),
        (SpotOrderRequest("BTCUSDT", "BUY", "MARKET", "1"), make_proposal()),
    ],
)
def test_mismatched_or_unsupported_order_never_reaches_adapter(setup_gate, order, proposal):
    gate, adapter, _ = setup_gate
    with pytest.raises(TestnetExecutionBlocked):
        asyncio.run(gate.execute_buy(order, proposal))
    assert adapter.calls == []


def test_invalid_exchange_filters_never_reach_adapter(setup_gate):
    gate, adapter, _ = setup_gate
    info = valid_exchange_info()
    info["symbols"][0]["filters"][0]["minQty"] = "2"
    adapter.exchange_info = info

    with pytest.raises(TestnetExecutionBlocked, match="exchange filters rejected"):
        asyncio.run(gate.execute_buy(
            SpotOrderRequest("BTCUSDT", "BUY", "LIMIT", "1", "100"),
            make_proposal(),
        ))
    assert adapter.exchange_info_calls == ["BTCUSDT"]
    assert adapter.calls == []


def test_exchange_metadata_failure_never_reaches_adapter(setup_gate):
    gate, adapter, _ = setup_gate
    adapter.exchange_info_error = RuntimeError("mock metadata failure")

    with pytest.raises(TestnetExecutionBlocked, match="exchange metadata unavailable"):
        asyncio.run(gate.execute_buy(
            SpotOrderRequest("BTCUSDT", "BUY", "LIMIT", "1", "100"),
            make_proposal(),
        ))
    assert adapter.exchange_info_calls == ["BTCUSDT"]
    assert adapter.calls == []


def test_risk_rejection_never_reaches_adapter(setup_gate):
    gate, adapter, _ = setup_gate
    with pytest.raises(TestnetExecutionBlocked, match="risk manager rejected"):
        asyncio.run(gate.execute_buy(
            SpotOrderRequest("BTCUSDT", "BUY", "LIMIT", "1", "100"),
            make_proposal(stop_price=D("50")),
        ))
    assert adapter.calls == []


def test_kill_switch_checked_before_submission(setup_gate):
    gate, adapter, switch = setup_gate
    switch.transition(KillSwitchState.ENTRIES_PAUSED, actor="test", reason="pause", authorized=True)
    with pytest.raises(TestnetExecutionBlocked):
        asyncio.run(gate.execute_buy(
            SpotOrderRequest("BTCUSDT", "BUY", "LIMIT", "1", "100"),
            make_proposal(),
        ))
    assert adapter.calls == []


def test_missing_fresh_context_fails_closed(setup_gate):
    gate, adapter, _ = setup_gate
    gate._context_provider = lambda: (_ for _ in ()).throw(RuntimeError("unavailable"))
    with pytest.raises(TestnetExecutionBlocked, match="risk inputs unavailable"):
        asyncio.run(gate.execute_buy(
            SpotOrderRequest("BTCUSDT", "BUY", "LIMIT", "1", "100"),
            make_proposal(),
        ))
    assert adapter.calls == []


def test_adapter_gate_token_is_not_optional_for_order_submission():
    # Adapter integration is asserted by the source-level method signature in
    # test_binance_spot_testnet_adapter.py; no real adapter/network is instantiated here.
    import inspect
    from crypto_bot.execution.adapters.binance_spot_testnet import BinanceSpotTestnetAdapter
    params = inspect.signature(BinanceSpotTestnetAdapter.submit_order).parameters
    assert "_authorization" in params
    assert params["_authorization"].default is None


class NoNetworkSession:
    def __init__(self):
        self.calls = []

    async def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("network must not be reached")


def test_direct_adapter_submission_is_blocked_before_http():
    settings = Settings(
        _env_file=None,
        app_env="test",
        default_trading_mode=TradingMode.BINANCE_SPOT_TESTNET,
        live_trading_enabled=False,
        binance_spot_testnet_api_key=SecretStr("mock-key"),
        binance_spot_testnet_api_secret=SecretStr("mock-secret"),
    )
    session = NoNetworkSession()
    adapter = BinanceSpotTestnetAdapter(settings, session=session, clock_ms=lambda: 123)
    with pytest.raises(TestnetConfigurationError, match="direct order submission is blocked"):
        asyncio.run(adapter.submit_order(SpotOrderRequest("BTCUSDT", "BUY", "LIMIT", "1", "100")))
    assert session.calls == []

@pytest.mark.parametrize(
    "bad_order_id",
    [None, "", "abc", "12x", "0", "-1", 0, -1, True, 12.5, [], {}],
    ids=[
        "none", "blank", "letters", "mixed", "zero-string",
        "negative-string", "zero-int", "negative-int", "boolean",
        "float", "list", "dict",
    ],
)
def test_invalid_order_id_acknowledgement_is_left_unresolved(
    setup_gate, bad_order_id
):
    gate, adapter, _ = setup_gate

    async def invalid_id_submit(order, *, _authorization=None):
        adapter.calls.append((order, _authorization))
        return {
            "orderId": bad_order_id,
            "clientOrderId": order.client_order_id,
            "symbol": order.symbol.upper(),
            "side": order.side.upper(),
            "type": order.order_type.upper(),
            "origQty": order.quantity,
            "price": order.price,
            "status": "NEW",
        }

    adapter.submit_order = invalid_id_submit
    order = SpotOrderRequest("BTCUSDT", "BUY", "LIMIT", "1", "100")

    with pytest.raises(TestnetExecutionBlocked, match="requires reconciliation"):
        asyncio.run(gate.execute_buy(order, make_proposal()))

    assert len(adapter.calls) == 1
    client_id = adapter.calls[0][0].client_order_id
    record = gate._ledger.get(client_id)
    assert record is not None
    assert record.state is OrderState.SUBMISSION_UNKNOWN
