from decimal import Decimal

import pytest

from crypto_bot.control import KillSwitch, KillSwitchState
from crypto_bot.portfolio.accounting import Portfolio
from crypto_bot.risk import RiskContext, RiskLimits, RiskManager, TradeProposal

D = Decimal


def make_limits():
    return RiskLimits(
        max_order_quote=D("500"),
        max_asset_exposure_quote=D("700"),
        max_total_exposure_quote=D("900"),
        max_open_positions=3,
        max_trade_loss_quote=D("30"),
        max_daily_loss_quote=D("100"),
    )


def make_portfolio(cash="1000", marks=None):
    return Portfolio(quote_currency="USDT", initial_cash=D(cash))


def make_proposal(**overrides):
    values = dict(
        asset="BTC",
        quantity=D("1"),
        entry_price=D("100"),
        stop_price=D("90"),
        estimated_fees_quote=D("1"),
        estimated_slippage_quote=D("1"),
    )
    values.update(overrides)
    return TradeProposal(**values)


def make_context(**overrides):
    values = dict(
        current_daily_loss_quote=D("0"),
        open_position_count=0,
        data_is_fresh=True,
        account_state_is_valid=True,
        exchange_filters_are_valid=True,
    )
    values.update(overrides)
    return RiskContext(**values)


@pytest.fixture
def kill_switch(tmp_path):
    switch = KillSwitch(tmp_path / "kill_switch.sqlite3")
    switch.transition(
        KillSwitchState.MONITORING,
        actor="unit-test",
        reason="test setup",
        authorized=True,
    )
    switch.transition(
        KillSwitchState.RUNNING,
        actor="unit-test",
        reason="test setup",
        authorized=True,
    )
    return switch


@pytest.fixture
def risk_manager(kill_switch):
    return RiskManager(make_limits(), kill_switch=kill_switch)


def test_approves_valid_trade(risk_manager):
    portfolio = make_portfolio()
    decision = risk_manager.evaluate(make_proposal(), portfolio.snapshot({}), make_context())
    assert decision.approved
    assert decision.permitted_quantity == D("1")
    assert decision.order_quote == D("100")


@pytest.mark.parametrize(
    "context, expected",
    [
        (make_context(data_is_fresh=False), "market data is stale"),
        (make_context(account_state_is_valid=False), "account state is invalid"),
        (make_context(exchange_filters_are_valid=False), "exchange filters are invalid"),
    ],
)
def test_rejects_unsafe_state(risk_manager, context, expected):
    decision = risk_manager.evaluate(make_proposal(), make_portfolio().snapshot({}), context)
    assert not decision.approved
    assert expected in decision.reasons


def test_rejects_order_above_limit(risk_manager):
    decision = risk_manager.evaluate(
        make_proposal(quantity=D("6")), make_portfolio().snapshot({}), make_context()
    )
    assert not decision.approved
    assert "order exceeds maximum order size" in decision.reasons


def test_rejects_insufficient_cash(risk_manager):
    decision = risk_manager.evaluate(
        make_proposal(quantity=D("10")), make_portfolio(cash="1000").snapshot({}), make_context()
    )
    assert not decision.approved
    assert "insufficient available cash" in decision.reasons


def test_rejects_trade_loss_above_limit(risk_manager):
    decision = risk_manager.evaluate(
        make_proposal(quantity=D("4")), make_portfolio().snapshot({}), make_context()
    )
    assert not decision.approved
    assert "estimated trade loss exceeds configured limit" in decision.reasons


def test_rejects_daily_loss_budget_breach(risk_manager):
    decision = risk_manager.evaluate(
        make_proposal(), make_portfolio().snapshot({}),
        make_context(current_daily_loss_quote=D("95")),
    )
    assert not decision.approved
    assert "trade could breach the remaining daily loss budget" in decision.reasons


def test_rejects_at_daily_loss_limit(risk_manager):
    decision = risk_manager.evaluate(
        make_proposal(), make_portfolio().snapshot({}),
        make_context(current_daily_loss_quote=D("100")),
    )
    assert not decision.approved
    assert "daily loss limit has been reached" in decision.reasons


def test_rejects_max_open_positions(risk_manager):
    decision = risk_manager.evaluate(
        make_proposal(), make_portfolio().snapshot({}),
        make_context(open_position_count=3),
    )
    assert not decision.approved
    assert "maximum open positions would be exceeded" in decision.reasons


def test_rejects_invalid_proposal(risk_manager):
    with pytest.raises(ValueError):
        make_proposal(stop_price=D("100"))


def test_limits_require_explicit_positive_values():
    with pytest.raises(ValueError):
        RiskLimits(D("0"), D("10"), D("20"), 1, D("5"), D("10"))


def test_fails_closed_for_missing_portfolio_fields(risk_manager):
    decision = risk_manager.evaluate(make_proposal(), object(), make_context())
    assert not decision.approved
    assert decision.reasons[0].startswith("invalid or missing risk input:")


