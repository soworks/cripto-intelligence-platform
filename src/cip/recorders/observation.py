from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from cip.domain.errors import RecorderError

SCHEMA_VERSION = 1

# series -> (family, symbol required)
SERIES: dict[str, tuple[str, bool]] = {
    "btc_dominance": ("market_regime", False),
    "stablecoin_supply": ("market_regime", False),
    "funding": ("derivatives_positioning", True),
    "open_interest": ("derivatives_positioning", True),
    "spread": ("execution_liquidity", True),
    "depth": ("execution_liquidity", True),
}


class CollectionStatus(StrEnum):
    OK = "ok"


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RecorderError(f"{label} must be timezone-aware")
    return value


@dataclass(frozen=True)
class Observation:
    series: str
    provider: str
    source_timestamp: datetime | None
    observed_at: datetime
    symbol: str | None
    values: tuple[tuple[str, Decimal], ...]
    units: tuple[tuple[str, str], ...]
    _family: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        family, needs_symbol = _series(self.series)
        object.__setattr__(self, "_family", family)
        if self.provider == "":
            raise RecorderError("provider is required")
        _aware(self.observed_at, "observed_at")
        if self.source_timestamp is not None:
            _aware(self.source_timestamp, "source_timestamp")
        if needs_symbol and not self.symbol:
            raise RecorderError(f"{self.series} requires a symbol")
        if not needs_symbol and self.symbol is not None:
            raise RecorderError(f"{self.series} does not take a symbol")
        if not self.values:
            raise RecorderError("observation values are required")
        names = [name for name, _ in self.values]
        if len(names) != len(set(names)):
            raise RecorderError("observation value names must be unique")
        if tuple(name for name, _ in self.units) != tuple(names):
            raise RecorderError("units must match value names")
        if any(unit == "" for _, unit in self.units):
            raise RecorderError("units are required")

    @property
    def family(self) -> str:
        return self._family

    @property
    def identity_time(self) -> datetime:
        return self.source_timestamp if self.source_timestamp is not None else self.observed_at


@dataclass(frozen=True)
class CollectionFailure:
    series: str
    provider: str
    observed_at: datetime
    symbol: str | None
    error: str

    def __post_init__(self) -> None:
        _series(self.series)
        if self.provider == "" or self.error == "":
            raise RecorderError("a collection failure needs a provider and an error")
        _aware(self.observed_at, "observed_at")


def family_of(series: str) -> str:
    return _series(series)[0]


def observation_id(observation: Observation) -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "family": observation.family,
        "series": observation.series,
        "provider": observation.provider,
        "symbol": observation.symbol,
        "source_timestamp": observation.identity_time.isoformat(),
    }
    return hashlib.sha256(_canonical(payload)).hexdigest()


def failure_id(failure: CollectionFailure) -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "series": failure.series,
        "provider": failure.provider,
        "symbol": failure.symbol,
        "observed_at": failure.observed_at.isoformat(),
        "error": failure.error,
    }
    return hashlib.sha256(_canonical(payload)).hexdigest()


def observation_document(observation: Observation) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "family": observation.family,
        "series": observation.series,
        "provider": observation.provider,
        "source_timestamp": _stamp(observation.source_timestamp),
        "observed_at": observation.observed_at.isoformat(),
        "symbol": observation.symbol,
        "values": {name: format(value, "f") for name, value in observation.values},
        "units": dict(observation.units),
        "collection_status": CollectionStatus.OK.value,
    }


def failure_document(failure: CollectionFailure) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "collection_failure",
        "family": family_of(failure.series),
        "series": failure.series,
        "provider": failure.provider,
        "observed_at": failure.observed_at.isoformat(),
        "symbol": failure.symbol,
        "error": failure.error,
    }


def _series(series: str) -> tuple[str, bool]:
    try:
        return SERIES[series]
    except KeyError as error:
        raise RecorderError(f"unknown series {series}") from error


def _stamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
