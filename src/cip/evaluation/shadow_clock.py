"""The production shadow clock. A merge does not start it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cip.domain.errors import DuplicateEventError
from cip.domain.events import EventType, LedgerEvent
from cip.persistence.ledger import LedgerRepository

_CORRELATION = "prod-shadow"
_KEY = "prod-shadow-clock"
_STAMP = "%Y-%m-%dT%H:%M:%S.%fZ"


def note_prod_shadow_clock(
    ledger: LedgerRepository,
    *,
    environment: str,
    mode: str,
    trading_enabled: bool,
    completed_at: datetime,
    policy_version: str,
) -> datetime | None:
    """Record the first successful production SHADOW cycle, and never move it.

    Dev does not write the clock. Trading enabled does not write it. A retry
    returns the original timestamp.
    """
    if type(environment) is not str or type(mode) is not str:
        raise ValueError("environment and mode must be strings")
    if type(trading_enabled) is not bool:
        raise ValueError("trading_enabled must be a bool")
    if completed_at.tzinfo is None or completed_at.utcoffset() != timedelta(0):
        raise ValueError("completed_at must be timezone-aware UTC")
    if environment != "prod" or mode != "SHADOW" or trading_enabled:
        return None
    moment = completed_at.astimezone(UTC)
    event = LedgerEvent(
        event_type=EventType.PROD_SHADOW_STARTED,
        correlation_id=_CORRELATION,
        policy_version=policy_version,
        idempotency_key=_KEY,
        created_at=moment,
        payload={"prod_shadow_started_at": moment.strftime(_STAMP)},
    )
    try:
        ledger.append(event)
    except DuplicateEventError:
        stored = ledger.existing(event.event_id)
        return stored.created_at
    return moment
