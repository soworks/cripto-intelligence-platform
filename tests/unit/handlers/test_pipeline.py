from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from cip.config.flags import ExecutionFlags
from cip.domain.events import EventType
from cip.domain.policy import ExecutionMode, LoadedPolicy, load_policy
from cip.handlers import pipeline
from cip.handlers.pipeline import run_complete_scan, run_record_failure, run_start_scan
from cip.persistence.ledger import LedgerRepository

REPO_POLICY = Path(__file__).parents[3] / "policies" / "investment-policy.yaml"
SHADOW = ExecutionFlags(ExecutionMode.SHADOW, trading_enabled=False, kill_switch_active=False)


@pytest.fixture
def policy() -> LoadedPolicy:
    return load_policy(REPO_POLICY)


def test_start_scan_records_flags_and_uses_supplied_correlation_id(
    ledger_table: Any, policy: LoadedPolicy
) -> None:
    ledger = LedgerRepository(ledger_table)
    result = run_start_scan({"correlation_id": "corr-9"}, ledger, SHADOW, policy)

    assert result == {"correlation_id": "corr-9", "policy_version": policy.version}
    [event] = ledger.list_by_correlation("corr-9")
    assert event.event_type is EventType.SCAN_STARTED
    assert event.policy_version == policy.version
    assert event.payload == {
        "execution_mode": "SHADOW",
        "trading_enabled": False,
        "kill_switch_active": False,
        "policy_mode": "SHADOW",
    }


def test_start_scan_generates_correlation_id_when_absent(
    ledger_table: Any, policy: LoadedPolicy
) -> None:
    result = run_start_scan({}, LedgerRepository(ledger_table), SHADOW, policy)
    assert len(result["correlation_id"]) == 32


def test_complete_scan_records_completion(ledger_table: Any) -> None:
    ledger = LedgerRepository(ledger_table)
    result = run_complete_scan({"correlation_id": "corr-9", "policy_version": "v1"}, ledger)

    assert result == {"correlation_id": "corr-9", "status": "COMPLETED"}
    [event] = ledger.list_by_correlation("corr-9")
    assert event.event_type is EventType.SCAN_COMPLETED


def test_record_failure_truncates_cause(ledger_table: Any) -> None:
    ledger = LedgerRepository(ledger_table)
    request = {
        "correlation_id": "corr-9",
        "error": {"Error": "Lambda.Unknown", "Cause": "x" * 5000},
    }
    result = run_record_failure(request, ledger)

    assert result == {"correlation_id": "corr-9", "status": "FAILED"}
    [event] = ledger.list_by_correlation("corr-9")
    assert event.event_type is EventType.PIPELINE_FAILED
    assert event.policy_version == "unknown"
    assert event.payload["error"] == "Lambda.Unknown"
    assert len(event.payload["cause"]) == 1000


@dataclass(frozen=True)
class FakeLambdaContext:
    function_name: str = "cip-test-pipeline"
    memory_limit_in_mb: int = 256
    invoked_function_arn: str = "arn:aws:lambda:us-east-1:123456789012:function:cip-test-pipeline"
    aws_request_id: str = "req-1"


@pytest.fixture
def lambda_env(ledger_table: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    monkeypatch.setenv("LEDGER_TABLE", ledger_table.name)
    monkeypatch.setenv("FLAGS_PREFIX", "/cip/test/flags")
    monkeypatch.setenv("POLICY_PATH", str(REPO_POLICY))
    pipeline._ledger.cache_clear()
    pipeline._policy.cache_clear()
    yield ledger_table
    pipeline._ledger.cache_clear()
    pipeline._policy.cache_clear()


def test_lambda_entry_points_wire_environment(lambda_env: Any) -> None:
    context = FakeLambdaContext()

    started = pipeline.start_scan({"correlation_id": "corr-env"}, context)
    completed = pipeline.complete_scan(started, context)
    failed = pipeline.record_failure({**started, "error": {"Error": "E"}}, context)

    assert completed == {"correlation_id": "corr-env", "status": "COMPLETED"}
    assert failed == {"correlation_id": "corr-env", "status": "FAILED"}
    events = LedgerRepository(lambda_env).list_by_correlation("corr-env")
    assert [event.event_type for event in events] == [
        EventType.SCAN_STARTED,
        EventType.SCAN_COMPLETED,
        EventType.PIPELINE_FAILED,
    ]
    assert events[0].payload["kill_switch_active"] is True
