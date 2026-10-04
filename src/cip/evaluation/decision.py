from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_SYMBOL = re.compile(r"^[A-Z0-9]{1,20}$")
DECISION_SCHEMA_VERSION: Literal[2] = 2
OUTCOME_SCHEMA_VERSION: Literal[1] = 1
_DECISION_FIELDS = frozenset(
    {
        "schema_version",
        "cohort",
        "symbol",
        "evaluated_at",
        "policy_version",
        "git_sha",
        "disposition",
        "reason_codes",
        "features",
        "score",
        "score_components",
        "rank",
        "regime",
        "raw_regime",
        "sources",
    }
)
_SOURCE_FIELDS = frozenset({"name", "observed_at", "provenance"})


class Cohort(StrEnum):
    BACKTEST = "BACKTEST"
    ALPHA_PILOT_2026_10 = "ALPHA_PILOT_2026_10"
    SHADOW = "SHADOW"
    LIVE = "LIVE"


class Disposition(StrEnum):
    INELIGIBLE = "INELIGIBLE"
    SCORED = "SCORED"
    BUY = "BUY"


def _utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{label} must be timezone-aware UTC")
    return value.astimezone(UTC)


def _decimal_string(value: object) -> Decimal:
    if not isinstance(value, str):
        raise ValueError("decimal values are strings")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("decimal values are strings") from error
    if not parsed.is_finite():
        raise ValueError("decimal values are finite")
    return parsed


def _decimal_map(value: object) -> dict[str, Decimal]:
    if not isinstance(value, dict):
        raise ValueError("decimal maps are objects")
    parsed: dict[str, Decimal] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError("decimal map keys are strings")
        parsed[key] = _decimal_string(item)
    return parsed


def _finite_decimal(value: object) -> Decimal:
    if isinstance(value, (bool, float, int)):
        raise ValueError("decimal values are Decimal or decimal strings")
    if isinstance(value, str):
        return _decimal_string(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("decimal values are finite")
        return value
    raise ValueError("decimal values are Decimal or decimal strings")


def _optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    return _finite_decimal(value)


def _decimal_values(value: object) -> dict[str, Decimal]:
    if not isinstance(value, dict):
        raise ValueError("decimal maps are objects")
    parsed: dict[str, Decimal] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError("decimal map keys are strings")
        parsed[key] = _finite_decimal(item)
    return parsed


def _optional_decimal_values(value: object) -> dict[str, Decimal] | None:
    if value is None:
        return None
    return _decimal_values(value)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class SourceStamp(_Strict):
    name: str = Field(min_length=1)
    observed_at: datetime
    provenance: str = Field(min_length=1)

    @field_validator("observed_at")
    @classmethod
    def _source_time(cls, value: datetime) -> datetime:
        return _utc(value, "observed_at")


class DecisionRecord(_Strict):
    """One immutable evaluation. A BUY is a recommendation, not an order."""

    schema_version: Literal[2] = DECISION_SCHEMA_VERSION
    cohort: Cohort
    symbol: str
    evaluated_at: datetime
    policy_version: str = Field(min_length=1)
    git_sha: str
    disposition: Disposition
    reason_codes: tuple[str, ...] = Field(min_length=1)
    features: Annotated[dict[str, Decimal], BeforeValidator(_decimal_values)]
    score: Annotated[Decimal | None, BeforeValidator(_optional_decimal)]
    score_components: Annotated[
        dict[str, Decimal] | None, BeforeValidator(_optional_decimal_values)
    ]
    rank: int | None = Field(default=None, ge=1)
    regime: Literal["RISK_ON", "NEUTRAL", "RISK_OFF"] | None
    raw_regime: Literal["RISK_ON", "NEUTRAL", "RISK_OFF"] | None
    sources: tuple[SourceStamp, ...] = Field(min_length=1)

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, value: str) -> str:
        if _SYMBOL.fullmatch(value) is None:
            raise ValueError("symbol must be 1 to 20 uppercase letters or digits")
        return value

    @field_validator("evaluated_at")
    @classmethod
    def _evaluated(cls, value: datetime) -> datetime:
        return _utc(value, "evaluated_at")

    @field_validator("git_sha")
    @classmethod
    def _sha(cls, value: str) -> str:
        if _SHA.fullmatch(value) is None:
            raise ValueError("git_sha must be 40 lowercase hex characters")
        return value

    @field_validator("reason_codes")
    @classmethod
    def _codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(_CODE.fullmatch(code) is None for code in value):
            raise ValueError("reason codes are lowercase snake_case")
        return value

    @model_validator(mode="after")
    def _disposition_matches_evidence(self) -> Self:
        if self.regime is not None and self.raw_regime is None:
            raise ValueError("a published regime has a raw state")
        if self.disposition is Disposition.INELIGIBLE:
            if self.score is not None or self.score_components is not None or self.rank is not None:
                raise ValueError("an ineligible decision has no score or rank")
            return self
        if (
            self.score is None
            or not self.score_components
            or self.rank is None
            or self.regime is None
            or not self.features
        ):
            raise ValueError(
                "a scored decision needs features, a score, components, a rank, and a regime"
            )
        if self.disposition is Disposition.BUY and self.regime == "RISK_OFF":
            raise ValueError("RISK_OFF cannot produce a BUY")
        return self

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "cohort": self.cohort.value,
            "symbol": self.symbol,
            "evaluated_at": self.evaluated_at.isoformat(),
            "policy_version": self.policy_version,
            "git_sha": self.git_sha,
            "disposition": self.disposition.value,
            "reason_codes": list(self.reason_codes),
            "features": {name: format(value, "f") for name, value in self.features.items()},
            "score": None if self.score is None else format(self.score, "f"),
            "score_components": None
            if self.score_components is None
            else {name: format(value, "f") for name, value in self.score_components.items()},
            "rank": self.rank,
            "regime": self.regime,
            "raw_regime": self.raw_regime,
            "sources": [
                {
                    "name": source.name,
                    "observed_at": source.observed_at.isoformat(),
                    "provenance": source.provenance,
                }
                for source in self.sources
            ],
        }

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> Self:
        if set(document) != _DECISION_FIELDS:
            raise ValueError("decision document keys are fixed")
        sources = document["sources"]
        if not isinstance(sources, list) or any(
            not isinstance(item, dict) or set(item) != _SOURCE_FIELDS for item in sources
        ):
            raise ValueError("decision document keys are fixed")
        components = document["score_components"]
        return cls(
            schema_version=document["schema_version"],
            cohort=document["cohort"],
            symbol=document["symbol"],
            evaluated_at=datetime.fromisoformat(document["evaluated_at"]),
            policy_version=document["policy_version"],
            git_sha=document["git_sha"],
            disposition=document["disposition"],
            reason_codes=tuple(document["reason_codes"]),
            features=_decimal_map(document["features"]),
            score=None if document["score"] is None else _decimal_string(document["score"]),
            score_components=None if components is None else _decimal_map(components),
            rank=document["rank"],
            regime=document["regime"],
            raw_regime=document["raw_regime"],
            sources=tuple(
                SourceStamp(
                    name=item["name"],
                    observed_at=datetime.fromisoformat(item["observed_at"]),
                    provenance=item["provenance"],
                )
                for item in sources
            ),
        )


