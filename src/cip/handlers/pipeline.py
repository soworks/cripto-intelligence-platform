from __future__ import annotations

import os
import uuid
from functools import cache
from pathlib import Path
from typing import Any

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext

from cip.config.flags import ExecutionFlags, read_execution_flags
from cip.domain.events import EventType, LedgerEvent
from cip.domain.policy import LoadedPolicy, load_policy
from cip.persistence.ledger import LedgerRepository

logger = Logger(service="cip-pipeline")

_MAX_CAUSE_CHARS = 1000


def run_start_scan(
    request: dict[str, Any],
    ledger: LedgerRepository,
    flags: ExecutionFlags,
    policy: LoadedPolicy,
) -> dict[str, str]:
    correlation_id = str(request.get("correlation_id") or uuid.uuid4().hex)
    logger.append_keys(correlation_id=correlation_id)
    ledger.append(
        LedgerEvent(
            event_type=EventType.SCAN_STARTED,
            correlation_id=correlation_id,
            policy_version=policy.version,
            payload={
                "execution_mode": str(flags.mode),
                "trading_enabled": flags.trading_enabled,
                "kill_switch_active": flags.kill_switch_active,
                "policy_mode": str(policy.policy.execution.mode),
            },
        )
    )
    logger.info("scan started")
    return {"correlation_id": correlation_id, "policy_version": policy.version}


def run_complete_scan(request: dict[str, Any], ledger: LedgerRepository) -> dict[str, str]:
    correlation_id = str(request["correlation_id"])
    logger.append_keys(correlation_id=correlation_id)
    ledger.append(
        LedgerEvent(
            event_type=EventType.SCAN_COMPLETED,
            correlation_id=correlation_id,
            policy_version=str(request["policy_version"]),
        )
    )
    logger.info("scan completed")
    return {"correlation_id": correlation_id, "status": "COMPLETED"}


def run_record_failure(request: dict[str, Any], ledger: LedgerRepository) -> dict[str, str]:
    correlation_id = str(request.get("correlation_id") or "unknown")
    error = request.get("error") or {}
    logger.append_keys(correlation_id=correlation_id)
    ledger.append(
        LedgerEvent(
            event_type=EventType.PIPELINE_FAILED,
            correlation_id=correlation_id,
            policy_version=str(request.get("policy_version") or "unknown"),
            payload={
                "error": str(error.get("Error", "")),
                "cause": str(error.get("Cause", ""))[:_MAX_CAUSE_CHARS],
            },
        )
    )
    logger.error("scan pipeline failed")
    return {"correlation_id": correlation_id, "status": "FAILED"}


@cache
def _ledger() -> LedgerRepository:
    return LedgerRepository(boto3.resource("dynamodb").Table(os.environ["LEDGER_TABLE"]))


@cache
def _policy() -> LoadedPolicy:
    return load_policy(Path(os.environ["POLICY_PATH"]))


def _flags() -> ExecutionFlags:
    return read_execution_flags(boto3.client("ssm"), os.environ["FLAGS_PREFIX"])


@logger.inject_lambda_context
def start_scan(event: dict[str, Any], context: LambdaContext) -> dict[str, str]:
    return run_start_scan(event, _ledger(), _flags(), _policy())


@logger.inject_lambda_context
def complete_scan(event: dict[str, Any], context: LambdaContext) -> dict[str, str]:
    return run_complete_scan(event, _ledger())


@logger.inject_lambda_context
def record_failure(event: dict[str, Any], context: LambdaContext) -> dict[str, str]:
    return run_record_failure(event, _ledger())
