from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
import pytest

from cip.config.flags import ExecutionFlags
from cip.domain.events import EventType
from cip.domain.policy import ExecutionMode, LoadedPolicy, load_policy
from cip.handlers import pipeline
from cip.handlers.pipeline import run_complete_scan, run_record_failure, run_start_scan
from cip.persistence.ledger import LedgerRepository

REPO_POLICY = Path(__file__).parents[3] / "policies" / "investment-policy.yaml"
SHADOW = ExecutionFlags(ExecutionMode.SHADOW, trading_enabled=False, kill_switch_active=False)
FLAGS_PREFIX = "/cip/test/flags"


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


def test_start_scan_retry_is_a_successful_replay(ledger_table: Any, policy: LoadedPolicy) -> None:
    ledger = LedgerRepository(ledger_table)
    first = run_start_scan({"correlation_id": "corr-9"}, ledger, SHADOW, policy)
    retry = run_start_scan({"correlation_id": "corr-9"}, ledger, SHADOW, policy)

    assert retry == first
    assert len(ledger.list_by_correlation("corr-9")) == 1


def test_complete_scan_retry_is_a_successful_replay(ledger_table: Any) -> None:
    ledger = LedgerRepository(ledger_table)
    request = {"correlation_id": "corr-9", "policy_version": "v1"}
    first = run_complete_scan(request, ledger)
    retry = run_complete_scan(request, ledger)

    assert retry == first
    assert len(ledger.list_by_correlation("corr-9")) == 1


def test_record_failure_retry_is_a_successful_replay(ledger_table: Any) -> None:
    ledger = LedgerRepository(ledger_table)
    request = {"correlation_id": "corr-9", "error": {"Error": "E"}}
    first = run_record_failure(request, ledger)
    retry = run_record_failure(request, ledger)

    assert retry == first
    assert len(ledger.list_by_correlation("corr-9")) == 1


def test_failures_without_correlation_id_are_all_recorded(ledger_table: Any) -> None:
    ledger = LedgerRepository(ledger_table)
    run_record_failure({"error": {"Error": "A"}}, ledger)
    run_record_failure({"error": {"Error": "B"}}, ledger)

    assert len(ledger.list_by_correlation("unknown")) == 2


@dataclass(frozen=True)
class FakeLambdaContext:
    function_name: str = "cip-test-pipeline"
    memory_limit_in_mb: int = 256
    invoked_function_arn: str = "arn:aws:lambda:us-east-1:123456789012:function:cip-test-pipeline"
    aws_request_id: str = "req-1"


@pytest.fixture
def lambda_env(ledger_table: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    monkeypatch.setenv("LEDGER_TABLE", ledger_table.name)
    monkeypatch.setenv("FLAGS_PREFIX", FLAGS_PREFIX)
    monkeypatch.setenv("POLICY_PATH", str(REPO_POLICY))
    _clear_caches()
    yield ledger_table
    _clear_caches()


def _clear_caches() -> None:
    pipeline._ledger.cache_clear()
    pipeline._policy.cache_clear()
    pipeline._ssm.cache_clear()


def _put_flags(kill: str) -> None:
    ssm = boto3.client("ssm", region_name="us-east-1")
    for name, value in (("execution_mode", "SHADOW"), ("trading_enabled", "false")):
        ssm.put_parameter(Name=f"{FLAGS_PREFIX}/{name}", Value=value, Type="String", Overwrite=True)
    ssm.put_parameter(Name=f"{FLAGS_PREFIX}/kill_switch", Value=kill, Type="String", Overwrite=True)


def test_flags_are_reread_every_invocation_through_a_cached_client(lambda_env: Any) -> None:
    context = FakeLambdaContext()
    _put_flags(kill="false")
    pipeline.start_scan({"correlation_id": "corr-a"}, context)
    _put_flags(kill="true")
    pipeline.start_scan({"correlation_id": "corr-b"}, context)

    ledger = LedgerRepository(lambda_env)
    assert ledger.list_by_correlation("corr-a")[0].payload["kill_switch_active"] is False
    assert ledger.list_by_correlation("corr-b")[0].payload["kill_switch_active"] is True
    assert pipeline._ssm() is pipeline._ssm()


def test_lambda_retry_returns_the_original_result(lambda_env: Any) -> None:
    context = FakeLambdaContext()
    first = pipeline.start_scan({"correlation_id": "corr-retry"}, context)
    retry = pipeline.start_scan({"correlation_id": "corr-retry"}, context)

    assert retry == first
    assert len(LedgerRepository(lambda_env).list_by_correlation("corr-retry")) == 1


def test_lambda_entry_points_wire_environment(lambda_env: Any) -> None:
    context = FakeLambdaContext()

    started = pipeline.start_scan({"correlation_id": "corr-env"}, context)
    completed = pipeline.complete_scan(started, context)
    failed = pipeline.record_failure({**started, "error": {"Error": "E"}}, context)

    assert completed == {"correlation_id": "corr-env", "status": "COMPLETED"}
    assert failed == {"correlation_id": "corr-env", "status": "FAILED"}
    ledger = LedgerRepository(lambda_env)
    events = ledger.list_by_correlation("corr-env")
    assert [event.event_type for event in events] == [
        EventType.SCAN_STARTED,
        EventType.SCAN_COMPLETED,
        EventType.PIPELINE_FAILED,
    ]
    assert events[0].payload["kill_switch_active"] is True
    assert ledger.list_by_correlation("prod-shadow") == []


def test_a_prod_shadow_completion_records_the_clock_once(
    lambda_env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CIP_ENV", "prod")
    _put_flags(kill="false")
    context = FakeLambdaContext()
    started = pipeline.start_scan({"correlation_id": "corr-prod"}, context)

    pipeline.complete_scan(started, context)
    pipeline.complete_scan(started, context)

    ledger = LedgerRepository(lambda_env)
    [clock] = ledger.list_by_correlation("prod-shadow")
    assert clock.event_type is EventType.PROD_SHADOW_STARTED
    assert clock.payload["prod_shadow_started_at"] == clock.timestamp
    assert clock.idempotency_key == "prod-shadow-clock"


def test_an_unread_prod_flag_set_does_not_start_the_clock(
    lambda_env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CIP_ENV", "prod")
    context = FakeLambdaContext()
    started = pipeline.start_scan({"correlation_id": "corr-unread"}, context)

    pipeline.complete_scan(started, context)

    assert LedgerRepository(lambda_env).list_by_correlation("prod-shadow") == []
