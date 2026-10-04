from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cip.domain.errors import EvaluationError
from cip.evaluation.decision import DecisionRecord, ForwardOutcome, decision_id


def append_decision(root: Path, record: DecisionRecord) -> bool:
    """Write the decision once. The same document is a no-op. A different document is refused."""
    identity = decision_id(record)
    day = record.evaluated_at.astimezone(UTC).date().isoformat()
    path = (
        root
        / "decisions"
        / f"cohort={record.cohort.value}"
        / f"date={day}"
        / f"symbol={record.symbol}"
        / f"{identity}.json"
    )
    return _create(path, _body(record.to_document()))


def append_outcome(root: Path, outcome: ForwardOutcome, *, as_of: datetime) -> bool:
    """Write one horizon after it has elapsed. The decision file is left untouched."""
    if as_of.tzinfo is None or as_of.utcoffset() != timedelta(0):
        raise EvaluationError("as_of must be timezone-aware UTC")
    decision = _find_decision(root, outcome.decision_id)
    if decision is None:
        raise EvaluationError("decision is missing")
    document = json.loads(decision.read_text())
    evaluated_at = datetime.fromisoformat(document["evaluated_at"])
    if as_of < evaluated_at + timedelta(days=outcome.horizon_days):
        raise EvaluationError("horizon has not elapsed")
    path = (
        root
        / "decision-outcomes"
        / f"decision={outcome.decision_id}"
        / f"horizon={outcome.horizon_days}.json"
    )
    return _create(path, _body(outcome.to_document()))


def _find_decision(root: Path, identity: str) -> Path | None:
    matches = list((root / "decisions").rglob(f"{identity}.json"))
    if not matches:
        return None
    return matches[0]


def _body(document: dict[str, object]) -> bytes:
    return json.dumps(document, sort_keys=True).encode()


def _create(path: Path, body: bytes) -> bool:
    if path.exists():
        if path.read_bytes() == body:
            return False
        raise EvaluationError(f"record {path.name} already exists with a different payload")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_bytes(body)
    temporary.replace(path)
    return True
