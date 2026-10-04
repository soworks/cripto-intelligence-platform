from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal

from cip.domain.errors import BacktestError

_STEP_TOLERANCE = Decimal("1e-12")
_START_RATE = Decimal("0.1")
_YEAR_DAYS = Decimal(365)


def daily_returns(
    equity: Sequence[tuple[date, Decimal]],
    contributions: Mapping[date, Decimal],
) -> tuple[Decimal, ...]:
    returns: list[Decimal] = []
    for index in range(1, len(equity)):
        day, value = equity[index]
        previous = equity[index - 1][1]
        contribution = contributions.get(day, Decimal(0))
        returns.append((value - contribution) / previous - 1)
    return tuple(returns)


def time_weighted_return(returns: Sequence[Decimal]) -> Decimal:
    index = Decimal(1)
    for daily in returns:
        index *= 1 + daily
    return index - 1


def cash_flows(
    schedule: Sequence[tuple[date, Decimal]],
    final_day: date,
    final_equity: Decimal,
) -> tuple[tuple[date, Decimal], ...]:
    totals: dict[date, Decimal] = {}
    for day, amount in schedule:
        totals[day] = -amount
    totals[final_day] = totals.get(final_day, Decimal(0)) + final_equity
    return tuple(sorted(totals.items()))


def solve_xirr(
    flows: Sequence[tuple[date, Decimal]],
    *,
    max_iterations: int = 100,
) -> Decimal:
    origin = flows[0][0]
    rate = _START_RATE
    for _ in range(max_iterations):
        npv = Decimal(0)
        derivative = Decimal(0)
        for day, amount in flows:
            exponent = Decimal((day - origin).days) / _YEAR_DAYS
            growth = (1 + rate) ** exponent
            npv += amount / growth
            derivative += -amount * exponent / ((1 + rate) ** (exponent + 1))
        if derivative == 0:
            raise BacktestError("XIRR derivative is zero")
        step = npv / derivative
        if abs(step) < _STEP_TOLERANCE:
            return rate
        nxt = rate - step
        if nxt <= -1:
            raise BacktestError("XIRR rate is below -1")
        rate = nxt
    raise BacktestError("XIRR did not converge")


def sharpe(returns: Sequence[Decimal]) -> Decimal | None:
    mean = _mean(returns)
    variance = _sample_variance(returns, mean)
    if variance == 0:
        return None
    return mean / variance.sqrt() * _YEAR_DAYS.sqrt()


def sortino(returns: Sequence[Decimal]) -> Decimal | None:
    count = Decimal(len(returns) - 1)
    downside = sum((min(daily, Decimal(0)) ** 2 for daily in returns), start=Decimal(0)) / count
    if downside == 0:
        return None
    return _mean(returns) / downside.sqrt() * _YEAR_DAYS.sqrt()


def max_drawdown(returns: Sequence[Decimal]) -> Decimal:
    index = Decimal(1)
    peak = index
    worst = Decimal(0)
    for daily in returns:
        index *= 1 + daily
        if index > peak:
            peak = index
        drawdown = (peak - index) / peak
        if drawdown > worst:
            worst = drawdown
    return worst


def calmar(twr: Decimal, drawdown: Decimal, span_days: int) -> Decimal | None:
    if drawdown == 0 or (1 + twr) <= 0:
        return None
    annualized = (1 + twr) ** (_YEAR_DAYS / Decimal(span_days)) - 1
    return annualized / drawdown


def beta_alpha(
    mixed: Sequence[Decimal],
    btc: Sequence[Decimal],
) -> tuple[Decimal | None, Decimal | None]:
    mean_mixed = _mean(mixed)
    mean_btc = _mean(btc)
    variance = _sample_variance(btc, mean_btc)
    if variance == 0:
        return None, None
    count = Decimal(len(mixed) - 1)
    covariance = (
        sum(
            (
                (left - mean_mixed) * (right - mean_btc)
                for left, right in zip(mixed, btc, strict=True)
            ),
            start=Decimal(0),
        )
        / count
    )
    beta = covariance / variance
    alpha = (mean_mixed - beta * mean_btc) * _YEAR_DAYS
    return beta, alpha


def _mean(values: Sequence[Decimal]) -> Decimal:
    return sum(values, start=Decimal(0)) / Decimal(len(values))


def _sample_variance(values: Sequence[Decimal], mean: Decimal) -> Decimal:
    return sum(((value - mean) ** 2 for value in values), start=Decimal(0)) / Decimal(
        len(values) - 1
    )
