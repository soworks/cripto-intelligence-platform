import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from cip.domain.errors import ExchangeBannedError, RecorderError
from cip.handlers.recorders import record
from cip.recorders.collect import CollectionResult
from cip.recorders.observation import CollectionFailure
from tests.unit.recorders.test_recorders import NOW, _dominance

REPO_POLICY = Path(__file__).parents[3] / "policies" / "investment-policy.yaml"
BUCKET = "cip-dev-data-test"


@dataclass(frozen=True)
class _Context:
    function_name: str = "cip-test-recorders"
    memory_limit_in_mb: int = 256
    invoked_function_arn: str = "arn:aws:lambda:us-east-1:123456789012:function:cip-test"
    aws_request_id: str = "req-1"


def test_record_writes_observations_and_failure_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def collect_live(**kwargs: object) -> CollectionResult:
        captured.update(kwargs)
        return CollectionResult(
            observations=(_dominance(),),
            failures=(
                CollectionFailure(
                    series="funding",
                    provider="binance",
                    observed_at=NOW,
                    symbol="BTCUSDT",
                    error="host returned 451",
                ),
            ),
        )

    monkeypatch.setattr("cip.handlers.recorders.collect_live", collect_live)
    with mock_aws():
        _prepare(monkeypatch)
        body = record({}, _Context())
        stored = _documents()

    assert captured["symbols"] == ("BTCUSDT", "ETHUSDT")
    assert captured["depth_band"] == Decimal("0.02")
    assert body["observations"] == 1
    assert body["failures"] == 1
    observations = [item for key, item in stored if key.startswith("observations/")]
    failures = [item for key, item in stored if key.startswith("observation-failures/")]
    assert observations[0]["collection_status"] == "ok"
    assert observations[0]["values"]["btc_dominance"] == "54.2"
    assert "open" not in observations[0]
    assert "close" not in observations[0]
    assert failures[0]["kind"] == "collection_failure"
    assert failures[0]["error"] == "host returned 451"
    assert "values" not in failures[0]
    assert {(item["series"], item.get("symbol")) for item in observations}.isdisjoint(
        {(item["series"], item.get("symbol")) for item in failures}
    )


def test_a_ban_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def collect_live(**_kwargs: object) -> CollectionResult:
        raise ExchangeBannedError("Binance returned 418; the scan must stop")

    monkeypatch.setattr("cip.handlers.recorders.collect_live", collect_live)
    with mock_aws():
        _prepare(monkeypatch)
        with pytest.raises(ExchangeBannedError):
            record({}, _Context())
        assert _documents() == []


def test_symbols_are_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLICY_PATH", str(REPO_POLICY))
    monkeypatch.setenv("DATA_BUCKET", BUCKET)
    monkeypatch.setenv("RECORDER_SYMBOLS", " , ")
    with pytest.raises(RecorderError, match="at least one symbol"):
        record({}, _Context())


def _prepare(monkeypatch: pytest.MonkeyPatch) -> None:
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
    monkeypatch.setenv("POLICY_PATH", str(REPO_POLICY))
    monkeypatch.setenv("DATA_BUCKET", BUCKET)
    monkeypatch.setenv("RECORDER_SYMBOLS", "BTCUSDT,ETHUSDT")


def _documents() -> list[tuple[str, dict[str, object]]]:
    client = boto3.client("s3", region_name="us-east-1")
    response = client.list_objects_v2(Bucket=BUCKET)
    found: list[tuple[str, dict[str, object]]] = []
    for item in response.get("Contents", []):
        raw = client.get_object(Bucket=BUCKET, Key=item["Key"])["Body"].read()
        found.append((item["Key"], json.loads(raw)))
    return found
