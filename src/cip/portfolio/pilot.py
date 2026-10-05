"""Record one owner-executed pilot purchase. A record is not an order."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, ValidationError, model_validator

from cip.domain.errors import PilotError

_COHORT = "ALPHA_PILOT_2026_10"
_START = date(2026, 10, 19)
_END = date(2026, 10, 31)
_CAP = Decimal("500")
_DECISION = re.compile(r"^[0-9a-f]{64}$")
_SYMBOL = re.compile(r"^[A-Z0-9]{1,20}$")
Cohort = Literal["ALPHA_PILOT_2026_10"]


def _exact_cohort(value: object) -> str:
    if type(value) is not str or value != _COHORT:
        raise ValueError("cohort must be the October pilot")
    return value


def _exact_text(value: object) -> str:
    if type(value) is not str:
        raise ValueError("text values are strings")
    return value


def _exact_day(value: object) -> date:
    if type(value) is not date:
        raise ValueError("the execution day is a date")
    return value


def _exact_decimal(value: object) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise ValueError("money values are Decimal")
    return value


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class PilotExecution(_Strict):
    """One manual October purchase. There is no order id."""

    cohort: Annotated[Cohort, BeforeValidator(_exact_cohort)]
    decision_id: Annotated[str, BeforeValidator(_exact_text)]
    symbol: Annotated[str, BeforeValidator(_exact_text)]
    executed_on: Annotated[date, BeforeValidator(_exact_day)]
    quantity: Annotated[Decimal, BeforeValidator(_exact_decimal)]
    quote_usd: Annotated[Decimal, BeforeValidator(_exact_decimal)]
    spent_before_usd: Annotated[Decimal, BeforeValidator(_exact_decimal)]
    provenance: Annotated[str, BeforeValidator(_exact_text)]

    @model_validator(mode="after")
    def _matches(self) -> PilotExecution:
        if _DECISION.fullmatch(self.decision_id) is None:
            raise ValueError("decision id must be a sha256")
        if _SYMBOL.fullmatch(self.symbol) is None:
            raise ValueError("symbol must be an uppercase ticker")
        if self.executed_on < _START or self.executed_on > _END:
            raise ValueError("the purchase is inside the October pilot")
        if self.quantity <= 0 or self.quote_usd <= 0:
            raise ValueError("the purchase is positive")
        if self.spent_before_usd < 0 or self.spent_before_usd.is_signed():
            raise ValueError("pilot spend is non-negative")
        if self.spent_before_usd + self.quote_usd > _CAP:
            raise ValueError("the purchase stays inside the pilot capital")
        if self.provenance.strip() == "":
            raise ValueError("provenance is required")
        return self

    def to_document(self) -> dict[str, Any]:
        try:
            checked = PilotExecution.model_validate(self.model_dump())
        except ValidationError as error:
            raise PilotError("pilot execution is invalid") from error
        return {
            "cohort": checked.cohort,
            "decision_id": checked.decision_id,
            "symbol": checked.symbol,
            "executed_on": checked.executed_on.isoformat(),
            "quantity": format(checked.quantity, "f"),
            "quote_usd": format(checked.quote_usd, "f"),
            "provenance": checked.provenance,
        }


def record_pilot_execution(
    *,
    decision_id: str,
    symbol: str,
    executed_on: date,
    quantity: Decimal,
    quote_usd: Decimal,
    spent_before_usd: Decimal,
    provenance: str,
) -> PilotExecution:
    """Record a purchase the owner already made. The monthly budget is not this cap."""
    identity = _text(decision_id, "decision id")
    ticker = _text(symbol, "symbol")
    day = _day(executed_on)
    bought = _money(quantity, "quantity", positive=True)
    paid = _money(quote_usd, "quote", positive=True)
    spent = _money(spent_before_usd, "pilot spend", positive=False)
    source = _text(provenance, "provenance")
    if _DECISION.fullmatch(identity) is None:
        raise PilotError("decision id must be a sha256")
    if _SYMBOL.fullmatch(ticker) is None:
        raise PilotError("symbol must be an uppercase ticker")
    if day < _START or day > _END:
        raise PilotError("the purchase is inside the October pilot")
    if spent < 0 or spent.is_signed():
        raise PilotError("pilot spend is non-negative")
    if spent + paid > _CAP:
        raise PilotError("the purchase stays inside the pilot capital")
    if source.strip() == "":
        raise PilotError("provenance is required")
    return PilotExecution(
        cohort=_COHORT,
        decision_id=identity,
        symbol=ticker,
        executed_on=day,
        quantity=bought,
        quote_usd=paid,
        spent_before_usd=spent,
        provenance=source,
    )


def _text(value: object, label: str) -> str:
    if type(value) is not str:
        raise PilotError(f"{label} must be a string")
    return value


def _day(value: object) -> date:
    if type(value) is not date:
        raise PilotError("the execution day is a date")
    return value


def _money(value: object, label: str, *, positive: bool) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise PilotError(f"{label} must be a decimal")
    if positive and value <= 0:
        raise PilotError(f"{label} must be positive")
    return value
