from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from botocore.exceptions import ClientError

from cip.domain.errors import DuplicateEventError
from cip.domain.events import EventType, LedgerEvent
from cip.persistence.ledger import LedgerRepository

T0 = datetime(2026, 10, 3, 16, 0, tzinfo=UTC)


def _event(
    event_type: EventType, created_at: datetime, correlation_id: str = "corr-1"
) -> LedgerEvent:
    return LedgerEvent(
        event_type=event_type,
        correlation_id=correlation_id,
        policy_version="v1",
        created_at=created_at,
    )


def test_append_then_list_by_correlation(ledger_table: Any) -> None:
    repo = LedgerRepository(ledger_table)
    event = _event(EventType.SCAN_STARTED, T0)
    repo.append(event)
    assert repo.list_by_correlation("corr-1") == [event]


def test_duplicate_append_raises(ledger_table: Any) -> None:
    repo = LedgerRepository(ledger_table)
    event = _event(EventType.SCAN_STARTED, T0)
    repo.append(event)
    with pytest.raises(DuplicateEventError):
        repo.append(event)


def test_events_are_time_ordered_and_scoped_to_correlation(ledger_table: Any) -> None:
    repo = LedgerRepository(ledger_table)
    completed = _event(EventType.SCAN_COMPLETED, T0 + timedelta(seconds=5))
    started = _event(EventType.SCAN_STARTED, T0)
    other = _event(EventType.SCAN_STARTED, T0, correlation_id="corr-2")
    for event in (completed, started, other):
        repo.append(event)
    assert repo.list_by_correlation("corr-1") == [started, completed]


def test_unknown_correlation_returns_empty(ledger_table: Any) -> None:
    assert LedgerRepository(ledger_table).list_by_correlation("missing") == []


def test_non_duplicate_client_error_is_reraised() -> None:
    class ThrottledTable:
        def put_item(self, **_: Any) -> Any:
            raise ClientError(
                {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "slow"}},
                "PutItem",
            )

    repo = LedgerRepository(ThrottledTable())  # type: ignore[arg-type]
    with pytest.raises(ClientError):
        repo.append(_event(EventType.SCAN_STARTED, T0))


def test_list_by_correlation_follows_pagination() -> None:
    first = _event(EventType.SCAN_STARTED, T0)
    second = _event(EventType.SCAN_COMPLETED, T0 + timedelta(seconds=1))

    class PagedTable:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def query(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            if "ExclusiveStartKey" not in kwargs:
                return {"Items": [first.to_item()], "LastEvaluatedKey": {"PK": "page-2"}}
            return {"Items": [second.to_item()]}

    table = PagedTable()
    assert LedgerRepository(table).list_by_correlation("corr-1") == [first, second]  # type: ignore[arg-type]
    assert table.calls[1]["ExclusiveStartKey"] == {"PK": "page-2"}