def test_new_kill_switch_defaults_to_stopped(tmp_path):
    switch = KillSwitch(tmp_path / "switch.sqlite3")
    assert switch.state is KillSwitchState.STOPPED
    assert not switch.can_open_entries


@pytest.mark.parametrize("state", list(KillSwitchState))
def test_only_running_state_allows_entries(tmp_path, state):
    switch = KillSwitch(tmp_path / "switch.sqlite3")
    if state is not KillSwitchState.STOPPED:
        switch.transition(state, actor="test", reason="test state", authorized=True)
    assert switch.can_open_entries is (state is KillSwitchState.RUNNING)


@pytest.mark.parametrize(
    "state",
    [
        KillSwitchState.STOPPED,
        KillSwitchState.MONITORING,
        KillSwitchState.ENTRIES_PAUSED,
        KillSwitchState.EMERGENCY_HALTED,
    ],
)
def test_risk_manager_rejects_entries_when_switch_blocks(tmp_path, state):
    switch = KillSwitch(tmp_path / "switch.sqlite3")
    if state is not KillSwitchState.STOPPED:
        switch.transition(state, actor="test", reason="unsafe state", authorized=True)
    manager = RiskManager(make_limits(), kill_switch=switch)
    decision = manager.evaluate(make_proposal(), make_portfolio().snapshot({}), make_context())
    assert not decision.approved
    assert any("kill switch blocks new entries" in reason for reason in decision.reasons)


def test_emergency_halt_persists_across_restart_and_blocks_risk_manager(tmp_path):
    database = tmp_path / "switch.sqlite3"
    switch = KillSwitch(database)
    switch.transition(
        KillSwitchState.EMERGENCY_HALTED,
        actor="operator",
        reason="emergency test",
        authorized=True,
    )
    restarted = KillSwitch(database)
    assert restarted.state is KillSwitchState.EMERGENCY_HALTED
    manager = RiskManager(make_limits(), kill_switch=restarted)
    decision = manager.evaluate(make_proposal(), make_portfolio().snapshot({}), make_context())
    assert not decision.approved
    assert any("kill switch blocks new entries" in reason for reason in decision.reasons)


def test_leaving_emergency_halt_requires_confirmation(tmp_path):
    switch = KillSwitch(tmp_path / "switch.sqlite3")
    switch.transition(
        KillSwitchState.EMERGENCY_HALTED,
        actor="operator",
        reason="halt",
        authorized=True,
    )
    with pytest.raises(PermissionError):
        switch.transition(
            KillSwitchState.RUNNING,
            actor="operator",
            reason="resume",
            authorized=True,
        )
    switch.transition(
        KillSwitchState.RUNNING,
        actor="operator",
        reason="confirmed resume",
        authorized=True,
        confirmed=True,
    )
    assert switch.can_open_entries


def test_unauthorized_transition_is_rejected(tmp_path):
    switch = KillSwitch(tmp_path / "switch.sqlite3")
    with pytest.raises(PermissionError):
        switch.transition(
            KillSwitchState.RUNNING,
            actor="unknown",
            reason="unauthorized",
        )
    assert switch.state is KillSwitchState.STOPPED


def test_audit_history_persists(tmp_path):
    database = tmp_path / "switch.sqlite3"
    switch = KillSwitch(database)
    switch.transition(
        KillSwitchState.MONITORING,
        actor="operator",
        reason="start monitoring",
        authorized=True,
    )
    switch.transition(
        KillSwitchState.ENTRIES_PAUSED,
        actor="operator",
        reason="pause entries",
        authorized=True,
    )
    history = KillSwitch(database).history()
    assert len(history) == 2
    assert history[-1].new_state is KillSwitchState.ENTRIES_PAUSED
    assert history[-1].actor == "operator"
    assert history[-1].reason == "pause entries"


def test_invalid_persisted_state_fails_closed(tmp_path):
    database = tmp_path / "switch.sqlite3"
    switch = KillSwitch(database)
    with switch._connect() as connection:
        connection.execute(
            "UPDATE kill_switch_state SET state = 'NOT_A_STATE' WHERE singleton_id = 1"
        )
    restarted = KillSwitch(database)
    assert restarted.state is KillSwitchState.EMERGENCY_HALTED
    assert not restarted.can_open_entries

@pytest.mark.parametrize(
    "flag_name",
    ["data_is_fresh", "account_state_is_valid", "exchange_filters_are_valid"],
)
@pytest.mark.parametrize("invalid_value", ["yes", 1, None, [], {}])
def test_rejects_malformed_safety_flags(
    risk_manager, flag_name, invalid_value
):
    context = make_context(**{flag_name: invalid_value})
    decision = risk_manager.evaluate(
        make_proposal(),
        make_portfolio().snapshot({}),
        context,
    )
    assert not decision.approved
    assert any(
        f"{flag_name} must be a bool" in reason
        for reason in decision.reasons
    )
