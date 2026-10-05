"""Position lifecycle. A state change is a record. It is not an order."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from cip.domain.errors import PositionError
from cip.domain.events import EventType, LedgerEvent
from cip.evaluation.decision import Cohort

_SHA = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_POLICY_SHA = re.compile(r"^[0-9a-f]{64}$")
_SYMBOL = re.compile(r"^[A-Z0-9]{1,20}$")
_FIELDS = frozenset(
    {
        "schema_version",
        "position_id",
        "decision_id",
        "symbol",
        "cohort",
        "state",
        "policy_version",
        "git_sha",
        "updated_at",
        "reason",
    }
)
SCHEMA_VERSION: Literal[1] = 1


class PositionState(StrEnum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    ENTRY_PENDING = "ENTRY_PENDING"
    OPEN = "OPEN"
    PARTIAL_EXIT = "PARTIAL_EXIT"
    EXIT_PENDING = "EXIT_PENDING"
    CLOSED = "CLOSED"


_NEXT: dict[PositionState, frozenset[PositionState]] = {
    PositionState.PROPOSED: frozenset({PositionState.APPROVED, PositionState.CLOSED}),
    PositionState.APPROVED: frozenset({PositionState.ENTRY_PENDING, PositionState.CLOSED}),
    PositionState.ENTRY_PENDING: frozenset({PositionState.OPEN, PositionState.CLOSED}),
    PositionState.OPEN: frozenset({PositionState.PARTIAL_EXIT, PositionState.EXIT_PENDING}),
    PositionState.PARTIAL_EXIT: frozenset({PositionState.EXIT_PENDING}),
    PositionState.EXIT_PENDING: frozenset({PositionState.CLOSED}),
    PositionState.CLOSED: frozenset(),
}


def _exact_type(kind: type[object]) -> BeforeValidator:
    def check(value: object) -> object:
        if type(value) is not kind:
            raise ValueError(f"must be a {kind.__name__}")
        return value

    return BeforeValidator(check)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Position(_Strict):
    """One decision's lifecycle. Quantity and order id are not fields."""

    schema_version: Annotated[Literal[1], _exact_type(int)] = SCHEMA_VERSION
    position_id: str
    decision_id: str
    symbol: str
    cohort: Cohort
    state: PositionState
    policy_version: str
    git_sha: str
    updated_at: datetime
    reason: str = Field(min_length=1)

    @field_validator("position_id", "decision_id")
    @classmethod
    def _hex64(cls, value: str) -> str:
        if _HEX64.fullmatch(value) is None:
            raise ValueError("identity must be 64 lowercase hex characters")
        return value

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, value: str) -> str:
        if _SYMBOL.fullmatch(value) is None:
            raise ValueError("symbol must be 1 to 20 uppercase letters or digits")
        return value

    @field_validator("policy_version")
    @classmethod
    def _policy_version(cls, value: str) -> str:
        if _POLICY_SHA.fullmatch(value) is None:
            raise ValueError("policy_version must be 64 lowercase hex characters")
        return value

    @field_validator("git_sha")
    @classmethod
    def _sha(cls, value: str) -> str:
        if _SHA.fullmatch(value) is None:
            raise ValueError("git_sha must be 40 lowercase hex characters")
        return value

    @field_validator("updated_at")
    @classmethod
    def _updated(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("updated_at must be timezone-aware UTC")
        return value

    @model_validator(mode="after")
    def _follows_decision(self) -> Self:
        if self.position_id != position_id(self.decision_id):
            raise ValueError("position_id does not follow the decision")
        return self

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "position_id": self.position_id,
            "decision_id": self.decision_id,
            "symbol": self.symbol,
            "cohort": self.cohort.value,
            "state": self.state.value,
            "policy_version": self.policy_version,
            "git_sha": self.git_sha,
            "updated_at": self.updated_at.isoformat(),
            "reason": self.reason,
        }

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> Self:
        if set(document) != _FIELDS:
            raise ValueError("position document keys are fixed")
        return cls(
            schema_version=document["schema_version"],
            position_id=document["position_id"],
            decision_id=document["decision_id"],
            symbol=document["symbol"],
            cohort=document["cohort"],
            state=document["state"],
            policy_version=document["policy_version"],
            git_sha=document["git_sha"],
            updated_at=_canonical_utc(document["updated_at"]),
            reason=document["reason"],
        )


def position_id(decision_id: str) -> str:
    """Stable id for one decision. This is not an order id."""
    if _HEX64.fullmatch(decision_id) is None:
        raise PositionError("decision_id must be 64 lowercase hex characters")
    return hashlib.sha256(b"cip:position:" + decision_id.encode()).hexdigest()


def propose(
    *,
    decision_id: str,
    symbol: str,
    cohort: Cohort,
    policy_version: str,
    git_sha: str,
    updated_at: datetime,
    reason: str,
) -> Position:
    """Open a lifecycle at PROPOSED. Nothing is sent to an exchange."""
    return Position(
        position_id=position_id(decision_id),
        decision_id=decision_id,
        symbol=symbol,
        cohort=cohort,
        state=PositionState.PROPOSED,
        policy_version=policy_version,
        git_sha=git_sha,
        updated_at=updated_at,
        reason=reason,
    )


def checked(position: Position) -> Position:
    """Rebuild the record so a copied object cannot skip validation."""
    try:
        return Position.model_validate(position.model_dump())
    except ValidationError as error:
        raise PositionError("position is invalid") from error


def advance(
    position: Position, *, to: PositionState, reason: str, updated_at: datetime
) -> Position:
    """Move one step. The caller names the next state. This does not fill."""
    current = checked(position)
    moved = Position.model_validate(
        {**current.model_dump(), "state": to, "reason": reason, "updated_at": updated_at}
    )
    require_transition(current, moved)
    return moved


def require_transition(before: Position | None, after: Position) -> None:
    if before is None:
        if after.state is not PositionState.PROPOSED:
            raise PositionError("a new position starts as PROPOSED")
        return
    if (
        before.position_id != after.position_id
        or before.decision_id != after.decision_id
        or before.symbol != after.symbol
        or before.cohort != after.cohort
    ):
        raise PositionError("a transition cannot retarget a position")
    if after.state not in _NEXT[before.state]:
        raise PositionError(f"a {before.state.value} position cannot move to {after.state.value}")


def transition_event(before: Position | None, after: Position) -> LedgerEvent:
    """One ledger event for this move. The payload has no order id."""
    current = None if before is None else checked(before)
    moved = checked(after)
    require_transition(current, moved)
    return LedgerEvent(
        event_type=EventType.POSITION_TRANSITIONED,
        correlation_id=moved.position_id,
        policy_version=moved.policy_version,
        asset=moved.symbol,
        created_at=moved.updated_at,
        idempotency_key=f"{moved.position_id}:{moved.state.value}",
        payload={
            "position_id": moved.position_id,
            "decision_id": moved.decision_id,
            "from_state": None if current is None else current.state.value,
            "to_state": moved.state.value,
            "reason": moved.reason,
        },
    )


def document_text(position: Position) -> str:
    return json.dumps(checked(position).to_document(), sort_keys=True)


def _canonical_utc(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("updated_at must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("updated_at must be canonical UTC") from error
    if parsed.isoformat() != value:
        raise ValueError("updated_at must be canonical UTC")
    return parsed
