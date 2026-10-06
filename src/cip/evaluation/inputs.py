"""Stored closed-session inputs. A capture after the close is not written."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from cip.domain.errors import EvaluationError
from cip.evaluation.scan import ScanCandidate, UniverseSnapshot
from cip.evaluation.session import session_close

_SYMBOL = re.compile(r"^[A-Z0-9]{1,20}$")
_NAME = re.compile(r"^symbol=([A-Z0-9]{1,20})\.json$")
_MISSING = object()


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AbsentInput(_Strict):
    """The producer looked. The value is not inferred from a missing key."""

    kind: Literal["absent_input"]
    session: date
    input: Literal["candidate", "daily_bar"]
    symbol: str
    produced_at: datetime


@dataclass(frozen=True)
class RecordedInputs:
    """What is already stored for one session. This does not decide readiness."""

    snapshot: UniverseSnapshot | None
    candidates: Mapping[str, ScanCandidate | None]
    candidate_files_present: Mapping[str, bool]
    bar_absences: Mapping[str, bool]


def write_universe(root: Path, snapshot: UniverseSnapshot) -> bool:
    """Store one universe snapshot. The same bytes are a no-op."""
    close = session_close(snapshot.session)
    _require_utc(snapshot.observed_at, "observed_at")
    if snapshot.observed_at > close:
        raise EvaluationError("snapshot is after the close")
    if len(snapshot.symbols) != len(set(snapshot.symbols)):
        raise EvaluationError("universe snapshot repeats a symbol")
    return _create(root / _universe_key(snapshot.session), _body(snapshot))


def write_candidate(root: Path, session: date, candidate: ScanCandidate) -> bool:
    """Store one candidate packet. An absence for that symbol wins."""
    close = session_close(session)
    symbol = _symbol(candidate.facts.symbol)
    if candidate.market.as_of > close:
        raise EvaluationError("candidate is after the close")
    for cap, stamp in (
        (candidate.coingecko.market_cap_usd, candidate.coingecko.source_timestamp),
        (candidate.cmc.market_cap_usd, candidate.cmc.source_timestamp),
    ):
        if cap is not None and stamp is None:
            raise EvaluationError("undated fundamental")
        if stamp is not None and stamp > close:
            raise EvaluationError("candidate is after the close")
    if (root / _absence_key(session, "candidate", symbol)).exists():
        raise EvaluationError("candidate absence already recorded")
    return _create(root / _candidate_key(session, symbol), _body(candidate))


def write_absence(root: Path, absence: AbsentInput) -> bool:
    """Store an explicit absence. ``produced_at`` may be after the close."""
    session_close(absence.session)
    symbol = _symbol(absence.symbol)
    _require_utc(absence.produced_at, "produced_at")
    if absence.input == "candidate" and (root / _candidate_key(absence.session, symbol)).exists():
        raise EvaluationError("candidate already recorded")
    return _create(root / _absence_key(absence.session, absence.input, symbol), _body(absence))


def load_session(root: Path, session: date) -> RecordedInputs:
    """Read the stored snapshot, packets, and absences. Do not fill gaps."""
    session_close(session)
    packets = _names(root / _candidate_key(session, "BTCUSDT").parent)
    absences = _names(root / _absence_key(session, "candidate", "BTCUSDT").parent)
    if packets & absences:
        raise EvaluationError("candidate absence conflicts with a packet")
    candidates: dict[str, ScanCandidate | None] = {}
    present: dict[str, bool] = {}
    for symbol in packets:
        candidates[symbol] = _read_candidate(root / _candidate_key(session, symbol), symbol)
        present[symbol] = True
    for symbol in absences:
        _read_absence(
            root / _absence_key(session, "candidate", symbol),
            session,
            "candidate",
            symbol,
        )
        candidates[symbol] = None
        present[symbol] = True
    bars = _names(root / _absence_key(session, "daily_bar", "BTCUSDT").parent)
    for symbol in bars:
        _read_absence(
            root / _absence_key(session, "daily_bar", symbol),
            session,
            "daily_bar",
            symbol,
        )
    return RecordedInputs(
        _read_universe(root, session),
        candidates,
        present,
        {symbol: True for symbol in bars},
    )


def _universe_key(session: date) -> Path:
    return Path("sessions") / f"date={session.isoformat()}" / "universe.json"


def _candidate_key(session: date, symbol: str) -> Path:
    return Path("sessions") / f"date={session.isoformat()}" / "candidates" / f"symbol={symbol}.json"


def _absence_key(session: date, kind: str, symbol: str) -> Path:
    return (
        Path("sessions")
        / f"date={session.isoformat()}"
        / "absences"
        / f"input={kind}"
        / f"symbol={symbol}.json"
    )


def _symbol(symbol: str) -> str:
    if _SYMBOL.fullmatch(symbol) is None:
        raise EvaluationError("symbol must be 1 to 20 uppercase letters or digits")
    return symbol


def _require_utc(moment: datetime, label: str) -> None:
    if moment.tzinfo is None or moment.utcoffset() != timedelta(0):
        raise EvaluationError(f"{label} must be timezone-aware UTC")


def _body(model: BaseModel) -> bytes:
    return json.dumps(model.model_dump(mode="json"), sort_keys=True).encode()


def _create(path: Path, body: bytes) -> bool:
    if path.exists():
        return _same(path, body)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(body)
    try:
        try:
            os.link(temporary, path)
        except FileExistsError:
            return _same(path, body)
        return True
    finally:
        temporary.unlink(missing_ok=True)


def _same(path: Path, body: bytes) -> bool:
    if path.read_bytes() == body:
        return False
    raise EvaluationError(f"session input {path.name} already exists with a different payload")


def _names(directory: Path) -> set[str]:
    if not directory.is_dir():
        return set()
    found: set[str] = set()
    for path in directory.iterdir():
        matched = _NAME.fullmatch(path.name)
        if matched is None:
            raise EvaluationError("session input is unusable")
        found.add(matched.group(1))
    return found


def _read_json(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise EvaluationError("session input is unusable") from error
    if not isinstance(document, dict):
        raise EvaluationError("session input is unusable")
    return document


def _read_universe(root: Path, session: date) -> UniverseSnapshot | None:
    path = root / _universe_key(session)
    if not path.is_file():
        return None
    try:
        snapshot = UniverseSnapshot.model_validate(_read_json(path))
    except ValidationError as error:
        raise EvaluationError("session input is unusable") from error
    if snapshot.session != session:
        raise EvaluationError("universe snapshot is for a different session")
    return snapshot


def _read_candidate(path: Path, symbol: str) -> ScanCandidate:
    document = _read_json(path)
    _restore_clocks(document)
    try:
        candidate = ScanCandidate.model_validate(document)
    except ValidationError as error:
        raise EvaluationError("session input is unusable") from error
    if candidate.facts.symbol != symbol:
        raise EvaluationError("candidate file does not match its symbol")
    return candidate


def _restore_clocks(document: dict[str, Any]) -> None:
    for name in ("coingecko", "cmc"):
        reading = document.get(name)
        if isinstance(reading, dict):
            stamp = reading.get("source_timestamp", _MISSING)
            if stamp is not _MISSING:
                reading["source_timestamp"] = _instant(stamp)


def _instant(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise EvaluationError("session input is unusable")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise EvaluationError("session input is unusable") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise EvaluationError("session input is unusable")
    return parsed.astimezone(UTC)


def _read_absence(path: Path, session: date, kind: str, symbol: str) -> None:
    try:
        absence = AbsentInput.model_validate(_read_json(path))
    except ValidationError as error:
        raise EvaluationError("session input is unusable") from error
    if (absence.session, absence.input, absence.symbol) != (session, kind, symbol):
        raise EvaluationError("absence file does not match its name")
