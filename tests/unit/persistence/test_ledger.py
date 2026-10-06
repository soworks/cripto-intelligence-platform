from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from botocore.exceptions import ClientError

from cip.domain.errors import DuplicateEventError, InvalidEventError
from cip.domain.events import EventType, LedgerEvent
from cip.persistence.ledger import LedgerRepository

T0 = datetime(2026, 10, 3, 16, 0, tzinfo=UTC)


def _event(
    event_type: EventType,
    created_at: datetime,
    correlation_id: str = "corr-1",
    idempotency_key: str | None = None,
) -> LedgerEvent:
    return LedgerEvent(
        event_type=event_type,
        correlation_id=correlation_id,
        policy_version="v1",
        created_at=created_at,
        idempotency_key=idempotency_key,
    )


def _failing_table(error: ClientError) -> Any:
    def transact_write_items(**_: Any) -> Any:
        raise error

    client = SimpleNamespace(transact_write_items=transact_write_items)
    return SimpleNamespace(name="cip-test-ledger", meta=SimpleNamespace(client=client))


def test_append_then_list_by_correlation(ledger_table: Any) -> None:
    repo = LedgerRepository(ledger_table)
    event = _event(EventType.SCAN_STARTED, T0)
    repo.append(event)
    assert repo.list_by_correlation("corr-1") == [event]


def test_append_writes_event_and_idempotency_guard(ledger_table: Any) -> None:
    event = _event(EventType.SCAN_STARTED, T0)
    LedgerRepository(ledger_table).append(event)
    guard = ledger_table.get_item(Key={"PK": f"IDEMP#{event.event_id}", "SK": "IDEMP"})["Item"]
    assert guard["event_sk"] == event.to_item()["SK"]
    assert ledger_table.scan(Select="COUNT")["Count"] == 2


def test_duplicate_append_raises(ledger_table: Any) -> None:
    repo = LedgerRepository(ledger_table)
    event = _event(EventType.SCAN_STARTED, T0)
    repo.append(event)
    with pytest.raises(DuplicateEventError):
        repo.append(event)


def test_retry_with_later_timestamp_is_rejected_as_duplicate(ledger_table: Any) -> None:
    repo = LedgerRepository(ledger_table)
    original = _event(EventType.SCAN_STARTED, T0)
    repo.append(original)
    with pytest.raises(DuplicateEventError):
        repo.append(_event(EventType.SCAN_STARTED, T0 + timedelta(seconds=7)))
    assert repo.list_by_correlation("corr-1") == [original]
    assert ledger_table.scan(Select="COUNT")["Count"] == 2


def test_distinct_idempotency_keys_are_both_recorded(ledger_table: Any) -> None:
    repo = LedgerRepository(ledger_table)
    first = _event(EventType.PIPELINE_FAILED, T0, idempotency_key="a")
    second = _event(EventType.PIPELINE_FAILED, T0, idempotency_key="b")
    repo.append(first)
    repo.append(second)
    assert len(repo.list_by_correlation("corr-1")) == 2


def test_events_are_time_ordered_and_scoped_to_correlation(ledger_table: Any) -> None:
    repo = LedgerRepository(ledger_table)
    completed = _event(EventType.SCAN_COMPLETED, T0 + timedelta(seconds=5))
    started = _event(EventType.SCAN_STARTED, T0)
    other = _event(EventType.SCAN_STARTED, T0, correlation_id="corr-2")
    for event in (completed, started, other):
        repo.append(event)
    assert repo.list_by_correlation("corr-1") == [started, completed]


@pytest.mark.parametrize("reverse", [False, True])
def test_equal_timestamps_order_deterministically_by_event_id(
    ledger_table: Any, reverse: bool
) -> None:
    repo = LedgerRepository(ledger_table)
    events = [_event(event_type, T0) for event_type in EventType]
    for event in reversed(events) if reverse else events:
        repo.append(event)
    assert repo.list_by_correlation("corr-1") == sorted(events, key=lambda e: e.event_id)


def test_unknown_correlation_returns_empty(ledger_table: Any) -> None:
    assert LedgerRepository(ledger_table).list_by_correlation("missing") == []


def test_non_duplicate_client_error_is_reraised() -> None:
    error = ClientError(
        {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "slow"}},
        "TransactWriteItems",
    )
    repo = LedgerRepository(_failing_table(error))
    with pytest.raises(ClientError):
        repo.append(_event(EventType.SCAN_STARTED, T0))


def test_cancellation_not_caused_by_guard_is_reraised() -> None:
    error = ClientError(
        {
            "Error": {"Code": "TransactionCanceledException", "Message": "conflict"},
            "CancellationReasons": [{"Code": "TransactionConflict"}, {"Code": "None"}],
        },
        "TransactWriteItems",
    )
    repo = LedgerRepository(_failing_table(error))
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


def test_existing_reads_the_event_a_retry_collided_with(ledger_table: Any) -> None:
    repo = LedgerRepository(ledger_table)
    event = _event(EventType.SCAN_STARTED, T0, idempotency_key="once")
    repo.append(event)

    assert repo.existing(event.event_id) == event


def test_existing_refuses_a_missing_guard(ledger_table: Any) -> None:
    with pytest.raises(InvalidEventError, match="missing"):
        LedgerRepository(ledger_table).existing("absent")


def test_existing_refuses_a_guard_whose_event_was_removed(ledger_table: Any) -> None:
    repo = LedgerRepository(ledger_table)
    event = _event(EventType.SCAN_STARTED, T0, idempotency_key="once")
    repo.append(event)
    item = event.to_item()
    ledger_table.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})

    with pytest.raises(InvalidEventError, match="missing"):
        repo.existing(event.event_id)
