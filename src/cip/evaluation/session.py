"""Closed-session readiness. A missing input stays missing."""

from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

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


class SessionReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ready: bool
    blocks: tuple[str, ...]
    decision_notes: tuple[str, ...]
    score_weights: Literal["absent", "present"]

    @model_validator(mode="after")
    def _ready_has_no_blocks(self) -> "SessionReadiness":
        if any(item == "" for item in self.blocks + self.decision_notes):
            raise EvaluationError("readiness reasons are non-empty")
        if self.ready and self.blocks:
            raise EvaluationError("a ready session has no blocks")
        return self
