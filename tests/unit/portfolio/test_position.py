import json
import warnings
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws
from pydantic import ValidationError

from cip.domain.errors import DuplicateEventError, PositionError
from cip.domain.events import EventType
from cip.evaluation.decision import Cohort
from cip.persistence.ledger import LedgerRepository
from cip.persistence.positions import PositionStore
from cip.portfolio.position import (
    Position,
    PositionState,
    advance,
    position_id,
    propose,
    require_transition,
    transition_event,
)

DECISION = "ab" * 32
POLICY = "cd" * 32
SHA = "a" * 40
WHEN = datetime(2026, 10, 5, 12, tzinfo=UTC)


def _propose(**overrides: object) -> Position:
    values: dict[str, object] = {
        "decision_id": DECISION,
        "symbol": "BTCUSDT",
        "cohort": Cohort.SHADOW,
        "policy_version": POLICY,
        "git_sha": SHA,
        "updated_at": WHEN,
        "reason": "owner proposal",
    }
    values.update(overrides)
    return propose(**values)  # type: ignore[arg-type]


def _walk(position: Position, states: tuple[PositionState, ...]) -> Position:
    current = position
    for index, state in enumerate(states, start=1):
        current = advance(
            current,
            to=state,
            reason=state.value,
            updated_at=WHEN + timedelta(minutes=index),
        )
    return current


