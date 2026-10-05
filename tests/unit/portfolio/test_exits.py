from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from cip.domain.errors import ExitError
from cip.domain.policy import load_policy
from cip.portfolio.exits import NamedExit, name_exit
from cip.portfolio.position import PositionState

_POLICY = Path(__file__).parents[3] / "policies" / "investment-policy.yaml"
EXITS = load_policy(_POLICY).policy.hypotheses.exits


def _name(**overrides: object) -> NamedExit | None:
    values: dict[str, object] = {
        "state": PositionState.OPEN,
        "entry_price": Decimal("100"),
        "mark_price": Decimal("100"),
        "highest_price": Decimal("100"),
        "atr": Decimal("2"),
        "days_held": 1,
        "return_vs_btc": Decimal("0.01"),
        "observed_triggers": frozenset(),
        "partial_already_taken": False,
        "exits": EXITS,
    }
    values.update(overrides)
    return name_exit(**values)  # type: ignore[arg-type]


def test_a_position_inside_the_rules_has_no_exit() -> None:
    assert EXITS.max_holding_days == 56
    assert _name() is None


def test_the_initial_stop_uses_the_tighter_of_atr_and_the_percent_cap() -> None:
    assert _name(mark_price=Decimal("95")).rule == "initial_stop"  # type: ignore[union-attr]
    assert _name(mark_price=Decimal("95.01")) is None
    capped = _name(atr=Decimal("10"), mark_price=Decimal("85"))
    assert capped is not None
    assert capped.rule == "initial_stop"
    assert _name(atr=Decimal("10"), mark_price=Decimal("85.01")) is None
    assert set(capped.to_document()) == {"rule", "reason", "triggers"}


def test_a_two_r_gain_names_a_partial_and_does_not_name_it_twice() -> None:
    named = _name(mark_price=Decimal("110"), highest_price=Decimal("110"))
    assert named is not None
    assert named.rule == "partial_take_profit"
    assert set(named.to_document()) == {"rule", "reason", "triggers"}
    assert (
        _name(
            mark_price=Decimal("110"),
            highest_price=Decimal("110"),
            partial_already_taken=True,
        )
        is None
    )
    assert (
        _name(
            state=PositionState.PARTIAL_EXIT,
            mark_price=Decimal("110"),
            highest_price=Decimal("110"),
        )
        is None
    )
    assert _name(mark_price=Decimal("109.99"), highest_price=Decimal("109.99")) is None


def test_the_chandelier_names_an_exit_only_after_it_activates() -> None:
    quiet = _name(highest_price=Decimal("104"), mark_price=Decimal("98"))
    assert quiet is None
    activated = _name(highest_price=Decimal("105"), mark_price=Decimal("99"))
    assert activated is not None
    assert activated.rule == "chandelier"
    trailed = _name(highest_price=Decimal("110"), mark_price=Decimal("104"))
    assert trailed is not None
    assert trailed.rule == "chandelier"
    assert _name(highest_price=Decimal("110"), mark_price=Decimal("104.01")) is None
    both = _name(highest_price=Decimal("110"), mark_price=Decimal("90"))
    assert both is not None
    assert both.rule == "chandelier"
    wider = EXITS.model_copy(update={"trailing_atr_mult": 10})
    initial = _name(exits=wider, highest_price=Decimal("110"), mark_price=Decimal("90"))
    assert initial is not None
    assert initial.rule == "initial_stop"


def test_the_time_stop_needs_both_the_day_and_a_weak_btc_relative_return() -> None:
    due = _name(days_held=21, return_vs_btc=Decimal("0"))
    assert due is not None
    assert due.rule == "time_stop"
    assert _name(days_held=21, return_vs_btc=Decimal("0.01")) is None
    assert _name(days_held=20, return_vs_btc=Decimal("-0.10")) is None


def test_day_56_names_the_max_hold_even_when_the_trade_is_ahead() -> None:
    named = _name(days_held=56, return_vs_btc=Decimal("0.20"))
    assert named is not None
    assert named.rule == "max_holding"
    assert _name(days_held=55, return_vs_btc=Decimal("0.20")) is None


def test_a_forced_trigger_is_named_in_policy_order() -> None:
    named = _name(
        observed_triggers=frozenset({"rs_rank_percentile_below_0.40", "delisting_announced"}),
        mark_price=Decimal("50"),
    )
    assert named is not None
    assert named.rule == "forced"
    assert named.reason == "delisting_announced"
    assert named.triggers == ("delisting_announced", "rs_rank_percentile_below_0.40")
    assert set(named.to_document()) == {"rule", "reason", "triggers"}
    with pytest.raises(ExitError, match="unknown"):
        _name(observed_triggers=frozenset({"not_a_trigger"}))


def test_a_stop_outranks_the_calendar_and_the_calendar_outranks_a_partial() -> None:
    stopped = _name(days_held=56, mark_price=Decimal("95"), return_vs_btc=Decimal("0.2"))
    assert stopped is not None
    assert stopped.rule == "initial_stop"
    forced = _name(
        days_held=56,
        mark_price=Decimal("95"),
        observed_triggers=frozenset({"regime_risk_off"}),
    )
    assert forced is not None
    assert forced.rule == "forced"
    timed = _name(
        days_held=21,
        return_vs_btc=Decimal("-0.01"),
        mark_price=Decimal("110"),
        highest_price=Decimal("110"),
    )
    assert timed is not None
    assert timed.rule == "time_stop"
    held = _name(
        days_held=56,
        return_vs_btc=Decimal("-0.01"),
        mark_price=Decimal("110"),
        highest_price=Decimal("110"),
    )
    assert held is not None
    assert held.rule == "max_holding"


def test_bad_inputs_and_a_position_that_is_not_open_are_refused() -> None:
    with pytest.raises(ExitError, match="positive"):
        _name(atr=Decimal("0"))
    with pytest.raises(ExitError, match="open"):
        _name(state=PositionState.CLOSED)
    with pytest.raises(ExitError, match="open"):
        _name(state=PositionState.PROPOSED)
    with pytest.raises(ExitError, match="open"):
        _name(state="OPEN")  # type: ignore[arg-type]
    with pytest.raises(ExitError, match="days"):
        _name(days_held=-1)
    with pytest.raises(ExitError, match="watermark"):
        _name(highest_price=Decimal("99"), mark_price=Decimal("100"))
    grind = _name(mark_price=Decimal("90"), highest_price=Decimal("95"))
    assert grind is not None
    assert grind.rule == "initial_stop"
    with pytest.raises(ExitError, match="watermark"):
        _name(mark_price=Decimal("101"), highest_price=Decimal("100"))
    with pytest.raises(ExitError, match="days"):
        _name(days_held=True)  # type: ignore[arg-type]
    with pytest.raises(ExitError, match="boolean"):
        _name(partial_already_taken="false")  # type: ignore[arg-type]
    with pytest.raises(ExitError, match="frozenset"):
        _name(observed_triggers={"delisting_announced"})  # type: ignore[arg-type]
    with pytest.raises(ExitError, match="decimal"):
        _name(atr="nope")  # type: ignore[arg-type]
    with pytest.raises(ExitError, match="decimal"):
        _name(atr=object())  # type: ignore[arg-type]
    with pytest.raises(ExitError, match="finite"):
        _name(atr=Decimal("NaN"))
    assert _name(atr="2") is None  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        NamedExit.model_validate(
            {"rule": "initial_stop", "reason": "initial_stop", "order_id": "1"}
        )
    with pytest.raises(ExitError):
        _name(entry_price=Decimal("100.0"), mark_price=1)  # type: ignore[arg-type]
