from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

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


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _event(created_at=datetime(2026, 10, 3, 16, 0, 0))  # noqa: DTZ001


def test_non_utc_offset_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _event(created_at=datetime(2026, 10, 3, 11, 0, tzinfo=timezone(timedelta(hours=-5))))


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _event(unexpected="x")