@pytest.fixture
def store() -> Any:
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        state = dynamodb.create_table(
            TableName="cip-test-state",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
        )
        ledger = dynamodb.create_table(
            TableName="cip-test-ledger",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": name, "AttributeType": "S"}
                for name in ("PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK")
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": index,
                    "KeySchema": [
                        {"AttributeName": f"{index}PK", "KeyType": "HASH"},
                        {"AttributeName": f"{index}SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
                for index in ("GSI1", "GSI2")
            ],
        )
        yield PositionStore(state, ledger), state, ledger


def test_a_proposal_is_not_an_order() -> None:
    position = _propose()
    assert position.state is PositionState.PROPOSED
    assert position.position_id == position_id(DECISION)
    assert position.position_id != DECISION
    document = position.to_document()
    assert "order_id" not in document
    assert "quantity" not in document
    assert Position.from_document(document) == position
    event = transition_event(None, position)
    assert event.event_type is EventType.POSITION_TRANSITIONED
    assert event.payload["from_state"] is None
    assert event.payload["to_state"] == "PROPOSED"
    assert "order_id" not in event.payload
    with pytest.raises(ValidationError):
        Position.model_validate({**position.model_dump(), "order_id": "1"})


def test_the_named_path_reaches_closed_and_a_partial_exit_is_optional() -> None:
    opened = _walk(
        _propose(),
        (PositionState.APPROVED, PositionState.ENTRY_PENDING, PositionState.OPEN),
    )
    closed = _walk(opened, (PositionState.EXIT_PENDING, PositionState.CLOSED))
    assert closed.state is PositionState.CLOSED
    partial = _walk(
        opened,
        (PositionState.PARTIAL_EXIT, PositionState.EXIT_PENDING, PositionState.CLOSED),
    )
    assert partial.state is PositionState.CLOSED
    with pytest.raises(PositionError, match="cannot move"):
        advance(opened, to=PositionState.CLOSED, reason="skip", updated_at=WHEN)
    with pytest.raises(PositionError, match="cannot move"):
        advance(_propose(), to=PositionState.OPEN, reason="jump", updated_at=WHEN)
    with pytest.raises(PositionError, match="cannot move"):
        advance(closed, to=PositionState.OPEN, reason="again", updated_at=WHEN)
    partial_only = advance(opened, to=PositionState.PARTIAL_EXIT, reason="partial", updated_at=WHEN)
    with pytest.raises(PositionError, match="cannot move"):
        advance(partial_only, to=PositionState.CLOSED, reason="skip", updated_at=WHEN)


def test_a_proposal_that_is_not_taken_can_close_without_opening() -> None:
    closed = advance(_propose(), to=PositionState.CLOSED, reason="not taken", updated_at=WHEN)
    assert closed.state is PositionState.CLOSED
    approved = advance(_propose(), to=PositionState.APPROVED, reason="approved", updated_at=WHEN)
    assert advance(approved, to=PositionState.CLOSED, reason="withdrawn", updated_at=WHEN).state
    pending = advance(approved, to=PositionState.ENTRY_PENDING, reason="waiting", updated_at=WHEN)
    assert advance(pending, to=PositionState.CLOSED, reason="no entry", updated_at=WHEN).state


def test_a_transition_cannot_retarget_or_start_open() -> None:
    position = _propose()
    retargeted = Position.model_validate({**position.model_dump(), "symbol": "ETHUSDT"})
    with pytest.raises(PositionError, match="retarget"):
        require_transition(position, retargeted)
    opened = _walk(
        position,
        (PositionState.APPROVED, PositionState.ENTRY_PENDING, PositionState.OPEN),
    )
    with pytest.raises(PositionError, match="starts as PROPOSED"):
        require_transition(None, opened)
    with pytest.raises(PositionError, match="64 lowercase hex"):
        position_id("not-a-decision")
    with pytest.raises(PositionError, match="retarget"):
        require_transition(position, position.model_copy(update={"position_id": "a" * 64}))
    with pytest.raises(PositionError, match="retarget"):
        require_transition(position, position.model_copy(update={"decision_id": "ef" * 32}))
    with pytest.raises(PositionError, match="retarget"):
        require_transition(position, position.model_copy(update={"cohort": Cohort.BACKTEST}))
    with pytest.raises(ValidationError):
        advance(position, to=PositionState.APPROVED, reason="", updated_at=WHEN)
    with pytest.raises(ValidationError):
        _propose(symbol="btc")
    with pytest.raises(ValidationError):
        _propose(policy_version="G" * 64)
    with pytest.raises(ValidationError):
        _propose(git_sha="nope")
    with pytest.raises(ValidationError):
        Position.model_validate({**position.model_dump(), "decision_id": "zz" * 32})
    with pytest.raises(ValidationError):
        _propose(updated_at=datetime(2026, 10, 5, 12))  # noqa: DTZ001
    with pytest.raises(ValidationError):
        _propose(updated_at=datetime(2026, 10, 5, 12, tzinfo=timezone(timedelta(hours=-5))))


def test_a_bad_position_document_is_refused() -> None:
    document = _propose().to_document()
    document["order_id"] = "1"
    with pytest.raises(ValueError, match="keys are fixed"):
        Position.from_document(document)
    missing = _propose().to_document()
    del missing["reason"]
    with pytest.raises(ValueError, match="keys are fixed"):
        Position.from_document(missing)
    flagged = _propose().to_document()
    flagged["schema_version"] = True
    with pytest.raises(ValidationError):
        Position.from_document(flagged)
    shifted = _propose().to_document()
    shifted["updated_at"] = "2026-10-05T07:00:00-05:00"
    with pytest.raises(ValidationError, match="UTC"):
        Position.from_document(shifted)
    zulu = _propose().to_document()
    zulu["updated_at"] = "2026-10-05T12:00:00Z"
    with pytest.raises(ValueError, match="canonical UTC"):
        Position.from_document(zulu)
    broken = _propose().to_document()
    broken["updated_at"] = "not-a-time"
    with pytest.raises(ValueError, match="canonical UTC"):
        Position.from_document(broken)
    numbered = _propose().to_document()
    numbered["updated_at"] = 1
    with pytest.raises(ValueError, match="canonical UTC"):
        Position.from_document(numbered)
    unbound = _propose().to_document()
    unbound["position_id"] = "a" * 64
    with pytest.raises(ValidationError, match="does not follow"):
        Position.from_document(unbound)


def test_each_move_is_one_ledger_event_and_a_replay_does_not_rewrite_it(store: Any) -> None:
    positions, _state, ledger = store
    proposed = _propose()
    positions.commit(None, proposed)
    assert positions.read(proposed.position_id) == proposed
    approved = advance(
        proposed,
        to=PositionState.APPROVED,
        reason="approved",
        updated_at=WHEN + timedelta(minutes=1),
    )
    positions.commit(proposed, approved)
    events = LedgerRepository(ledger).list_by_correlation(proposed.position_id)
    assert [event.payload["to_state"] for event in events] == ["PROPOSED", "APPROVED"]
    assert all(event.event_type is EventType.POSITION_TRANSITIONED for event in events)
    with pytest.raises(DuplicateEventError):
        positions.commit(None, proposed)
    assert positions.read(proposed.position_id) == approved


def test_an_unvalidated_copy_does_not_change_the_stored_position(store: Any) -> None:
    positions, state, ledger = store
    proposed = _propose()
    forged = proposed.model_copy(
        update={"reason": {"order_id": "1", "quantity": "5", "notional": "10"}}
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with pytest.raises(PositionError, match="invalid"):
            positions.commit(None, forged)
    mismatched = proposed.model_copy(update={"decision_id": "ef" * 32})
    with pytest.raises(PositionError, match="invalid"):
        positions.commit(None, mismatched)
    assert state.scan(Select="COUNT")["Count"] == 0
    assert ledger.scan(Select="COUNT")["Count"] == 0
    positions.commit(None, proposed)
    copied = proposed.model_copy(update={"symbol": "ETHUSDT"})
    moved = advance(
        copied,
        to=PositionState.APPROVED,
        reason="approved",
        updated_at=WHEN + timedelta(minutes=1),
    )
    recorded = ledger.scan(Select="COUNT")["Count"]
    with pytest.raises(PositionError, match="no longer"):
        positions.commit(copied, moved)
    assert positions.read(proposed.position_id) == proposed
    assert ledger.scan(Select="COUNT")["Count"] == recorded
    other = _propose(decision_id="ef" * 32)
    state.put_item(
        Item={
            "PK": f"POSITION#{other.position_id}",
            "SK": "STATE",
            "state": other.state.value,
            "document": json.dumps(other.to_document(), sort_keys=True),
        }
    )
    with pytest.raises(PositionError, match="already exists"):
        positions.commit(None, other)
    assert LedgerRepository(ledger).list_by_correlation(other.position_id) == []


def test_a_conflicting_state_is_refused_and_a_broken_row_is_unusable(store: Any) -> None:
    positions, state, _ledger = store
    proposed = _propose()
    positions.commit(None, proposed)
    approved = advance(proposed, to=PositionState.APPROVED, reason="approved", updated_at=WHEN)
    state.put_item(
        Item={
            "PK": f"POSITION#{proposed.position_id}",
            "SK": "STATE",
            "state": "CLOSED",
            "document": json.dumps(proposed.to_document(), sort_keys=True),
        }
    )
    with pytest.raises(PositionError, match="no longer"):
        positions.commit(proposed, approved)
    assert _ledger.scan(Select="COUNT")["Count"] == 2
    with pytest.raises(PositionError, match="missing"):
        positions.read("c" * 64)
    state.put_item(
        Item={
            "PK": f"POSITION#{proposed.position_id}",
            "SK": "STATE",
            "state": "PROPOSED",
            "document": "{",
        }
    )
    with pytest.raises(PositionError, match="unusable"):
        positions.read(proposed.position_id)
    state.put_item(
        Item={
            "PK": f"POSITION#{proposed.position_id}",
            "SK": "STATE",
            "state": "PROPOSED",
            "document": 1,
        }
    )
    with pytest.raises(PositionError, match="unusable"):
        positions.read(proposed.position_id)
    other = _propose(decision_id="ef" * 32)
    state.put_item(
        Item={
            "PK": f"POSITION#{proposed.position_id}",
            "SK": "STATE",
            "state": other.state.value,
            "document": json.dumps(other.to_document(), sort_keys=True),
        }
    )
    with pytest.raises(PositionError, match="unusable"):
        positions.read(proposed.position_id)


def test_a_store_error_that_is_not_a_conflict_is_reraised() -> None:
    error = ClientError(
        {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "slow"}},
        "TransactWriteItems",
    )

    class Broken:
        name = "cip-test"

        def __init__(self) -> None:
            self.meta = type("Meta", (), {})()
            self.meta.client = type("Client", (), {})()
            self.meta.client.transact_write_items = lambda **_: (_ for _ in ()).throw(error)

    store = PositionStore(Broken(), Broken())  # type: ignore[arg-type]
    with pytest.raises(ClientError):
        store.commit(None, _propose())
    cancelled = ClientError(
        {
            "Error": {"Code": "TransactionCanceledException", "Message": "conflict"},
            "CancellationReasons": [{"Code": "None"}, {"Code": "None"}, {"Code": "None"}],
        },
        "TransactWriteItems",
    )
    broken = Broken()
    broken.meta.client.transact_write_items = lambda **_: (_ for _ in ()).throw(cancelled)
    with pytest.raises(ClientError):
        PositionStore(broken, broken).commit(None, _propose())  # type: ignore[arg-type]
