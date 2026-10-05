import warnings
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from cip.domain.errors import FillError
from cip.domain.policy import load_policy
from cip.portfolio.fills import ShadowFill, shadow_fill

_POLICY = load_policy(Path(__file__).parents[3] / "policies" / "investment-policy.yaml")


def _model(**overrides: object) -> ShadowFill:
    values: dict[str, object] = {
        "side": "buy",
        "reason": "filled",
        "fill_price": Decimal("100.075"),
        "quantity": Decimal("1"),
        "fee_drag_usd": Decimal("0.075"),
        "budget_usd": Decimal("100.075"),
        "requested": Decimal("1"),
    }
    values.update(overrides)
    return ShadowFill(**values)  # type: ignore[arg-type]


def _document(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "side": "buy",
        "reason": "unfilled",
        "fill_price": None,
        "quantity": None,
        "fee_drag_usd": None,
        "budget_usd": Decimal("0"),
        "requested": Decimal("1"),
    }
    payload.update(overrides)
    return payload


def _fill(**overrides: object) -> ShadowFill:
    values: dict[str, object] = {
        "side": "buy",
        "next_open": Decimal("100"),
        "quantity": Decimal("1"),
        "filled_quantity": None,
        "spread": Decimal("0"),
        "slippage": Decimal("0"),
        "buy_spent_today_usd": Decimal("0"),
        "buy_spent_month_usd": Decimal("0"),
        "policy": _POLICY,
    }
    values.update(overrides)
    return shadow_fill(**values)  # type: ignore[arg-type]


def test_a_buy_pays_the_next_open_plus_the_taker_fee() -> None:
    fill = _fill()
    assert fill.reason == "filled"
    assert fill.fill_price == Decimal("100.075")
    assert fill.quantity == Decimal("1")
    assert fill.fee_drag_usd == Decimal("0.075")
    assert fill.budget_usd == Decimal("100.075")
    document = fill.to_document()
    assert set(document) == {
        "side",
        "reason",
        "fill_price",
        "quantity",
        "fee_drag_usd",
        "budget_usd",
    }
    assert document["fill_price"] == "100.075"
    with pytest.raises(ValidationError):
        ShadowFill.model_validate({**fill.model_dump(), "order_id": "1"})


def test_half_the_spread_and_slippage_move_both_sides() -> None:
    buy = _fill(spread=Decimal("0.002"), slippage=Decimal("0.001"))
    sell = _fill(side="sell", spread=Decimal("0.002"), slippage=Decimal("0.001"))
    assert buy.fill_price == Decimal("100.275")
    assert sell.fill_price == Decimal("99.725")
    assert sell.budget_usd == Decimal("0")
    assert sell.fee_drag_usd == Decimal("0.275")


def test_a_sell_is_not_blocked_by_a_buy_budget() -> None:
    blocked = _fill(buy_spent_today_usd=Decimal("150"), buy_spent_month_usd=Decimal("750"))
    assert blocked.reason == "buy_budget"
    assert blocked.fill_price is None
    assert blocked.quantity is None
    assert blocked.budget_usd == Decimal("0")
    assert blocked.to_document()["fill_price"] is None
    sold = _fill(
        side="sell",
        buy_spent_today_usd=Decimal("150"),
        buy_spent_month_usd=Decimal("750"),
    )
    assert sold.reason == "filled"
    assert sold.fill_price == Decimal("99.925")
    assert sold.budget_usd == Decimal("0")


def test_a_buy_fits_the_remaining_budget_and_stops_above_it() -> None:
    room = _fill(buy_spent_today_usd=Decimal("49.925"))
    assert room.reason == "filled"
    over_day = _fill(buy_spent_today_usd=Decimal("49.93"))
    assert over_day.reason == "buy_budget"
    over_month = _fill(buy_spent_month_usd=Decimal("649.93"))
    assert over_month.reason == "buy_budget"


def test_a_smaller_fill_is_partial_and_a_missing_bar_is_not_a_fill() -> None:
    partial = _fill(quantity=Decimal("2"), filled_quantity=Decimal("1"))
    assert partial.reason == "partial"
    assert partial.quantity == Decimal("1")
    assert partial.fill_price == Decimal("100.075")
    assert _fill(filled_quantity=Decimal("0")).reason == "unfilled"
    missing = _fill(next_open=None)
    assert missing.reason == "no_next_bar"
    assert missing.fill_price is None
    assert missing.quantity is None


def test_bad_inputs_are_refused() -> None:
    with pytest.raises(FillError, match="decimal"):
        _fill(next_open=100)  # type: ignore[arg-type]
    with pytest.raises(FillError, match="decimal"):
        _fill(next_open=Decimal("NaN"))
    with pytest.raises(FillError, match="spent"):
        _fill(buy_spent_today_usd=Decimal("-1"))
    with pytest.raises(FillError, match="positive"):
        _fill(quantity=Decimal("0"))
    with pytest.raises(FillError, match="spread"):
        _fill(spread=Decimal("-0.01"))
    with pytest.raises(FillError, match="drag"):
        _fill(spread=Decimal("2"))
    with pytest.raises(FillError, match="filled"):
        _fill(filled_quantity=Decimal("2"))
    with pytest.raises(FillError, match="side"):
        _fill(side="BUY")
    with pytest.raises(FillError, match="filled"):
        _fill(filled_quantity=Decimal("-1"))
    with pytest.raises(ValidationError):
        _model(fill_price=None, quantity=None, fee_drag_usd=None)
    with pytest.raises(ValidationError):
        _model(
            reason="no_next_bar",
            fill_price=None,
            quantity=None,
            fee_drag_usd=None,
            budget_usd=Decimal("1"),
        )
    with pytest.raises(ValidationError):
        _model(fill_price=Decimal("0"))
    with pytest.raises(ValidationError):
        _model(quantity=Decimal("0"))
    with pytest.raises(ValidationError):
        _model(fee_drag_usd=Decimal("0"))
    with pytest.raises(ValidationError):
        _model(quantity=Decimal("2"), budget_usd=Decimal("200.15"))
    with pytest.raises(ValidationError):
        _model(reason="partial")
    with pytest.raises(ValidationError):
        _model(budget_usd=Decimal("1"))
    with pytest.raises(ValidationError):
        _model(side="sell", budget_usd=Decimal("1"), fill_price=Decimal("99.925"))
    with pytest.raises(ValidationError):
        _model(fee_drag_usd=Decimal("200"))
    with pytest.raises(ValidationError):
        _model(
            reason="no_next_bar",
            fill_price=None,
            quantity=None,
            fee_drag_usd=None,
            budget_usd=Decimal("0"),
            requested=Decimal("0"),
        )
    with pytest.raises(ValidationError):
        _model(quantity=None)
    with pytest.raises(ValidationError):
        _model(
            reason="no_next_bar",
            fill_price=None,
            fee_drag_usd=None,
            budget_usd=Decimal("0"),
        )
    with pytest.raises(ValidationError):
        ShadowFill.model_validate(_document(side=1))
    with pytest.raises(ValidationError):
        ShadowFill.model_validate(_document(budget_usd=1))
    with pytest.raises(ValidationError):
        ShadowFill.model_validate(_document(budget_usd=Decimal("NaN")))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        forged = _fill().model_copy(update={"reason": "buy_budget"})
        with pytest.raises(FillError, match="invalid"):
            forged.to_document()
        leaked = _fill().model_copy(
            update={
                "reason": "buy_budget",
                "quantity": None,
                "fee_drag_usd": None,
                "budget_usd": Decimal("0"),
            }
        )
        with pytest.raises(FillError, match="invalid"):
            leaked.to_document()
