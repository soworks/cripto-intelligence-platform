import warnings
from datetime import date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from cip.domain.errors import PilotError
from cip.portfolio.pilot import PilotExecution, record_pilot_execution

_DECISION = "a" * 64
_KEYS = {
    "cohort",
    "decision_id",
    "symbol",
    "executed_on",
    "quantity",
    "quote_usd",
    "provenance",
}


def _record(**overrides: object) -> PilotExecution:
    values: dict[str, object] = {
        "decision_id": _DECISION,
        "symbol": "BTCUSDT",
        "executed_on": date(2026, 10, 19),
        "quantity": Decimal("0.001"),
        "quote_usd": Decimal("100.00"),
        "spent_before_usd": Decimal("0"),
        "provenance": "binance spot history",
    }
    values.update(overrides)
    return record_pilot_execution(**values)  # type: ignore[arg-type]


def test_an_october_purchase_is_recorded_on_the_pilot_cohort() -> None:
    recorded = _record()
    assert recorded.cohort == "ALPHA_PILOT_2026_10"
    assert recorded.quantity == Decimal("0.001")
    document = recorded.to_document()
    assert set(document) == _KEYS
    assert document["executed_on"] == "2026-10-19"
    assert document["quote_usd"] == "100.00"
    assert "order_id" not in document
    with pytest.raises(ValidationError):
        PilotExecution.model_validate({**recorded.model_dump(), "order_id": "1"})
    last = _record(executed_on=date(2026, 10, 31), quote_usd=Decimal("500"))
    assert last.executed_on == date(2026, 10, 31)
    room = _record(spent_before_usd=Decimal("499.99"), quote_usd=Decimal("0.01"))
    assert room.quote_usd == Decimal("0.01")


def test_the_monthly_budget_is_not_the_pilot_cap() -> None:
    with pytest.raises(PilotError, match="pilot capital"):
        _record(quote_usd=Decimal("500.01"))
    with pytest.raises(PilotError, match="pilot capital"):
        _record(spent_before_usd=Decimal("400"), quote_usd=Decimal("100.01"))


def test_a_purchase_outside_the_pilot_is_refused() -> None:
    with pytest.raises(PilotError, match="October pilot"):
        _record(executed_on=date(2026, 10, 18))
    with pytest.raises(PilotError, match="October pilot"):
        _record(executed_on=date(2026, 11, 1))
    with pytest.raises(PilotError, match="date"):
        _record(executed_on=datetime(2026, 10, 19, 12, 0))  # noqa: DTZ001
    with pytest.raises(PilotError, match="sha256"):
        _record(decision_id="A" * 64)
    with pytest.raises(PilotError, match="ticker"):
        _record(symbol="btcusdt")
    with pytest.raises(PilotError, match="non-negative"):
        _record(spent_before_usd=Decimal("-1"))
    with pytest.raises(PilotError, match="non-negative"):
        _record(spent_before_usd=Decimal("-0"))
    with pytest.raises(PilotError, match="positive"):
        _record(quantity=Decimal("0"))
    with pytest.raises(PilotError, match="positive"):
        _record(quote_usd=Decimal("0"))
    with pytest.raises(PilotError, match="provenance"):
        _record(provenance="  ")
    with pytest.raises(PilotError, match="string"):
        _record(symbol=1)
    with pytest.raises(PilotError, match="decimal"):
        _record(quantity=1)
    with pytest.raises(PilotError, match="decimal"):
        _record(quote_usd=Decimal("NaN"))


def _raw(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "cohort": "ALPHA_PILOT_2026_10",
        "decision_id": _DECISION,
        "symbol": "ETHUSDT",
        "executed_on": date(2026, 10, 20),
        "quantity": Decimal("0.01"),
        "quote_usd": Decimal("40"),
        "spent_before_usd": Decimal("0"),
        "provenance": "owner note",
    }
    values.update(overrides)
    return values


def test_a_document_that_is_not_a_pilot_purchase_is_refused() -> None:
    with pytest.raises(ValidationError):
        PilotExecution.model_validate(_raw(cohort=1))
    with pytest.raises(ValidationError):
        PilotExecution.model_validate(_raw(cohort="SHADOW"))
    with pytest.raises(ValidationError):
        PilotExecution.model_validate(_raw(decision_id=1))
    with pytest.raises(ValidationError):
        PilotExecution.model_validate(_raw(decision_id="nope"))
    with pytest.raises(ValidationError):
        PilotExecution.model_validate(_raw(symbol="eth"))
    with pytest.raises(ValidationError):
        PilotExecution.model_validate(_raw(executed_on=date(2026, 10, 18)))
    with pytest.raises(ValidationError):
        PilotExecution.model_validate(_raw(executed_on=date(2026, 11, 1)))
    with pytest.raises(ValidationError):
        PilotExecution.model_validate(_raw(executed_on="2026-10-20"))
    with pytest.raises(ValidationError):
        PilotExecution.model_validate(_raw(quantity=Decimal("0")))
    with pytest.raises(ValidationError):
        PilotExecution.model_validate(_raw(quote_usd=Decimal("-1")))
    with pytest.raises(ValidationError):
        PilotExecution.model_validate(_raw(spent_before_usd=Decimal("-0.01")))
    with pytest.raises(ValidationError):
        PilotExecution.model_validate(_raw(spent_before_usd=Decimal("-0")))
    with pytest.raises(ValidationError):
        PilotExecution.model_validate(_raw(quote_usd=Decimal("500.01")))
    with pytest.raises(ValidationError):
        PilotExecution.model_validate(_raw(provenance=" "))
    with pytest.raises(ValidationError):
        PilotExecution.model_validate(_raw(quantity=Decimal("NaN")))


def test_a_copied_purchase_over_the_pilot_cap_is_refused() -> None:
    recorded = _record()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        copied = recorded.model_copy(update={"quote_usd": Decimal("800")})
    with pytest.raises(PilotError, match="invalid"):
        copied.to_document()
