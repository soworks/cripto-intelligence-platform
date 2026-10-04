import json
from datetime import datetime, timedelta
from pathlib import Path

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from cip.domain.errors import RecorderError
from cip.recorders.observation import CollectionFailure, Observation
from cip.recorders.sources import parse_btc_dominance
from cip.recorders.store import (
    DirectoryStore,
    S3Store,
    append_failure_to,
    append_observation_to,
)
from tests.unit.recorders.test_recorders import NOW, _dominance

BUCKET = "cip-dev-data-test"


def _bucket() -> S3Store:
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket=BUCKET)
    return S3Store(client, BUCKET)


def test_s3_keeps_the_first_file_when_only_the_poll_time_changes() -> None:
    with mock_aws():
        store = _bucket()
        observation = _dominance()
        assert append_observation_to(store, observation) is True
        later = append_observation_to(
            store,
            _dominance_at(observation.observed_at + timedelta(hours=1)),
        )
        assert later is False
        body = store.read(_only_key(store))
    assert body is not None
    assert json.loads(body)["observed_at"] == NOW.isoformat()


def test_s3_records_a_failure_and_refuses_a_changed_observation() -> None:
    with mock_aws():
        store = _bucket()
        observation = _dominance()
        append_observation_to(store, observation)
        replacement = Observation(
            series=observation.series,
            provider=observation.provider,
            source_timestamp=observation.source_timestamp,
            observed_at=observation.observed_at,
            symbol=None,
            values=(("btc_dominance", observation.values[0][1] + 1),),
            units=observation.units,
        )
        with pytest.raises(RecorderError, match="different payload"):
            append_observation_to(store, replacement)
        failure = CollectionFailure(
            series="funding",
            provider="binance",
            observed_at=NOW,
            symbol="BTCUSDT",
            error="host returned 451",
        )
        assert append_failure_to(store, failure) is True
        keys = _keys(store)
    assert any(key.startswith("observation-failures/") for key in keys)
    assert all(not key.startswith("klines/") for key in keys)


def test_a_missing_object_reads_as_absent(tmp_path: Path) -> None:
    assert DirectoryStore(tmp_path).read("observations/missing") is None

    class Missing:
        def get_object(self, **_kwargs: object) -> None:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "no"}}, "GetObject")

    assert S3Store(Missing(), BUCKET).read("observations/missing") is None


def test_s3_create_does_not_hide_access_errors() -> None:
    class Denied:
        def put_object(self, **_kwargs: object) -> None:
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "PutObject")

    with pytest.raises(ClientError):
        S3Store(Denied(), BUCKET).create("observations/missing", b"{}")


def test_s3_read_does_not_hide_access_errors() -> None:
    class Denied:
        def get_object(self, **_kwargs: object) -> None:
            raise ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "no"}},
                "GetObject",
            )

    with pytest.raises(ClientError):
        S3Store(Denied(), BUCKET).read("observations/missing")


def _dominance_at(moment: datetime) -> Observation:
    parsed = parse_btc_dominance(
        {"data": {"market_cap_percentage": {"btc": 54.2}, "updated_at": 1_759_000_000}},
        observed_at=moment,
    )
    return parsed


def _only_key(store: S3Store) -> str:
    keys = _keys(store)
    assert len(keys) == 1
    return keys[0]


def _keys(store: S3Store) -> list[str]:
    response = store.client.list_objects_v2(Bucket=store.bucket)
    return [item["Key"] for item in response.get("Contents", [])]
