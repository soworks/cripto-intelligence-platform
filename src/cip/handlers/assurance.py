"""Weekly assurance entry point. A report is written only from stored records."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext
from botocore.exceptions import ClientError

from cip.domain.errors import ScorecardError
from cip.evaluation.evidence import previous_sunday, scorecard_from_evidence
from cip.evaluation.weekly import weekly_scorecard
from cip.recorders.store import S3Store

logger = Logger(service="cip-assurance")

_DECISIONS = "decisions/"
_OUTCOMES = "decision-outcomes/"
_TRADES = "shadow-trades/"
_PORTFOLIO = "assurance/portfolio.json"


def run_weekly_assurance(
    event: dict[str, Any],
    *,
    decisions: tuple[tuple[str, bytes], ...],
    outcomes: tuple[bytes, ...],
    trades: tuple[bytes, ...],
    portfolio: bytes | None,
    as_of: datetime,
) -> dict[str, str]:
    """Publish the four-dimension week. An unread store does not reach this function."""
    if set(event) != {"trigger"} or event.get("trigger") != "schedule":
        raise ScorecardError("a weekly report is not invented from a schedule")
    card = scorecard_from_evidence(
        decisions=decisions, outcomes=outcomes, trades=trades, portfolio=portfolio
    )
    week = previous_sunday(as_of)
    document = weekly_scorecard(week_ending=week, scorecard=card)
    return {
        "status": "published",
        "week_ending": week.isoformat(),
        "decisions": str(card.decision_integrity.decisions),
        "trade_status": card.trade_status,
        "document": json.dumps(document, sort_keys=True),
    }


def load_week_evidence(client: Any, bucket: str) -> dict[str, Any]:
    """Read the stored prefixes. A failed read is not an empty week."""
    return {
        "decisions": tuple(_objects(client, bucket, _DECISIONS)),
        "outcomes": tuple(body for _key, body in _objects(client, bucket, _OUTCOMES)),
        "trades": tuple(body for _key, body in _objects(client, bucket, _TRADES)),
        "portfolio": _optional(client, bucket, _PORTFOLIO),
    }


def publish_week(store: S3Store, week_ending: str, document: str) -> bool:
    """Write the week once. The same bytes are a no-op. Different bytes are refused."""
    key = f"assurance/week={week_ending}/scorecard.json"
    body = document.encode()
    existing = store.read(key)
    if existing is None:
        if store.create(key, body):
            return True
        existing = store.read(key)
    if existing == body:
        return False
    if existing is None:
        raise ScorecardError("weekly report disappeared before it could be stored")
    raise ScorecardError("weekly report already exists with a different payload")


@logger.inject_lambda_context
def weekly(event: dict[str, Any], context: LambdaContext) -> dict[str, str]:
    bucket = os.environ.get("DATA_BUCKET")
    if not bucket:
        raise ScorecardError("weekly evidence bucket is not configured")
    if set(event) != {"trigger"} or event.get("trigger") != "schedule":
        raise ScorecardError("a weekly report is not invented from a schedule")
    client = boto3.client("s3")
    evidence = load_week_evidence(client, bucket)
    published = run_weekly_assurance(
        event,
        decisions=evidence["decisions"],
        outcomes=evidence["outcomes"],
        trades=evidence["trades"],
        portfolio=evidence["portfolio"],
        as_of=datetime.now(UTC),
    )
    created = publish_week(S3Store(client, bucket), published["week_ending"], published["document"])
    return {
        "status": published["status"],
        "week_ending": published["week_ending"],
        "decisions": published["decisions"],
        "trade_status": published["trade_status"],
        "created": str(created).lower(),
    }


def _objects(client: Any, bucket: str, prefix: str) -> list[tuple[str, bytes]]:
    keys = _keys(client, bucket, prefix)
    found: list[tuple[str, bytes]] = []
    for key in keys:
        if not key.startswith(prefix) or key.endswith("/"):
            continue
        body = _get(client, bucket, key)
        if body is None:
            raise ScorecardError("stored evidence disappeared while it was being read")
        found.append((key, body))
    return found


def _keys(client: Any, bucket: str, prefix: str) -> list[str]:
    token: str | None = None
    keys: list[str] = []
    while True:
        args: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
        if token is not None:
            args["ContinuationToken"] = token
        page = client.list_objects_v2(**args)
        keys.extend(
            item["Key"] for item in page.get("Contents", []) if not str(item["Key"]).endswith("/")
        )
        token = page.get("NextContinuationToken")
        if token is None:
            return keys


def _optional(client: Any, bucket: str, key: str) -> bytes | None:
    return _get(client, bucket, key)


def _get(client: Any, bucket: str, key: str) -> bytes | None:
    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")
        if code in {"NoSuchKey", "404", "NotFound"}:
            return None
        raise
    body: bytes = response["Body"].read()
    return body
