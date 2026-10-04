from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from botocore.exceptions import ClientError

from cip.domain.errors import RecorderError
from cip.recorders.observation import (
    CollectionFailure,
    Observation,
    failure_document,
    failure_id,
    family_of,
    observation_document,
    observation_id,
)


class ObjectStore(Protocol):
    def read(self, key: str) -> bytes | None: ...

    def create(self, key: str, body: bytes) -> bool: ...


class DirectoryStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def read(self, key: str) -> bytes | None:
        path = self._root / key
        if not path.exists():
            return None
        return path.read_bytes()

    def create(self, key: str, body: bytes) -> bool:
        path = self._root / key
        if path.exists():
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.tmp")
        temporary.write_bytes(body)
        temporary.replace(path)
        return True


class S3Store:
    def __init__(self, client: Any, bucket: str) -> None:
        self.client = client
        self.bucket = bucket

    def read(self, key: str) -> bytes | None:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code")
            if code in {"NoSuchKey", "404", "NotFound"}:
                return None
            raise
        body: bytes = response["Body"].read()
        return body

    def create(self, key: str, body: bytes) -> bool:
        """Create the key only when it is absent. A missing key is not a GetObject."""
        try:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=body, IfNoneMatch="*")
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code")
            if code in {"PreconditionFailed", "412"}:
                return False
            raise
        return True


def append_observation(root: Path, observation: Observation) -> bool:
    """Write the observation once. The same payload is a no-op. A different payload is refused."""
    return append_observation_to(DirectoryStore(root), observation)


def append_observation_to(store: ObjectStore, observation: Observation) -> bool:
    return _append(
        store,
        object_key(
            "observations",
            observation.family,
            observation.series,
            observation.identity_time,
            observation_id(observation),
        ),
        json.dumps(observation_document(observation), sort_keys=True).encode(),
    )


def append_failure(root: Path, failure: CollectionFailure) -> bool:
    return append_failure_to(DirectoryStore(root), failure)


def append_failure_to(store: ObjectStore, failure: CollectionFailure) -> bool:
    return _append(
        store,
        object_key(
            "observation-failures",
            family_of(failure.series),
            failure.series,
            failure.observed_at,
            failure_id(failure),
        ),
        json.dumps(failure_document(failure), sort_keys=True).encode(),
    )


def object_key(kind: str, family: str, series: str, moment: datetime, identity: str) -> str:
    day = moment.astimezone(UTC).date()
    return f"{kind}/family={family}/series={series}/date={day.isoformat()}/{identity}.json"


def _append(store: ObjectStore, key: str, body: bytes) -> bool:
    if store.create(key, body):
        return True
    current = store.read(key)
    if current == body or (current is not None and _same_point(current, body)):
        return False
    name = key.rsplit("/", 1)[-1]
    raise RecorderError(f"observation {name} already exists with a different payload")


def _same_point(current: bytes, body: bytes) -> bool:
    """A later poll of the same source point keeps the first file."""
    try:
        left = json.loads(current)
        right = json.loads(body)
    except json.JSONDecodeError:
        return False
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    left.pop("observed_at", None)
    right.pop("observed_at", None)
    return left == right
