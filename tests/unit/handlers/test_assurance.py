from dataclasses import dataclass

import pytest

from cip.domain.errors import ScorecardError
from cip.handlers.assurance import run_weekly_assurance, weekly


@dataclass(frozen=True)
class FakeLambdaContext:
    function_name: str = "cip-test-assurance"
    memory_limit_in_mb: int = 256
    invoked_function_arn: str = "arn:aws:lambda:us-east-1:123456789012:function:cip-test-assurance"
    aws_request_id: str = "req-1"


def test_a_schedule_with_no_stored_inputs_writes_nothing() -> None:
    assert run_weekly_assurance({"trigger": "schedule"}) == {"status": "no_stored_inputs"}
    assert weekly({"trigger": "schedule"}, FakeLambdaContext()) == {"status": "no_stored_inputs"}


def test_a_schedule_cannot_invent_a_report() -> None:
    with pytest.raises(ScorecardError, match="not invented"):
        run_weekly_assurance({"trigger": "manual"})
    with pytest.raises(ScorecardError, match="not invented"):
        run_weekly_assurance({})
