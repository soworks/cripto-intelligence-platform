from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from cip.domain.events import EventType
from cip.evaluation.shadow_clock import note_prod_shadow_clock
from cip.persistence.ledger import LedgerRepository

FIRST = datetime(2026, 10, 6, 1, 0, tzinfo=UTC)
LATER = datetime(2026, 10, 6, 2, 0, tzinfo=UTC)
POLICY = "a" * 64


def _note(ledger: LedgerRepository, **overrides: object) -> datetime | None:
    values: dict[str, object] = {
        "environment": "prod",
        "mode": "SHADOW",
        "trading_enabled": False,
        "completed_at": FIRST,
        "policy_version": POLICY,
    }
    values.update(overrides)
    return note_prod_shadow_clock(ledger, **values)  # type: ignore[arg-type]


def test_the_first_prod_shadow_cycle_is_the_only_clock(ledger_table: Any) -> None:
    ledger = LedgerRepository(ledger_table)
    assert _note(ledger) == FIRST
    assert _note(ledger, completed_at=LATER) == FIRST

    [event] = ledger.list_by_correlation("prod-shadow")
    assert event.event_type is EventType.PROD_SHADOW_STARTED
    assert event.payload == {"prod_shadow_started_at": event.timestamp}
    assert event.created_at == FIRST


def test_dev_live_mode_and_trading_do_not_start_the_clock(ledger_table: Any) -> None:
    ledger = LedgerRepository(ledger_table)
    assert _note(ledger, environment="dev") is None
    assert _note(ledger, mode="LIVE") is None
    assert _note(ledger, trading_enabled=True) is None
    assert ledger.list_by_correlation("prod-shadow") == []


def test_the_clock_refuses_a_non_string_environment_or_mode(ledger_table: Any) -> None:
    ledger = LedgerRepository(ledger_table)
    with pytest.raises(ValueError, match="strings"):
        _note(ledger, environment=1)
    with pytest.raises(ValueError, match="strings"):
        _note(ledger, mode=1)


def test_the_clock_refuses_a_non_bool_trading_flag(ledger_table: Any) -> None:
    with pytest.raises(ValueError, match="bool"):
        _note(LedgerRepository(ledger_table), trading_enabled=0)


def test_the_clock_refuses_a_naive_or_offset_timestamp(ledger_table: Any) -> None:
    ledger = LedgerRepository(ledger_table)
    with pytest.raises(ValueError, match="UTC"):
        _note(ledger, completed_at=datetime(2026, 10, 6))  # noqa: DTZ001
    with pytest.raises(ValueError, match="UTC"):
        _note(ledger, completed_at=datetime(2026, 10, 6, tzinfo=timezone(timedelta(hours=1))))
