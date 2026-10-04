from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from cip.domain.errors import EvaluationError
from cip.evaluation.decision import DecisionRecord, ForwardOutcome, decision_id


class ObjectStore(Protocol):
    def read(self, key: str) -> bytes | None: ...

    def create(self, key: str, body: bytes) -> bool: ...


@dataclass(frozen=True)
class StoredDecision:
    """One stored decision object. ``sha256`` is the hash of the stored bytes."""

    key: str
    sha256: str
    created: bool
    record: DecisionRecord


class DecisionWriter(Protocol):
    def write(self, record: DecisionRecord) -> StoredDecision: ...


def decision_key(record: DecisionRecord) -> str:
    """S3 and local layout for one decision. The date is the UTC close date."""
    day = record.evaluated_at.astimezone(UTC).date().isoformat()
    identity = decision_id(record)
    return (
        f"decisions/cohort={record.cohort.value}/date={day}/symbol={record.symbol}/{identity}.json"
    )


def append_decision(root: Path, record: DecisionRecord) -> bool:
    """Write the decision once. The same document is a no-op. A different document is refused."""
    path = root / decision_key(record)
    _require_inside(root / "decisions", path)
    return _create(path, _body(record.to_document()))


class FileDecisionWriter:
    """Write decisions under ``root`` with the same key the object store uses."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def write(self, record: DecisionRecord) -> StoredDecision:
        created = append_decision(self._root, record)
        key = decision_key(record)
        body = (self._root / key).read_bytes()
        return StoredDecision(key, hashlib.sha256(body).hexdigest(), created, record)


def append_decision_to(store: ObjectStore, record: DecisionRecord) -> StoredDecision:
    """Create the object once. The same bytes are a no-op. Different bytes are refused."""
    key = decision_key(record)
    body = _body(record.to_document())
    digest = hashlib.sha256(body).hexdigest()
    existing = store.read(key)
    if existing is None:
        if store.create(key, body):
            return StoredDecision(key, digest, True, record)
        existing = store.read(key)
    if existing == body:
        return StoredDecision(key, digest, False, record)
    name = key.rsplit("/", 1)[-1]
    if existing is None:
        raise EvaluationError(f"record {name} disappeared before it could be stored")
    raise EvaluationError(f"record {name} already exists with a different payload")


class ObjectDecisionWriter:
    """Write decisions through an object store. S3 uses this with ``IfNoneMatch``."""

    def __init__(self, store: ObjectStore) -> None:
        self._store = store

    def write(self, record: DecisionRecord) -> StoredDecision:
        return append_decision_to(self._store, record)


def append_outcome(root: Path, outcome: ForwardOutcome, *, as_of: datetime) -> bool:
    """Write one horizon after it has elapsed. The decision file is left untouched."""
    if as_of.tzinfo is None or as_of.utcoffset() != timedelta(0):
        raise EvaluationError("as_of must be timezone-aware UTC")
    record = _load_decision(root, outcome.decision_id)
    if as_of < record.evaluated_at + timedelta(days=outcome.horizon_days):
        raise EvaluationError("horizon has not elapsed")
    path = (
        root
        / "decision-outcomes"
        / f"decision={outcome.decision_id}"
        / f"horizon={outcome.horizon_days}.json"
    )
    _require_inside(root / "decision-outcomes", path)
    return _create(path, _body(outcome.to_document()))


def _load_decision(root: Path, identity: str) -> DecisionRecord:
    matches = list((root / "decisions").rglob(f"{identity}.json"))
    if not matches:
        raise EvaluationError("decision is missing")
    if len(matches) != 1:
        raise EvaluationError("decision id is ambiguous")
    path = matches[0]
    _require_inside(root / "decisions", path)
    try:
        document = json.loads(path.read_text())
        record = DecisionRecord.from_document(document)
    except (
        OSError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
        ValidationError,
    ) as error:
        raise EvaluationError("decision file is unusable") from error
    if decision_id(record) != identity:
        raise EvaluationError("decision file does not match its id")
    return record


def _body(document: dict[str, object]) -> bytes:
    return json.dumps(document, sort_keys=True).encode()


def _require_inside(directory: Path, path: Path) -> None:
    if not path.resolve().is_relative_to(directory.resolve()):
        raise EvaluationError("record path escapes the store")


def _create(path: Path, body: bytes) -> bool:
    if path.exists():
        return _same_or_refuse(path, body)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(body)
    try:
        try:
            os.link(temporary, path)
        except FileExistsError:
            return _same_or_refuse(path, body)
        return True
    finally:
        temporary.unlink(missing_ok=True)


def _same_or_refuse(path: Path, body: bytes) -> bool:
    if path.read_bytes() == body:
        return False
    raise EvaluationError(f"record {path.name} already exists with a different payload")
