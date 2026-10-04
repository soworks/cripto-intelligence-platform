"""Score v2. Weights are an input. This module does not invent them."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cip.domain.errors import EvaluationError
from cip.evaluation.features import (
    PENALTY_FEATURE,
    TOKENOMICS_FEATURES,
    TREND_FEATURES,
    WEIGHT_FEATURES,
)

_FORBIDDEN = frozenset({"liquidity", "portfolio_fit", "liquidity_score", "portfolio_fit_score"})


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class ScoreResult(_Strict):
    """Weighted components. Liquidity and portfolio fit are not among them."""

    score: Decimal
    components: dict[str, Decimal] = Field(min_length=1)

    @model_validator(mode="after")
    def _sum(self) -> Self:
        if sum(self.components.values(), Decimal(0)) != self.score:
            raise ValueError("score is the sum of its components")
        return self


def combine(features: Mapping[str, Decimal], weights: Mapping[str, Decimal]) -> ScoreResult:
    """Apply frozen weights. An empty map is not a score."""
    if not weights:
        raise EvaluationError("score weights are not frozen")
    if _FORBIDDEN & set(weights):
        raise EvaluationError("liquidity and portfolio fit stay out of the score")
    if set(weights) - WEIGHT_FEATURES:
        raise EvaluationError("unknown score weight")
    checked = {name: _weight(value) for name, value in weights.items()}
    components: dict[str, Decimal] = {}
    trend = _group(features, checked, TREND_FEATURES)
    if trend is not None:
        components["trend_rs"] = trend
    penalty = _penalty(features, checked)
    if penalty is not None:
        components["extension_penalty"] = penalty
    tokenomics = _group(features, checked, TOKENOMICS_FEATURES)
    if tokenomics is not None:
        components["tokenomics"] = tokenomics
    return ScoreResult(score=sum(components.values(), Decimal(0)), components=components)


def _group(
    features: Mapping[str, Decimal], weights: Mapping[str, Decimal], names: tuple[str, ...]
) -> Decimal | None:
    selected = [name for name in names if name in weights]
    if not selected:
        return None
    missing = [name for name in selected if name not in features]
    if missing:
        raise EvaluationError("score is missing a weighted feature")
    return sum((weights[name] * features[name] for name in selected), Decimal(0))


def _penalty(features: Mapping[str, Decimal], weights: Mapping[str, Decimal]) -> Decimal | None:
    if PENALTY_FEATURE not in weights:
        return None
    if PENALTY_FEATURE not in features:
        raise EvaluationError("score is missing a weighted feature")
    return -abs(weights[PENALTY_FEATURE]) * features[PENALTY_FEATURE]


def _weight(value: object) -> Decimal:
    if isinstance(value, (bool, float, int)) or not isinstance(value, Decimal):
        raise EvaluationError("weights are non-zero finite decimals")
    if not value.is_finite() or value == 0:
        raise EvaluationError("weights are non-zero finite decimals")
    return value
