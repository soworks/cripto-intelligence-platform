from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

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


def append_observation(root: Path, observation: Observation) -> bool:
    """Write the observation once. The same payload is a no-op. A different payload is refused."""
    return _append(
        _path(
            root,
            "observations",
            observation.family,
            observation.series,
            observation.identity_time,
            observation_id(observation),
        ),
        json.dumps(observation_document(observation), sort_keys=True).encode(),
    )


def append_failure(root: Path, failure: CollectionFailure) -> bool:
    return _append(
        _path(
            root,
            "observation-failures",
            family_of(failure.series),
            failure.series,
            failure.observed_at,
            failure_id(failure),
        ),
        json.dumps(failure_document(failure), sort_keys=True).encode(),
    )


def _path(root: Path, kind: str, family: str, series: str, moment: datetime, identity: str) -> Path:
    day = moment.astimezone(UTC).date()
    return (
        root
        / kind
        / f"family={family}"
        / f"series={series}"
        / f"date={day.isoformat()}"
        / f"{identity}.json"
    )


def _append(path: Path, body: bytes) -> bool:
    if path.exists():
        current = path.read_bytes()
        if current == body or _same_point(current, body):
            return False
        raise RecorderError(f"observation {path.name} already exists with a different payload")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_bytes(body)
    temporary.replace(path)
    return True


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