class ForwardOutcome(_Strict):
    """Later market results for one decision. This object cannot restate the decision."""

    schema_version: Literal[1] = OUTCOME_SCHEMA_VERSION
    decision_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    horizon_days: Literal[7, 14, 30, 60]
    absolute_return: Annotated[Decimal, BeforeValidator(_finite_decimal)]
    btc_return: Annotated[Decimal, BeforeValidator(_finite_decimal)]
    excess_return: Annotated[Decimal, BeforeValidator(_finite_decimal)]
    universe_relative_return: Annotated[Decimal | None, BeforeValidator(_optional_decimal)]
    mfe: Annotated[Decimal, BeforeValidator(_finite_decimal)]
    mae: Annotated[Decimal, BeforeValidator(_finite_decimal)]
    price_timestamp: datetime
    btc_price_timestamp: datetime

    @field_validator("price_timestamp", "btc_price_timestamp")
    @classmethod
    def _price_time(cls, value: datetime) -> datetime:
        return _utc(value, "price timestamp")

    @model_validator(mode="after")
    def _window(self) -> Self:
        if self.excess_return != self.absolute_return - self.btc_return:
            raise ValueError("excess return is the asset return minus the BTC return")
        if self.mae > 0 or self.mfe < 0:
            raise ValueError("MAE is at most zero and MFE is at least zero")
        return self

    def to_document(self) -> dict[str, Any]:
        relative = self.universe_relative_return
        return {
            "schema_version": self.schema_version,
            "decision_id": self.decision_id,
            "horizon_days": self.horizon_days,
            "absolute_return": format(self.absolute_return, "f"),
            "btc_return": format(self.btc_return, "f"),
            "excess_return": format(self.excess_return, "f"),
            "universe_relative_return": None if relative is None else format(relative, "f"),
            "mfe": format(self.mfe, "f"),
            "mae": format(self.mae, "f"),
            "price_timestamp": self.price_timestamp.isoformat(),
            "btc_price_timestamp": self.btc_price_timestamp.isoformat(),
        }


def decision_id(record: DecisionRecord) -> str:
    identity = {
        "schema_version": record.schema_version,
        "cohort": record.cohort.value,
        "symbol": record.symbol,
        "evaluated_at": record.evaluated_at.isoformat(),
        "policy_version": record.policy_version,
        "git_sha": record.git_sha,
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
