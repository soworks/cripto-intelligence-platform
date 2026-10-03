from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

GLOBAL_ASSET = "GLOBAL"
_TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


class EventType(StrEnum):
    SCAN_STARTED = "SCAN_STARTED"
    SCAN_COMPLETED = "SCAN_COMPLETED"
    PIPELINE_FAILED = "PIPELINE_FAILED"


def _from_dynamo(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: _from_dynamo(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_from_dynamo(item) for item in value]
    return value


class LedgerEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_type: EventType
    correlation_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    asset: str = GLOBAL_ASSET
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("created_at")
    @classmethod
    def _must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("created_at must be timezone-aware UTC")
        return value.astimezone(UTC)

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
            "event_id": self.event_id,
            "payload": json.loads(json.dumps(self.payload), parse_float=Decimal),
        }

    @classmethod
    def from_item(cls, item: Mapping[str, Any]) -> LedgerEvent:
        return cls(
            event_type=EventType(item["event_type"]),
            correlation_id=item["correlation_id"],
            policy_version=item["policy_version"],
            asset=item["asset"],
            created_at=datetime.strptime(item["created_at"], _TS_FORMAT).replace(tzinfo=UTC),
            event_id=item["event_id"],
            payload=_from_dynamo(item["payload"]),
        )
