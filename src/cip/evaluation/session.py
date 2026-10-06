"""Closed-session readiness. A missing input stays missing."""

from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum

from cip.domain.errors import EvaluationError


class InputPresence(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"
    NOT_PRODUCED = "not_produced"
    LOOKAHEAD = "lookahead"


def session_close(session: date) -> datetime:
    """00:00 UTC on the day after the bar open date."""
    if type(session) is not date:
        raise EvaluationError("session is a date")
    return datetime.combine(session + timedelta(days=1), time.min, tzinfo=UTC)
