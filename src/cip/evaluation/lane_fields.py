"""Point-in-time lane fields for symbols that already cleared the market-cap floor.

This module does not call a provider and does not change eligibility thresholds.
A missing unlock schedule stays unavailable. It is not recorded as false.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

from cip.domain.errors import EvaluationError
from cip.evaluation.eligibility import CandidateFacts, Lane
from cip.evaluation.prospect import prospective_session
from cip.evaluation.session import session_close

_SYMBOL = re.compile(r"^[A-Z0-9]{1,20}$")
_SEALED = date(2026, 10, 6)
_DAY_MS = 86_400_000
UnlockState = Literal["future_unlocks", "no_applicable_future_unlock", "unavailable"]


@dataclass(frozen=True)
class MarketFields:
    """Supply, rank, and FDV from one dated CoinGecko row."""

    provider: str
    asset_id: str
    source_timestamp: datetime
    circulating_ratio: Decimal | None
    fdv_to_market_cap: Decimal | None
    market_cap_rank: int | None


@dataclass(frozen=True)
class LaneFields:
    """Lane evidence for one symbol. Clocks stay with the values they date."""

    symbol: str
    base_asset: str
    lane: Lane
    coin_id: str
    circulating_ratio: Decimal | None
    history_days: int | None
    history_open: datetime | None
    market_cap_rank: int | None
    fdv_to_market_cap: Decimal | None
    unlock_state: UnlockState
    market_source_timestamp: datetime | None
    market_captured_at: datetime
    history_captured_at: datetime
    unlock_provider: str | None
    unlock_provider_id: str | None
    unlock_source_timestamp: datetime | None


def parse_market_fields(payload: object) -> dict[str, MarketFields]:
    """Dated supply, rank, and FDV. A repeated id is unusable."""
    if not isinstance(payload, list):
        raise EvaluationError("capture is unusable")
    found: dict[str, MarketFields] = {}
    seen: set[str] = set()
    for row in payload:
        if not isinstance(row, dict):
            raise EvaluationError("capture is unusable")
        asset_id = row.get("id")
        if not isinstance(asset_id, str) or asset_id == "":
            raise EvaluationError("capture is unusable")
        if asset_id in seen:
            raise EvaluationError("market catalog repeats an asset")
        seen.add(asset_id)
        reading = _market_row(asset_id, row)
        if reading is not None:
            found[asset_id] = reading
    return found


def parse_first_daily_open(payload: object) -> int | None:
    """Open time of the earliest daily kline. An empty page is missing."""
    if not isinstance(payload, list):
        raise EvaluationError("capture is unusable")
    if not payload:
        return None
    row = payload[0]
    if not isinstance(row, list) or not row:
        raise EvaluationError("capture is unusable")
    return _midnight(row[0])


def history_days(open_time_ms: object, session: date) -> int:
    """UTC midnights from the listing open to the session date."""
    open_date = datetime.fromtimestamp(_midnight(open_time_ms) / 1000, UTC).date()
    if open_date > session:
        raise EvaluationError("history starts after the session")
    return (session - open_date).days


def classify_unlock(
    events: Sequence[datetime] | None, *, tracked: bool, as_of: datetime
) -> UnlockState:
    """A source that returns no schedule does not prove the schedule is unknown."""
    if not tracked or events is None:
        return "unavailable"
    for event in events:
        _clock(event)
        if event > as_of:
            return "future_unlocks"
    return "no_applicable_future_unlock"


def unlock_schedule_known(state: UnlockState) -> bool | None:
    """Map a known schedule to true. Unavailable stays missing, not false."""
    if state == "unavailable":
        return None
    return True


def overlay_lane(
    facts: CandidateFacts,
    *,
    lane: Lane,
    circulating_ratio: Decimal | None,
    history_days: int | None,
    market_cap_rank: int | None,
    fdv_to_market_cap: Decimal | None,
    unlock: UnlockState,
) -> CandidateFacts:
    """Copy one lane's fields onto facts. The other lane's fields stay missing."""
    if lane is Lane.NORMAL:
        if unlock != "unavailable":
            raise EvaluationError("normal lane does not record an unlock schedule")
        rank: int | None = market_cap_rank
        fdv: Decimal | None = fdv_to_market_cap
        known = None
    else:
        if market_cap_rank is not None or fdv_to_market_cap is not None:
            raise EvaluationError("high-risk lane does not record rank or fdv")
        rank = None
        fdv = None
        known = unlock_schedule_known(unlock)
    return facts.model_copy(
        update={
            "circulating_ratio": circulating_ratio,
            "history_days": history_days,
            "market_cap_rank": rank,
            "fdv_to_market_cap": fdv,
            "unlock_schedule_known": known,
        }
    )


def store_lane(root: Path, session: date, item: LaneFields) -> None:
    """Store one pre-close lane document. A sealed session is refused."""
    prospective_session(session)
    if session == _SEALED:
        raise EvaluationError("sealed session stays sealed")
    if _manifest(root, session).exists():
        raise EvaluationError("finalized session is sealed")
    _checked(session, item)
    _create(_path(root, session, item.symbol), _body(session, item))


def _market_row(asset_id: str, row: Mapping[str, object]) -> MarketFields | None:
    circulating = _amount(row.get("circulating_supply"))
    total = _amount(row.get("total_supply"))
    fdv = _amount(row.get("fully_diluted_valuation"))
    cap = _amount(row.get("market_cap"))
    rank = _rank(row.get("market_cap_rank"))
    stamp = _moment(row.get("last_updated"))
    if stamp is None:
        if any(value is not None for value in (circulating, total, fdv, cap, rank)):
            raise EvaluationError("undated fundamental")
        return None
    ratio = None if circulating is None or total is None or total == 0 else circulating / total
    multiple = None if fdv is None or cap is None or cap == 0 else fdv / cap
    return MarketFields("coingecko", asset_id, stamp, ratio, multiple, rank)


def _amount(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise EvaluationError("capture is unusable")
    if isinstance(value, int):
        parsed = Decimal(value)
    elif isinstance(value, float):
        parsed = Decimal(str(value))
    elif isinstance(value, str):
        try:
            parsed = Decimal(value)
        except InvalidOperation as error:
            raise EvaluationError("capture is unusable") from error
    else:
        raise EvaluationError("capture is unusable")
    if not parsed.is_finite() or parsed < 0:
        raise EvaluationError("capture is unusable")
    return parsed


def _rank(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 1:
        raise EvaluationError("capture is unusable")
    return value


def _midnight(value: object) -> int:
    if type(value) is not int or value < 0 or value % _DAY_MS != 0:
        raise EvaluationError("capture is unusable")
    return value


def _clock(moment: datetime) -> None:
    if moment.tzinfo is None or moment.utcoffset() != timedelta(0):
        raise EvaluationError("capture is unusable")


def _moment(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise EvaluationError("capture is unusable")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise EvaluationError("capture is unusable") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise EvaluationError("capture is unusable")
    return parsed.astimezone(UTC)


def _checked(session: date, item: LaneFields) -> None:
    if _SYMBOL.fullmatch(item.symbol) is None or _SYMBOL.fullmatch(item.base_asset) is None:
        raise EvaluationError("capture is unusable")
    if item.coin_id == "":
        raise EvaluationError("capture is unusable")
    close = session_close(session)
    _before_close(item.market_captured_at, close)
    _before_close(item.history_captured_at, close)
    if item.market_source_timestamp is not None:
        _before_close(item.market_source_timestamp, close)
    _history_agrees(session, item)
    _lane_scope(item)
    _unlock_provenance(item)


def _history_agrees(session: date, item: LaneFields) -> None:
    if item.history_open is None:
        if item.history_days is not None:
            raise EvaluationError("capture is unusable")
        return
    _clock(item.history_open)
    opened = int(item.history_open.timestamp() * 1000)
    if item.history_days != history_days(opened, session):
        raise EvaluationError("capture is unusable")


def _lane_scope(item: LaneFields) -> None:
    if item.lane is Lane.NORMAL:
        if item.unlock_state != "unavailable":
            raise EvaluationError("normal lane does not record an unlock schedule")
        return
    if item.market_cap_rank is not None or item.fdv_to_market_cap is not None:
        raise EvaluationError("high-risk lane does not record rank or fdv")


def _unlock_provenance(item: LaneFields) -> None:
    named = (item.unlock_provider, item.unlock_provider_id, item.unlock_source_timestamp)
    if item.unlock_state == "unavailable":
        if any(value is not None for value in named):
            raise EvaluationError("capture is unusable")
        return
    provider, provider_id, stamp = named
    if not isinstance(provider, str) or provider == "":
        raise EvaluationError("capture is unusable")
    if not isinstance(provider_id, str) or provider_id == "":
        raise EvaluationError("capture is unusable")
    if stamp is None:
        raise EvaluationError("undated fundamental")


def _before_close(moment: datetime, close: datetime) -> None:
    if moment.tzinfo is None or moment.utcoffset() != timedelta(0):
        raise EvaluationError("captured_at must be timezone-aware UTC")
    if moment > close:
        raise EvaluationError("capture is after the close")


def _manifest(root: Path, session: date) -> Path:
    return root / "sessions" / f"date={session.isoformat()}" / "manifest.json"


def _path(root: Path, session: date, symbol: str) -> Path:
    return root / "captures" / f"session={session.isoformat()}" / "lane" / f"symbol={symbol}.json"


def _body(session: date, item: LaneFields) -> bytes:
    document = {
        "session": session.isoformat(),
        "symbol": item.symbol,
        "base_asset": item.base_asset,
        "lane": item.lane.value,
        "coin_id": item.coin_id,
        "circulating_ratio": _decimal(item.circulating_ratio),
        "history_days": item.history_days,
        "history_open": _stamp(item.history_open),
        "history_provider": None if item.history_open is None else "binance",
        "market_cap_rank": item.market_cap_rank,
        "fdv_to_market_cap": _decimal(item.fdv_to_market_cap),
        "market_provider": "coingecko",
        "market_source_timestamp": _stamp(item.market_source_timestamp),
        "market_captured_at": _iso(item.market_captured_at),
        "history_captured_at": _iso(item.history_captured_at),
        "unlock_state": item.unlock_state,
        "unlock_provider": item.unlock_provider,
        "unlock_provider_id": item.unlock_provider_id,
        "unlock_source_timestamp": _stamp(item.unlock_source_timestamp),
    }
    return json.dumps(document, sort_keys=True).encode()


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _stamp(moment: datetime | None) -> str | None:
    return None if moment is None else _iso(moment)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _create(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() == body:
            return
        raise EvaluationError(f"capture {path.name} already exists with a different payload")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(body)
    try:
        os.link(temporary, path)
    except FileExistsError:
        if path.read_bytes() != body:
            raise EvaluationError(
                f"capture {path.name} already exists with a different payload"
            ) from None
    finally:
        temporary.unlink(missing_ok=True)
