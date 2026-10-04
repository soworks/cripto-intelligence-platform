from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from cip.backtest.schedule import allocate, contribution_schedule
from cip.domain.errors import BacktestError
from cip.domain.policy import load_policy

_POLICY = Path("policies/investment-policy.yaml")


def test_mid_month_opening_buys_on_the_following_first() -> None:
    schedule = contribution_schedule(
        date(2017, 8, 17),
        date(2017, 9, 1),
        Decimal("650"),
        Decimal("800"),
    )

    assert schedule == (
        (date(2017, 8, 17), Decimal("650")),
        (date(2017, 9, 1), Decimal("800")),
    )


def test_opening_on_the_first_does_not_add_the_monthly_buy() -> None:
    schedule = contribution_schedule(
        date(2017, 9, 1),
        date(2017, 10, 1),
        Decimal("650"),
        Decimal("800"),
    )

    assert schedule == (
        (date(2017, 9, 1), Decimal("650")),
        (date(2017, 10, 1), Decimal("800")),
    )


def test_december_rolls_into_january() -> None:
    schedule = contribution_schedule(
        date(2017, 11, 15),
        date(2018, 1, 1),
        Decimal("650"),
        Decimal("800"),
    )

    assert schedule == (
        (date(2017, 11, 15), Decimal("650")),
        (date(2017, 12, 1), Decimal("800")),
        (date(2018, 1, 1), Decimal("800")),
    )


def test_repository_core_mix_splits_the_full_contribution() -> None:
    policy = load_policy(_POLICY).policy
    weights = {symbol: Decimal(str(weight)) for symbol, weight in policy.portfolio.core_mix.items()}

    opening = allocate(Decimal(str(policy.portfolio.starting_value_usd)), weights)
    monthly = allocate(Decimal(str(policy.portfolio.monthly_contribution_usd)), weights)

    assert opening == {"BTCUSDT": Decimal("455.00"), "ETHUSDT": Decimal("195.00")}
    assert monthly == {"BTCUSDT": Decimal("560.00"), "ETHUSDT": Decimal("240.00")}
    assert monthly != {"BTCUSDT": Decimal("392.00"), "ETHUSDT": Decimal("168.00")}


def test_residual_cent_lands_on_eth() -> None:
    legs = allocate(
        Decimal("1.05"),
        {"ETHUSDT": Decimal("0.30"), "BTCUSDT": Decimal("0.70")},
    )

    assert legs == {"BTCUSDT": Decimal("0.74"), "ETHUSDT": Decimal("0.31")}


def test_single_symbol_receives_the_whole_amount() -> None:
    assert allocate(Decimal("650"), {"BTCUSDT": Decimal(1)}) == {"BTCUSDT": Decimal("650")}


def test_weights_that_do_not_sum_to_one_are_rejected() -> None:
    with pytest.raises(BacktestError, match="sum to 1"):
        allocate(Decimal("100"), {"BTCUSDT": Decimal("0.40"), "ETHUSDT": Decimal("0.40")})


def test_a_rounded_leg_must_be_positive() -> None:
    with pytest.raises(BacktestError, match="positive"):
        allocate(Decimal("1"), {"BTCUSDT": Decimal("0.001"), "ETHUSDT": Decimal("0.999")})

    with pytest.raises(BacktestError, match="positive"):
        allocate(Decimal("0.01"), {"BTCUSDT": Decimal("0.70"), "ETHUSDT": Decimal("0.30")})


@given(
    amount=st.decimals(
        min_value=Decimal("1"),
        max_value=Decimal("100000"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
    first=st.decimals(
        min_value=Decimal("0.05"),
        max_value=Decimal("0.95"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
)
def test_legs_sum_to_the_contribution(amount: Decimal, first: Decimal) -> None:
    legs = allocate(amount, {"BTCUSDT": first, "ETHUSDT": Decimal(1) - first})

    assert sum(legs.values()) == amount
    assert all(leg > 0 for leg in legs.values())
