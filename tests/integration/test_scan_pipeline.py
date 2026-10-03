import json
import time
import uuid

import boto3
import pytest

from cip.domain.events import EventType, LedgerEvent
from cip.persistence.ledger import LedgerRepository
from tests.integration.conftest import require_env

pytestmark = pytest.mark.integration


def _wait_for_execution(sfn: object, execution_arn: str, timeout_s: int = 120) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status: str = sfn.describe_execution(executionArn=execution_arn)["status"]  # type: ignore[attr-defined]
        if status != "RUNNING":
            return status
        time.sleep(2)
    pytest.fail(f"execution {execution_arn} still RUNNING after {timeout_s}s")


def _wait_for_events(
    ledger: LedgerRepository, correlation_id: str, count: int
) -> list[LedgerEvent]:
    for _ in range(10):
        events = ledger.list_by_correlation(correlation_id)
        if len(events) >= count:
            return events
        time.sleep(1)
    return ledger.list_by_correlation(correlation_id)


def test_pipeline_records_start_and_completion_in_shadow_mode() -> None:
    sfn = boto3.client("stepfunctions")
    correlation_id = f"it-{uuid.uuid4().hex}"
    execution = sfn.start_execution(
        stateMachineArn=require_env("CIP_STATE_MACHINE_ARN"),
        input=json.dumps({"correlation_id": correlation_id}),
    )

    assert _wait_for_execution(sfn, execution["executionArn"]) == "SUCCEEDED"

    ledger = LedgerRepository(boto3.resource("dynamodb").Table(require_env("CIP_LEDGER_TABLE")))
    events = _wait_for_events(ledger, correlation_id, count=2)
    assert [event.event_type for event in events] == [
        EventType.SCAN_STARTED,
        EventType.SCAN_COMPLETED,
    ]
    assert events[0].payload["execution_mode"] == "SHADOW"
    assert events[0].payload["trading_enabled"] is False
    assert events[0].policy_version == events[1].policy_version
