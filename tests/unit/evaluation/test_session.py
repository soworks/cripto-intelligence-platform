from datetime import UTC, date, datetime

import pytest

from cip.domain.errors import EvaluationError
from cip.evaluation.session import InputPresence, session_close


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
