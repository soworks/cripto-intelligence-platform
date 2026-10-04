from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from cip.domain.errors import BacktestError

_CENT = Decimal("0.01")
_WEIGHT_TOLERANCE = Decimal("1e-9")


def contribution_schedule(
    first: date,
    last: date,
    starting_usd: Decimal,
    monthly_usd: Decimal,
) -> tuple[tuple[date, Decimal], ...]:
    items = [(first, starting_usd)]
    year, month = _next_month(first.year, first.month)
    while True:
        current = date(year, month, 1)
        if current > last:
            return tuple(items)
        items.append((current, monthly_usd))
        year, month = _next_month(year, month)


def allocate(amount: Decimal, weights: Mapping[str, Decimal]) -> dict[str, Decimal]:
    total = sum(weights.values(), start=Decimal(0))
    if abs(total - 1) > _WEIGHT_TOLERANCE:
        raise BacktestError("weights must sum to 1")
    symbols = sorted(weights)
    legs: dict[str, Decimal] = {}
    consumed = Decimal(0)
    for symbol in symbols[:-1]:
        leg = (amount * weights[symbol]).quantize(_CENT, rounding=ROUND_HALF_UP)
        if leg <= 0:
            raise BacktestError("allocation leg must be positive")
        legs[symbol] = leg
        consumed += leg
    residual = amount - consumed
    if residual <= 0:
        raise BacktestError("allocation leg must be positive")
    legs[symbols[-1]] = residual
    return legs


def _next_month(year: int, month: int) -> tuple[int, int]:
    if month == 12:
        return year + 1, 1
    return year, month + 1
