import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from cip.domain.errors import ScorecardError
from cip.handlers import assurance
from cip.handlers.assurance import load_week_evidence, publish_week, run_weekly_assurance, weekly

BUCKET = "cip-test-evidence"
MONDAY = datetime(2026, 10, 12, tzinfo=UTC)


@dataclass(frozen=True)
class FakeLambdaContext:
    function_name: str = "cip-test-assurance"
    memory_limit_in_mb: int = 256
    invoked_function_arn: str = "arn:aws:lambda:us-east-1:123456789012:function:cip-test-assurance"
    aws_request_id: str = "req-1"


def _empty() -> dict[str, str]:
    return run_weekly_assurance(
        {"trigger": "schedule"},
        decisions=(),
        outcomes=(),
        trades=(),
        portfolio=None,
        as_of=MONDAY,
    )


def test_a_schedule_publishes_the_stored_week() -> None:
    published = _empty()
    assert published["status"] == "published"
    assert published["week_ending"] == "2026-10-11"
    assert published["decisions"] == "0"
    assert published["trade_status"] == "no_trades"
    document = json.loads(published["document"])
    assert document["scorecard"]["trade_quality"]["status"] == "no_trades"
    assert document["scorecard"]["portfolio_quality"]["sharpe"] is None


def test_a_schedule_cannot_invent_a_report() -> None:
    with pytest.raises(ScorecardError, match="not invented"):
        run_weekly_assurance(
            {"trigger": "manual"},
            decisions=(),
            outcomes=(),
            trades=(),
            portfolio=None,
            as_of=MONDAY,
        )
    with pytest.raises(ScorecardError, match="not invented"):
        run_weekly_assurance({}, decisions=(), outcomes=(), trades=(), portfolio=None, as_of=MONDAY)


def test_weekly_writes_an_empty_production_week_once(monkeypatch: pytest.MonkeyPatch) -> None:
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return MONDAY

    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        monkeypatch.setenv("DATA_BUCKET", BUCKET)
        monkeypatch.setattr(assurance, "datetime", FrozenDateTime)
        first = weekly({"trigger": "schedule"}, FakeLambdaContext())
        second = weekly({"trigger": "schedule"}, FakeLambdaContext())
        stored = client.get_object(Bucket=BUCKET, Key="assurance/week=2026-10-11/scorecard.json")
        body = stored["Body"].read()
    assert first["created"] == "true"
    assert second["created"] == "false"
    assert json.loads(body)["week_ending"] == "2026-10-11"


def test_weekly_without_a_bucket_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATA_BUCKET", raising=False)
    with pytest.raises(ScorecardError, match="bucket is not configured"):
        weekly({"trigger": "schedule"}, FakeLambdaContext())


def test_a_failed_read_is_not_an_empty_week() -> None:
    class Denied:
        def list_objects_v2(self, **_kwargs: object) -> dict[str, object]:
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "ListObjectsV2")

    with pytest.raises(ClientError):
        load_week_evidence(Denied(), BUCKET)


def test_listing_follows_pages_and_skips_a_prefix_marker() -> None:
    class Paged:
        def list_objects_v2(self, **kwargs: object) -> dict[str, object]:
            if "ContinuationToken" not in kwargs:
                return {
                    "Contents": [
                        {"Key": "decisions/"},
                        {"Key": "other/skip.json"},
                        {"Key": "decisions/one.json"},
                    ],
                    "NextContinuationToken": "next",
                }
            return {"Contents": [{"Key": "decisions/two.json"}]}

        def get_object(self, **kwargs: object) -> dict[str, Any]:
            key = str(kwargs["Key"])
            if key == "assurance/portfolio.json":
                raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "no"}}, "GetObject")
            return {"Body": _Body(b"{}")}

    evidence = load_week_evidence(Paged(), BUCKET)
    assert evidence["decisions"] == (("decisions/one.json", b"{}"), ("decisions/two.json", b"{}"))
    assert evidence["portfolio"] is None


def test_a_listed_object_that_vanishes_is_refused() -> None:
    class Vanished:
        def list_objects_v2(self, **_kwargs: object) -> dict[str, object]:
            return {"Contents": [{"Key": "decisions/gone.json"}]}

        def get_object(self, **_kwargs: object) -> dict[str, Any]:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "no"}}, "GetObject")

    with pytest.raises(ScorecardError, match="disappeared"):
        load_week_evidence(Vanished(), BUCKET)


def test_a_different_week_is_refused_and_a_lost_create_is_kept() -> None:
    class Memory:
        def __init__(self) -> None:
            self.objects: dict[str, bytes] = {}
            self.lose = False

        def read(self, key: str) -> bytes | None:
            return self.objects.get(key)

        def create(self, key: str, body: bytes) -> bool:
            if self.lose:
                self.objects[key] = body
                return False
            if key in self.objects:
                return False
            self.objects[key] = body
            return True

    store = Memory()
    assert publish_week(store, "2026-10-11", "{}") is True  # type: ignore[arg-type]
    assert publish_week(store, "2026-10-11", "{}") is False  # type: ignore[arg-type]
    with pytest.raises(ScorecardError, match="different payload"):
        publish_week(store, "2026-10-11", '{"week":1}')  # type: ignore[arg-type]
    store.lose = True
    store.objects.clear()
    assert publish_week(store, "2026-10-11", "{}") is False  # type: ignore[arg-type]


def test_a_lost_create_that_leaves_nothing_is_refused() -> None:
    class Empty:
        def read(self, _key: str) -> None:
            return None

        def create(self, _key: str, _body: bytes) -> bool:
            return False

    with pytest.raises(ScorecardError, match="disappeared"):
        publish_week(Empty(), "2026-10-11", "{}")  # type: ignore[arg-type]


class _Body:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload


def test_get_reraises_a_denied_object() -> None:
    error = ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "GetObject")

    class DeniedGet:
        def get_object(self, **_kwargs: object) -> dict[str, Any]:
            raise error

    with pytest.raises(ClientError):
        assurance._get(DeniedGet(), BUCKET, "decisions/one.json")
