"""Write one weekly Engine Assurance report. Absent inputs stay absent."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

from cip.domain.errors import ScorecardError
from cip.evaluation.scorecard import Scorecard


def weekly_scorecard(*, week_ending: date, scorecard: Scorecard) -> dict[str, object]:
    """Place one stored scorecard under the week that contains it."""
    if type(week_ending) is not date:
        raise ScorecardError("week ending is a date")
    if type(scorecard) is not Scorecard:
        raise ScorecardError("a weekly report is a stored scorecard")
    document = scorecard.to_document()
    return {"week_ending": week_ending.isoformat(), "scorecard": document}


def write_weekly_scorecard(root: Path, week_ending: date, scorecard: Scorecard) -> bool:
    """Write the week once. The same document is a no-op. A different document is refused."""
    body = json.dumps(
        weekly_scorecard(week_ending=week_ending, scorecard=scorecard), sort_keys=True
    ).encode()
    path = root / "assurance" / f"week={week_ending.isoformat()}" / "scorecard.json"
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
    raise ScorecardError(f"record {path.name} already exists with a different payload")
