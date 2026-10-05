from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from cip.domain.errors import InvalidEventError

GLOBAL_ASSET = "GLOBAL"
_TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
_EVENT_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "urn:cip:ledger-event")


class EventType(StrEnum):
    SCAN_STARTED = "SCAN_STARTED"
    SCAN_COMPLETED = "SCAN_COMPLETED"
    DECISION_RECORDED = "DECISION_RECORDED"
    PIPELINE_FAILED = "PIPELINE_FAILED"
    POSITION_TRANSITIONED = "POSITION_TRANSITIONED"


def _payload_json(payload: Mapping[str, Any]) -> str:
    try:
        return json.dumps(payload, allow_nan=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise InvalidEventError(
            f"ledger payload is not storable as strict JSON: {error}"
        ) from error


def _derive_event_id(data: dict[str, Any]) -> str:
    identity = [
        data["correlation_id"],
        str(data["event_type"]),
        data["asset"],
        data["idempotency_key"],
    ]
    return uuid.uuid5(_EVENT_ID_NAMESPACE, json.dumps(identity)).hex


class LedgerEvent(BaseModel):
    """Append-only ledger event.

    ``event_id`` is derived from the logical identity (correlation, type, asset and optional
    ``idempotency_key``), never from the clock, so a retried write maps to the same event.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_type: EventType
    correlation_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    asset: str = GLOBAL_ASSET
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    idempotency_key: str | None = Field(default=None, min_length=1)
    event_id: str = Field(default_factory=_derive_event_id)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("created_at")
    @classmethod
    def _must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("created_at must be timezone-aware UTC")
        return value.astimezone(UTC)

    @field_validator("payload")
    @classmethod
    def _must_round_trip(cls, value: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = json.loads(_payload_json(value))
        return normalized

    @property
    def timestamp(self) -> str:
        return self.created_at.strftime(_TS_FORMAT)

    def to_item(self) -> dict[str, Any]:
        ts = self.timestamp
        return {
            "PK": f"ASSET#{self.asset}",
            "SK": f"EVENT#{ts}#{self.event_id}",
            "GSI1PK": f"CORR#{self.correlation_id}",
            "GSI1SK": f"{ts}#{self.event_id}",
            "GSI2PK": f"TYPE#{self.event_type}#{ts[:10]}",
            "GSI2SK": f"{ts}#{self.event_id}",
            "event_type": str(self.event_type),
            "correlation_id": self.correlation_id,
            "policy_version": self.policy_version,
            "asset": self.asset,
            "created_at": ts,
            "idempotency_key": self.idempotency_key,
            "event_id": self.event_id,
            "payload": _payload_json(self.payload),
        }

    @classmethod
    def from_item(cls, item: Mapping[str, Any]) -> LedgerEvent:
        return cls(
            event_type=EventType(item["event_type"]),
            correlation_id=item["correlation_id"],
            policy_version=item["policy_version"],
            asset=item["asset"],
            created_at=datetime.strptime(item["created_at"], _TS_FORMAT).replace(tzinfo=UTC),
            idempotency_key=item.get("idempotency_key"),
            event_id=item["event_id"],
            payload=json.loads(item["payload"]),
        )
