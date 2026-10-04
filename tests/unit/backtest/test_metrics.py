from datetime import date
from decimal import Decimal

import pytest

from cip.backtest.metrics import (
    beta_alpha,
    calmar,
    cash_flows,
    daily_returns,
    max_drawdown,
    sharpe,
    solve_xirr,
    sortino,
    time_weighted_return,
)
from cip.domain.errors import BacktestError


def test_a_contribution_is_absent_from_that_days_return() -> None:
    day0 = date(2020, 1, 1)
    day1 = date(2020, 1, 2)
    day2 = date(2020, 1, 3)
    equity = ((day0, Decimal("100")), (day1, Decimal("250")), (day2, Decimal("250")))

    returns = daily_returns(equity, {day1: Decimal("100")})

    assert returns == (Decimal("0.5"), Decimal("0"))


def test_time_weighted_return_compounds_daily_returns() -> None:
    assert time_weighted_return((Decimal("0.1"), Decimal("0.1"))) == Decimal("0.21")


def test_xirr_of_a_one_year_gain_is_ten_percent() -> None:
    start = date(2019, 1, 1)
    end = date(2020, 1, 1)

    rate = solve_xirr(((start, Decimal("-100")), (end, Decimal("110"))))

    assert (end - start).days == 365
    assert rate == Decimal("0.1")


def test_a_later_guess_is_accepted_when_the_first_rate_is_not_the_root() -> None:
    start = date(2019, 1, 1)
    end = date(2020, 1, 1)

    rate = solve_xirr(((start, Decimal("-100")), (end, Decimal("121"))))

    assert abs(rate - Decimal("0.21")) < Decimal("1e-10")


def test_the_last_buy_is_netted_against_final_equity() -> None:
    day0 = date(2020, 1, 1)
    day1 = date(2020, 1, 2)

    flows = cash_flows(
        ((day0, Decimal("100")), (day1, Decimal("40"))),
        day1,
        Decimal("150"),
    )

    assert flows == ((day0, Decimal("-100")), (day1, Decimal("110")))


def test_final_equity_is_its_own_flow_when_the_last_day_is_not_a_buy() -> None:
    day0 = date(2020, 1, 1)
    day1 = date(2020, 1, 2)

    flows = cash_flows(((day0, Decimal("100")),), day1, Decimal("150"))

    assert flows == ((day0, Decimal("-100")), (day1, Decimal("150")))


def test_xirr_rejects_a_zero_iteration_cap() -> None:
    start = date(2020, 1, 1)
    with pytest.raises(BacktestError, match="did not converge"):
        solve_xirr(
            ((start, Decimal("-100")), (date(2021, 1, 1), Decimal("110"))),
            max_iterations=0,
        )


def test_xirr_rejects_a_rate_below_minus_one() -> None:
    start = date(2020, 1, 1)
    with pytest.raises(BacktestError, match="below -1"):
        solve_xirr(((start, Decimal("-100")), (date(2020, 1, 2), Decimal("1"))))


def test_xirr_rejects_a_zero_derivative() -> None:
    with pytest.raises(BacktestError, match="derivative is zero"):
        solve_xirr(((date(2020, 1, 1), Decimal("-100")),))


def test_constant_returns_have_no_sharpe() -> None:
    assert sharpe((Decimal("0.01"), Decimal("0.01"))) is None


def test_symmetric_returns_have_zero_sharpe() -> None:
    assert sharpe((Decimal("0.01"), Decimal("-0.01"))) == Decimal(0)


def test_returns_that_never_fall_have_no_sortino() -> None:
    assert sortino((Decimal("0.01"), Decimal("0.02"))) is None


def test_symmetric_returns_have_zero_sortino() -> None:
    assert sortino((Decimal("0.01"), Decimal("-0.01"))) == Decimal(0)


def test_a_rise_then_a_fall_draws_down_the_index() -> None:
    assert max_drawdown((Decimal("0.1"), Decimal("-0.1"))) == Decimal("0.1")


def test_a_series_that_never_falls_has_no_drawdown() -> None:
    assert max_drawdown((Decimal("0.1"), Decimal("0.1"))) == Decimal(0)


def test_calmar_is_annualized_return_over_drawdown() -> None:
    assert calmar(Decimal("0.1"), Decimal("0.2"), 365) == Decimal("0.5")


def test_calmar_is_null_when_drawdown_is_zero() -> None:
    assert calmar(Decimal("0.1"), Decimal(0), 365) is None


def test_calmar_annualizes_over_a_span_other_than_one_year() -> None:
    assert calmar(Decimal("0.21"), Decimal("0.2"), 730) == Decimal("0.5")


def test_calmar_is_null_when_the_wealth_index_is_not_positive() -> None:
    assert calmar(Decimal("-1.1"), Decimal("0.5"), 100) is None


def test_beta_and_alpha_against_btc() -> None:
    beta, alpha = beta_alpha(
        (Decimal("0.02"), Decimal("0.04")),
        (Decimal("0.01"), Decimal("0.03")),
    )

    assert beta == Decimal(1)
    assert alpha == Decimal("3.65")


def test_constant_btc_returns_have_no_beta() -> None:
    beta, alpha = beta_alpha(
        (Decimal("0.02"), Decimal("0.04")),
        (Decimal("0.01"), Decimal("0.01")),
    )

    assert beta is None
    assert alpha is None
