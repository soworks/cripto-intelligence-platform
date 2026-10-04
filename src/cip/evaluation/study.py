"""Information coefficients. The study reports them and does not choose weights."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from cip.domain.errors import EvaluationError

RegimeName = Literal["RISK_ON", "NEUTRAL", "RISK_OFF"]

# B5 refuses re-weighting on fewer than 30 closed trades. The same floor blocks a
# coefficient, and a coefficient still does not become a weight.
MINIMUM_SAMPLE = 30


def _finite_decimal(value: object) -> Decimal:
    if isinstance(value, (bool, float, int)):
        raise ValueError("decimal values are Decimal or decimal strings")
    if isinstance(value, str):
        try:
            parsed = Decimal(value)
        except InvalidOperation as error:
            raise ValueError("decimal values are Decimal or decimal strings") from error
    elif isinstance(value, Decimal):
        parsed = value
    else:
        raise ValueError("decimal values are Decimal or decimal strings")
    if not parsed.is_finite():
        raise ValueError("decimal values are finite")
    return parsed


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class StudyRow(_Strict):
    name: str = Field(min_length=1)
    regime: RegimeName
    value: Annotated[Decimal, BeforeValidator(_finite_decimal)]
    forward_excess_return: Annotated[Decimal, BeforeValidator(_finite_decimal)]


class Coefficient(_Strict):
    name: str
    regime: RegimeName
    observations: int = Field(ge=0)
    coefficient: Decimal | None
    reason_codes: tuple[str, ...] = Field(min_length=1)


def information_coefficients(rows: Sequence[StudyRow]) -> tuple[Coefficient, ...]:
    """Spearman correlation of one feature with later excess return, per regime."""
    groups: dict[tuple[str, RegimeName], list[StudyRow]] = defaultdict(list)
    for row in rows:
        groups[(row.name, row.regime)].append(row)
    return tuple(
        _coefficient(name, regime, groups[(name, regime)]) for name, regime in sorted(groups)
    )


def freeze_weights(coefficients: Sequence[Coefficient]) -> None:
    """Refuse to turn a study into policy weights."""
    if not coefficients or any(item.coefficient is None for item in coefficients):
        raise EvaluationError("the study cannot freeze weights")
    raise EvaluationError("the study reports coefficients and does not choose weights")


def _coefficient(name: str, regime: RegimeName, rows: list[StudyRow]) -> Coefficient:
    if len(rows) < MINIMUM_SAMPLE:
        return _missing(name, regime, rows, "sample_below_minimum")
    coefficient = _spearman(
        [row.value for row in rows],
        [row.forward_excess_return for row in rows],
    )
    if coefficient is None:
        return _missing(name, regime, rows, "undefined_correlation")
    return Coefficient(
        name=name,
        regime=regime,
        observations=len(rows),
        coefficient=coefficient,
        reason_codes=("coefficient",),
    )


def _missing(name: str, regime: RegimeName, rows: list[StudyRow], reason: str) -> Coefficient:
    return Coefficient(
        name=name,
        regime=regime,
        observations=len(rows),
        coefficient=None,
        reason_codes=(reason,),
    )


def _spearman(left: list[Decimal], right: list[Decimal]) -> Decimal | None:
    ranked_left = _ranks(left)
    ranked_right = _ranks(right)
    mean_left = sum(ranked_left, Decimal(0)) / Decimal(len(ranked_left))
    mean_right = sum(ranked_right, Decimal(0)) / Decimal(len(ranked_right))
    pairs = zip(ranked_left, ranked_right, strict=True)
    covariance = sum((one - mean_left) * (other - mean_right) for one, other in pairs)
    left_scale = sum((value - mean_left) ** 2 for value in ranked_left)
    right_scale = sum((value - mean_right) ** 2 for value in ranked_right)
    if left_scale == 0 or right_scale == 0:
        return None
    return covariance / (left_scale.sqrt() * right_scale.sqrt())


def _ranks(values: list[Decimal]) -> list[Decimal]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [Decimal(0)] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        average = Decimal(start + 1 + end + 1) / 2
        for cursor in range(start, end + 1):
            ranks[order[cursor]] = average
        start = end + 1
    return ranks
