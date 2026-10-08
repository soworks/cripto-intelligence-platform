"""Resume classification sources across invocations. Publication stays atomic.

A finished ticker catalog is one artifact. A later throttle does not discard it
and does not publish a symbol classification.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from cip.domain.errors import EvaluationError, RecorderError
from cip.evaluation.classification import SymbolClassification
from cip.evaluation.classification_producer import (
    CatalogIncomplete,
    classify_stored,
    fetch_component,
)
from cip.evaluation.prospect import prospective_session
from cip.evaluation.session import session_close

_SEALED = date(2026, 10, 6)
_COMPONENTS = ("tickers", "assets", "coins", "eur", "marketing", "markets")
_SOURCES = {
    "tickers": "coingecko:binance-tickers",
    "assets": "binance:asset-catalog",
    "coins": "binance:coin-config",
    "eur": "coingecko:eur-stablecoin",
    "marketing": "binance:marketing-symbols",
    "markets": "coingecko:markets",
}
_Reader = Callable[[str], object]


def ensure_bundle(
    root: Path,
    session: date,
    captured_at: datetime,
    pairs: Sequence[tuple[str, str]],
    get: _Reader,
) -> tuple[SymbolClassification, ...] | None:
    """Publish only when every source for this session is already complete."""
    _open_for(root, session, captured_at)
    stored: dict[str, object] = {}
    for name in _COMPONENTS:
        found = load_component(root, session, name)
        if found is None:
            try:
                fetched = fetch_component(name, get, pairs, stored)
            except (CatalogIncomplete, RecorderError):
                return None
            found = store_component(root, session, name, fetched, captured_at)
        stored[name] = found
    return classify_stored(pairs, stored)


def load_component(root: Path, session: date, name: str) -> object | None:
    """Return one finished source. A stored source outside the session is refused."""
    path = _path(root, session, name)
    if not path.is_file():
        return None
    document = json.loads(path.read_text(), parse_float=str)
    if not isinstance(document, dict):
        raise EvaluationError("classification source is unusable")
    if document.get("session") != session.isoformat() or document.get("component") != name:
        raise EvaluationError("classification source is outside the session")
    if document.get("provenance") != _SOURCES.get(name):
        raise EvaluationError("classification source is unusable")
    if document.get("complete") is not True:
        raise EvaluationError("classification source is incomplete")
    payload = document.get("payload")
    digest = document.get("content_sha256")
    if not isinstance(digest, str) or digest != _hash(payload):
        raise EvaluationError("classification source is unusable")
    captured_at = _moment(document.get("captured_at"))
    _within(session, captured_at)
    return payload


def store_component(
    root: Path, session: date, name: str, payload: object, captured_at: datetime
) -> object:
    """Store one finished source. A different body for the same source is a conflict."""
    _open_for(root, session, captured_at)
    if name not in _SOURCES:
        raise EvaluationError("classification source is unknown")
    payload = _freeze(payload)
    body = _json(
        {
            "captured_at": _iso(captured_at),
            "complete": True,
            "component": name,
            "content_sha256": _hash(payload),
            "payload": payload,
            "provenance": _SOURCES[name],
            "session": session.isoformat(),
        }
    )
    path = _path(root, session, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() == body:
            return payload
        raise EvaluationError("conflicting observation")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(body)
    try:
        os.link(temporary, path)
    except FileExistsError:
        if path.read_bytes() != body:
            raise EvaluationError("conflicting observation") from None
    finally:
        temporary.unlink(missing_ok=True)
    return payload


def _open_for(root: Path, session: date, captured_at: datetime) -> None:
    prospective_session(session)
    if session == _SEALED:
        raise EvaluationError("sealed session stays sealed")
    if _manifest(root, session).is_file():
        raise EvaluationError("finalized session is sealed")
    _within(session, captured_at)


def _within(session: date, captured_at: datetime) -> None:
    if captured_at.tzinfo is None or captured_at.utcoffset() != timedelta(0):
        raise EvaluationError("captured_at must be timezone-aware UTC")
    opened = datetime.combine(session, time.min, tzinfo=UTC)
    if captured_at < opened or captured_at >= session_close(session):
        raise EvaluationError("classification source is outside the session")


def _moment(value: object) -> datetime:
    if not isinstance(value, str):
        raise EvaluationError("classification source is unusable")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise EvaluationError("classification source is unusable") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise EvaluationError("captured_at must be timezone-aware UTC")
    return parsed.astimezone(UTC)


def _hash(payload: object) -> str:
    return hashlib.sha256(_canon(payload)).hexdigest()


def _freeze(payload: object) -> object:
    frozen = json.loads(_canon(payload), parse_float=str)
    if not isinstance(frozen, dict | list):
        raise EvaluationError("classification source is unusable")
    return frozen


def _canon(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_plain).encode()


def _plain(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError(f"classification source cannot encode {type(value).__name__}")


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _json(document: dict[str, object]) -> bytes:
    return json.dumps(document, sort_keys=True, default=_plain).encode()


def _path(root: Path, session: date, name: str) -> Path:
    folder = root / "captures" / f"session={session.isoformat()}" / "classification_sources"
    return folder / f"{name}.json"


def _manifest(root: Path, session: date) -> Path:
    return root / "sessions" / f"date={session.isoformat()}" / "manifest.json"
