import json

import boto3
import pytest

from tests.integration.conftest import require_env

pytestmark = pytest.mark.integration

_BAR_FIELDS = ("open", "high", "low", "close", "volume")


def test_scheduled_recorder_persists_observations_and_failures() -> None:
    function_name = require_env("CIP_RECORDER_FUNCTION")
    schedule = boto3.client("scheduler").get_schedule(Name=require_env("CIP_RECORDER_SCHEDULE"))
    assert schedule["State"] == "ENABLED"
    assert schedule["ScheduleExpression"] == "rate(1 hour)"
    assert schedule["Target"]["Arn"].endswith(function_name)

    response = boto3.client("lambda").invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=b"{}",
    )
    assert response["StatusCode"] == 200
    assert response.get("FunctionError") is None
    body = json.loads(response["Payload"].read())
    observations = [_object(key) for key in body["observation_keys"]]
    failures = [_object(key) for key in body["failure_keys"]]
    liquidity = {
        (item["series"], item["symbol"])
        for item in observations
        if item["series"] in {"spread", "depth"}
    }

    assert liquidity == {
        ("spread", "BTCUSDT"),
        ("depth", "BTCUSDT"),
        ("spread", "ETHUSDT"),
        ("depth", "ETHUSDT"),
    }
    assert {(item["series"], item.get("symbol")) for item in observations}.isdisjoint(
        {(item["series"], item.get("symbol")) for item in failures}
    )
    for key, item in zip(body["observation_keys"], observations, strict=True):
        assert key.startswith("observations/")
        assert item["collection_status"] == "ok"
        assert item["values"]
        assert item["schema_version"] == 1
        assert all(field not in item for field in _BAR_FIELDS)
    for key, item in zip(body["failure_keys"], failures, strict=True):
        assert key.startswith("observation-failures/")
        assert item["kind"] == "collection_failure"
        assert item["error"]
        assert "values" not in item
        assert all(field not in item for field in _BAR_FIELDS)


def _object(key: str) -> dict[str, object]:
    stored = boto3.client("s3").get_object(Bucket=require_env("CIP_DATA_BUCKET"), Key=key)
    document: dict[str, object] = json.loads(stored["Body"].read())
    return document
