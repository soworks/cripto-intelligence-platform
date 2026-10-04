"""Replay contracts. M3 and M4 implement these. The simulator must call them."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Protocol, runtime_checkable


@runtime_checkable
class FeatureCalculator(Protocol):
    def features(self, symbol: str, as_of: date) -> dict[str, Decimal]: ...


@runtime_checkable
class Eligibility(Protocol):
    def eligible(self, symbol: str, as_of: date) -> bool: ...


@runtime_checkable
class Scorer(Protocol):
    def score(self, symbol: str, as_of: date) -> Decimal: ...


@runtime_checkable
class Sizer(Protocol):
    def size_usd(self, symbol: str, as_of: date) -> Decimal: ...


@runtime_checkable
class ExitRules(Protocol):
    def exit_due(self, symbol: str, as_of: date) -> bool: ...
