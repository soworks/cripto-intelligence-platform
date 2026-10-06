"""Weekly assurance entry point. A schedule with no stored inputs writes nothing."""

from __future__ import annotations

from typing import Any

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext

from cip.domain.errors import ScorecardError

logger = Logger(service="cip-assurance")


def run_weekly_assurance(event: dict[str, Any]) -> dict[str, str]:
    """The schedule carries no figures. Missing figures are not a zero report."""
    if set(event) == {"trigger"} and event["trigger"] == "schedule":
        logger.info("weekly assurance has no stored inputs")
        return {"status": "no_stored_inputs"}
    raise ScorecardError("a weekly report is not invented from a schedule")


@logger.inject_lambda_context
def weekly(event: dict[str, Any], context: LambdaContext) -> dict[str, str]:
    return run_weekly_assurance(event)
