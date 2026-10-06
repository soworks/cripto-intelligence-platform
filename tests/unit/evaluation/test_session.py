from datetime import UTC, date, datetime

import pytest

from cip.domain.errors import EvaluationError
from cip.evaluation.session import InputPresence, SessionReadiness, session_close


def test_the_close_is_the_next_utc_midnight() -> None:
    assert session_close(date(2026, 10, 5)) == datetime(2026, 10, 6, tzinfo=UTC)


def test_presence_names_the_four_states() -> None:
    assert [item.value for item in InputPresence] == [
        "present",
        "absent",
        "not_produced",
        "lookahead",
    ]


def test_a_boolean_session_is_refused() -> None:
    with pytest.raises(EvaluationError, match="session is a date"):
        session_close(True)  # type: ignore[arg-type]


def test_weights_are_named_without_blocking_the_session() -> None:
    result = SessionReadiness(
        ready=True,
        blocks=(),
        decision_notes=("score_weights_not_frozen",),
        score_weights="absent",
    )
    assert result.ready is True
    assert result.score_weights == "absent"


def test_a_block_is_not_ready() -> None:
    result = SessionReadiness(
        ready=False,
        blocks=("snapshot_not_produced",),
        decision_notes=(),
        score_weights="absent",
    )
    assert result.ready is False


def test_a_ready_session_cannot_carry_a_block() -> None:
    with pytest.raises(EvaluationError, match="a ready session has no blocks"):
        SessionReadiness(
            ready=True,
            blocks=("snapshot_not_produced",),
            decision_notes=(),
            score_weights="absent",
        )
