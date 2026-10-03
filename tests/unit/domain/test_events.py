import math
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from cip.domain.errors import InvalidEventError
from cip.domain.events import EventType, LedgerEvent

FIXED = datetime(2026, 10, 3, 16, 0, 0, 123456, tzinfo=UTC)


def _event(**overrides: object) -> LedgerEvent:
    fields: dict[str, object] = {
        "event_type": EventType.SCAN_STARTED,
        "correlation_id": "corr-1",
        "policy_version": "abc123",
        "created_at": FIXED,
        "event_id": "e1",
    }
    fields.update(overrides)
    return LedgerEvent.model_validate(fields)


def _derived(**overrides: object) -> LedgerEvent:
    fields: dict[str, object] = {
        "event_type": EventType.SCAN_STARTED,
        "correlation_id": "corr-1",
        "policy_version": "abc123",
    }
    fields.update(overrides)
    return LedgerEvent.model_validate(fields)


def test_item_keys_follow_ledger_schema() -> None:
    item = _event(asset="BTCUSDT").to_item()
    assert item["PK"] == "ASSET#BTCUSDT"
    assert item["SK"] == "EVENT#2026-10-03T16:00:00.123456Z#e1"
    assert item["GSI1PK"] == "CORR#corr-1"
    assert item["GSI1SK"] == "2026-10-03T16:00:00.123456Z#e1"
    assert item["GSI2PK"] == "TYPE#SCAN_STARTED#2026-10-03"


def test_asset_defaults_to_global() -> None:
    assert _event().to_item()["PK"] == "ASSET#GLOBAL"


def test_round_trip_preserves_event_including_floats() -> None:
    event = _event(payload={"score": 0.25, "count": 3, "nested": {"ok": True, "xs": [1.5]}})
    assert LedgerEvent.from_item(event.to_item()) == event


def test_round_trip_preserves_numeric_types_exactly() -> None:
    payload = {
        "whole_float": 1.0,
        "int": 3,
        "huge_float": 1.7976931348623157e308,
        "subnormal": 5e-324,
        "big_int": 10**40,
        "nested": [2.0, {"zero": 0.0, "none": None}],
    }
    restored = LedgerEvent.from_item(_event(payload=payload).to_item()).payload
    assert restored == payload
    assert type(restored["whole_float"]) is float
    assert type(restored["nested"][0]) is float
    assert type(restored["big_int"]) is int


def test_payload_is_stored_as_json_string() -> None:
    item = _event(payload={"b": 1, "a": 2.0}).to_item()
    assert item["payload"] == '{"a":2.0,"b":1}'


@pytest.mark.parametrize(
    "payload",
    [
        {"x": math.nan},
        {"x": math.inf},
        {"nested": {"xs": [-math.inf]}},
        {"x": 10**5000},
        {"x": {1, 2}},
        {"x": FIXED},
    ],
)
def test_unstorable_payload_raises_domain_error(payload: dict[str, object]) -> None:
    with pytest.raises(InvalidEventError):
        _event(payload=payload)


def test_event_id_is_deterministic_and_ignores_timestamp() -> None:
    first = _derived(created_at=FIXED)
    retry = _derived(created_at=FIXED + timedelta(seconds=30))
    assert first.event_id == retry.event_id
    assert len(first.event_id) == 32


@pytest.mark.parametrize(
    "change",
    [
        {"correlation_id": "corr-2"},
        {"event_type": EventType.SCAN_COMPLETED},
        {"asset": "BTCUSDT"},
        {"idempotency_key": "attempt-2"},
    ],
)
def test_event_id_changes_with_logical_identity(change: dict[str, object]) -> None:
    assert _derived(**change).event_id != _derived().event_id


def test_event_id_encoding_is_unambiguous() -> None:
    assert (
        _derived(correlation_id="a:b", asset="c").event_id
        != _derived(correlation_id="a", asset="b:c").event_id
    )


def test_idempotency_key_round_trips() -> None:
    event = _derived(idempotency_key="k1", created_at=FIXED)
    assert LedgerEvent.from_item(event.to_item()) == event


def test_empty_idempotency_key_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _derived(idempotency_key="")


def test_invalid_identity_fields_raise_validation_error() -> None:
    with pytest.raises(ValidationError):
        _derived(correlation_id="")


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _event(created_at=datetime(2026, 10, 3, 16, 0, 0))  # noqa: DTZ001


def test_non_utc_offset_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _event(created_at=datetime(2026, 10, 3, 11, 0, tzinfo=timezone(timedelta(hours=-5))))


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _event(unexpected="x")
