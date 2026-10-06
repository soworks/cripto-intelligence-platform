"""Capture the next closed session before and after its close.

Sessions through 2026-10-05 stay blocked. This module does not call a provider
and it does not run the daily scan.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from cip.adapters.market import ExchangeInfo
from cip.domain.errors import EvaluationError, RecorderError
from cip.evaluation.populate import (
    AbsenceCapture,
    BarCapture,
    CaptureClock,
    PacketCapture,
    RegimeCapture,
    SessionCaptures,
    UniverseCapture,
)
from cip.evaluation.scan import ScanCandidate
from cip.evaluation.session import session_close
from cip.history.bars import DailyBar
from cip.recorders.observation import Observation

BLOCKED_THROUGH = date(2026, 10, 5)
_SYMBOL = re.compile(r"^[A-Z0-9]{1,20}$")


def prospective_session(session: date) -> date:
    """Refuse a session whose blocked result is already evidence."""
    if type(session) is not date:
        raise EvaluationError("session is a date")
    if session <= BLOCKED_THROUGH:
        raise EvaluationError("historical session stays blocked")
    return session


def trading_usdt_symbols(info: ExchangeInfo) -> tuple[str, ...]:
    """The exchange's trading USDT names. This is not a research listing."""
    found: list[str] = []
    for item in info.symbols:
        if item.status != "TRADING" or item.quote_asset != "USDT":
            continue
        if _SYMBOL.fullmatch(item.symbol) is None:
            continue
        found.append(item.symbol)
    if len(found) != len(set(found)):
        raise EvaluationError("universe snapshot repeats a symbol")
    return tuple(found)


def universe_capture(
    session: date,
    info: ExchangeInfo,
    observed_at: datetime,
    retrieved_at: datetime,
) -> UniverseCapture:
    """A pre-close snapshot. Both clocks must be at or before the close."""
    prospective_session(session)
    close = session_close(session)
    _before_close(observed_at, close, "observed_at")
    _before_close(retrieved_at, close, "captured_at")
    symbols = trading_usdt_symbols(info)
    if not symbols:
        raise EvaluationError("universe snapshot is empty")
    return UniverseCapture(
        symbols,
        observed_at,
        CaptureClock("binance/exchangeInfo", observed_at, retrieved_at),
    )


def history_capture(
    session: date,
    symbol: str,
    bars: tuple[DailyBar, ...],
    retrieved_at: datetime,
) -> BarCapture:
    """Bars whose periods have already ended. The session bar is not among them."""
    prospective_session(session)
    close = session_close(session)
    _aware(retrieved_at, "captured_at")
    if retrieved_at > close:
        raise EvaluationError("capture is after the close")
    if not bars:
        raise EvaluationError("history is empty")
    if any(bar.open_date >= session for bar in bars):
        raise EvaluationError("session bar was retrieved before it closed")
    period_end = session_close(max(bar.open_date for bar in bars))
    if retrieved_at < period_end:
        raise EvaluationError("bar was retrieved before it closed")
    return BarCapture(symbol, bars, CaptureClock("binance/klines", period_end, retrieved_at))


def session_bar_capture(
    session: date,
    symbol: str,
    bars: tuple[DailyBar, ...],
    retrieved_at: datetime,
) -> BarCapture:
    """The completed session bar, retrieved once its period has ended."""
    prospective_session(session)
    close = session_close(session)
    _aware(retrieved_at, "captured_at")
    if retrieved_at < close:
        raise EvaluationError("session bar was retrieved before it closed")
    if any(bar.open_date > session for bar in bars):
        raise EvaluationError("capture is after the close")
    if not any(bar.open_date == session for bar in bars):
        raise EvaluationError("session bar is missing")
    return BarCapture(symbol, bars, CaptureClock("binance/klines", close, retrieved_at))


def candidate_absence(
    session: date,
    symbol: str,
    produced_at: datetime,
    source: str,
    *,
    looked: bool,
) -> AbsenceCapture:
    """An absence is a lookup that found nothing. A skipped lookup is not one."""
    prospective_session(session)
    if not looked:
        raise EvaluationError("candidate absence requires a lookup")
    if source == "":
        raise EvaluationError("source is required")
    return AbsenceCapture(symbol, produced_at, source)


def _before_close(moment: datetime, close: datetime, label: str) -> None:
    _aware(moment, label)
    if moment > close:
        raise EvaluationError("capture is after the close")


def _aware(moment: datetime, label: str) -> None:
    if moment.tzinfo is None or moment.utcoffset() != timedelta(0):
        raise EvaluationError(f"{label} must be timezone-aware UTC")


def store_universe(root: Path, session: date, capture: UniverseCapture) -> None:
    """Store a pre-close universe snapshot. A later different snapshot is refused."""
    _stored_universe(session, capture)
    _create(_universe_path(root, session), _universe_body(session, capture))


def store_history(root: Path, session: date, capture: BarCapture) -> None:
    """Store bars whose periods have ended. The session bar is not in this file."""
    checked = history_capture(session, capture.symbol, capture.bars, capture.clock.captured_at)
    if capture != checked:
        raise EvaluationError("history capture does not match its bars")
    _create(_history_path(root, session, capture.symbol), _bar_body(session, checked))


def store_completed_bars(root: Path, session: date, capture: BarCapture) -> None:
    """Store the completed session bar. Its retrieval is at or after the close."""
    checked = session_bar_capture(session, capture.symbol, capture.bars, capture.clock.captured_at)
    if capture != checked:
        raise EvaluationError("session bar capture does not match its bars")
    _create(_final_path(root, session, capture.symbol), _bar_body(session, checked))


def store_packet(root: Path, session: date, capture: PacketCapture) -> None:
    """Store one looked-up candidate. A missing fundamental stays missing."""
    prospective_session(session)
    close = session_close(session)
    _ticker(capture.symbol)
    if capture.candidate.facts.symbol != capture.symbol:
        raise EvaluationError("candidate symbol does not match the packet")
    _before_close(capture.clock.captured_at, close, "captured_at")
    _value(capture.clock.source_timestamp, close)
    _before_close(capture.candidate.market.as_of, close, "market clock")
    gecko = capture.candidate.coingecko
    cmc = capture.candidate.cmc
    _dated_cap(gecko.market_cap_usd, gecko.source_timestamp)
    _dated_cap(cmc.market_cap_usd, cmc.source_timestamp)
    if capture.clock.source == "":
        raise EvaluationError("source is required")
    _create(_packet_path(root, session, capture.symbol), _packet_body(session, capture))


def store_absence(
    root: Path,
    session: date,
    kind: Literal["candidate", "daily_bar"],
    capture: AbsenceCapture,
) -> None:
    """Store an absence only after the producer looked."""
    checked = candidate_absence(
        session, capture.symbol, capture.produced_at, capture.source, looked=True
    )
    _ticker(capture.symbol)
    _create(
        _absence_path(root, session, kind, capture.symbol),
        _absence_body(session, kind, checked),
    )


def store_regime(root: Path, session: date, capture: RegimeCapture) -> None:
    """Store one regime series observed on the session date, before the close."""
    _checked_regime(session, capture)
    _create(_regime_path(root, session, capture.observation.series), _regime_body(session, capture))


def _checked_regime(session: date, capture: RegimeCapture) -> None:
    prospective_session(session)
    close = session_close(session)
    observation = capture.observation
    if observation.series not in ("btc_dominance", "stablecoin_supply"):
        raise EvaluationError("regime series is not a readiness input")
    _before_close(capture.captured_at, close, "captured_at")
    _before_close(observation.observed_at, close, "observed_at")
    if observation.observed_at.astimezone(UTC).date() != session:
        raise EvaluationError("regime observation is for a different session")
    _value(observation.source_timestamp, close)


def load_pre_close(root: Path, session: date) -> SessionCaptures:
    """Pre-close captures. A completed session bar stored for later is not included."""
    prospective_session(session)
    return _load(root, session, include_final=False)


def load_closed(root: Path, session: date) -> SessionCaptures:
    """Pre-close captures plus completed bars retrieved at or after the close."""
    prospective_session(session)
    return _load(root, session, include_final=True)


def _stored_universe(session: date, capture: UniverseCapture) -> None:
    prospective_session(session)
    close = session_close(session)
    _before_close(capture.observed_at, close, "observed_at")
    _before_close(capture.clock.captured_at, close, "captured_at")
    _value(capture.clock.source_timestamp, close)
    if capture.clock.source == "":
        raise EvaluationError("source is required")
    if not capture.symbols:
        raise EvaluationError("universe snapshot is empty")
    if len(capture.symbols) != len(set(capture.symbols)):
        raise EvaluationError("universe snapshot repeats a symbol")
    for symbol in capture.symbols:
        _ticker(symbol)


def _value(moment: datetime | None, close: datetime) -> None:
    if moment is None:
        return
    _before_close(moment, close, "source_timestamp")


def _dated_cap(cap: Decimal | None, stamp: datetime | None) -> None:
    if cap is not None and stamp is None:
        raise EvaluationError("undated fundamental")


def _load(root: Path, session: date, *, include_final: bool) -> SessionCaptures:
    universe = _load_universe(root, session)
    history = _load_bars(root, session, _history_dir(root, session), completed=False)
    final: dict[str, BarCapture] = {}
    if include_final:
        final = _load_bars(root, session, _final_dir(root, session), completed=True)
    bars = tuple(final.get(symbol, series) for symbol, series in history.items())
    bars += tuple(series for symbol, series in final.items() if symbol not in history)
    packets = _load_packets(root, session)
    candidate_absences, bar_absences = _load_absences(root, session)
    regime = _load_regime(root, session)
    return SessionCaptures(universe, packets, candidate_absences, bars, bar_absences, regime, ())


def _load_universe(root: Path, session: date) -> UniverseCapture | None:
    path = _universe_path(root, session)
    if not path.is_file():
        return None
    document = _object(path)
    if not isinstance(document, dict):
        raise EvaluationError("capture is unusable")
    try:
        raw_symbols = document["symbols"]
        observed_at = _moment(document["observed_at"])
        captured_at = _moment(document["captured_at"])
        source = document["source"]
        raw_source = document["source_timestamp"]
        if not isinstance(source, str) or not isinstance(raw_symbols, list):
            raise TypeError
        if not all(isinstance(symbol, str) for symbol in raw_symbols):
            raise TypeError
        symbols = tuple(raw_symbols)
    except (KeyError, TypeError, ValueError) as error:
        raise EvaluationError("capture is unusable") from error
    source_timestamp = None if raw_source is None else _moment(raw_source)
    capture = UniverseCapture(
        symbols,
        observed_at,
        CaptureClock(source, source_timestamp, captured_at),
    )
    _stored_universe(session, capture)
    if document.get("session") != session.isoformat():
        raise EvaluationError("capture is unusable")
    return capture


def _load_bars(
    root: Path, session: date, directory: Path, *, completed: bool
) -> dict[str, BarCapture]:
    del root
    found: dict[str, BarCapture] = {}
    for path in _files(directory):
        symbol = _symbol_name(path)
        document = _object(path)
        found[symbol] = _bars_from(session, symbol, document, completed=completed)
    return found


def _bars_from(session: date, symbol: str, document: object, *, completed: bool) -> BarCapture:
    if not isinstance(document, dict):
        raise EvaluationError("capture is unusable")
    try:
        rows = document["bars"]
        retrieved_at = _moment(document["captured_at"])
        if not isinstance(rows, list) or document.get("symbol") != symbol:
            raise TypeError
        bars = tuple(_bar_from(row) for row in rows)
    except (KeyError, TypeError, ValueError) as error:
        raise EvaluationError("capture is unusable") from error
    if completed:
        return session_bar_capture(session, symbol, bars, retrieved_at)
    return history_capture(session, symbol, bars, retrieved_at)


def _bar_from(row: object) -> DailyBar:
    if not isinstance(row, dict):
        raise EvaluationError("capture is unusable")
    try:
        return DailyBar(
            symbol=row["symbol"],
            open_date=date.fromisoformat(row["open_date"]),
            open=Decimal(row["open"]),
            high=Decimal(row["high"]),
            low=Decimal(row["low"]),
            close=Decimal(row["close"]),
            volume=Decimal(row["volume"]),
            quote_volume=Decimal(row["quote_volume"]),
            trade_count=row["trade_count"],
            taker_buy_base_volume=Decimal(row["taker_buy_base_volume"]),
            taker_buy_quote_volume=Decimal(row["taker_buy_quote_volume"]),
        )
    except (KeyError, TypeError, ValueError, ArithmeticError) as error:
        raise EvaluationError("capture is unusable") from error


def _load_packets(root: Path, session: date) -> tuple[PacketCapture, ...]:
    found: list[PacketCapture] = []
    for path in _files(_packet_dir(root, session)):
        symbol = _symbol_name(path)
        document = _object(path)
        if not isinstance(document, dict):
            raise EvaluationError("capture is unusable")
        try:
            candidate = ScanCandidate.model_validate(_clocks(document["candidate"]))
            raw_stamp = document["source_timestamp"]
            clock = CaptureClock(
                str(document["source"]),
                None if raw_stamp is None else _moment(raw_stamp),
                _moment(document["captured_at"]),
            )
        except (KeyError, TypeError, ValueError, ValidationError) as error:
            raise EvaluationError("capture is unusable") from error
        capture = PacketCapture(symbol, candidate, clock)
        if candidate.facts.symbol != symbol:
            raise EvaluationError("capture is unusable")
        found.append(capture)
    return tuple(found)


def _load_absences(
    root: Path, session: date
) -> tuple[tuple[AbsenceCapture, ...], tuple[AbsenceCapture, ...]]:
    candidates: list[AbsenceCapture] = []
    bars: list[AbsenceCapture] = []
    directory = root / "captures" / f"session={session.isoformat()}" / "absences"
    if not directory.exists():
        return (), ()
    if not directory.is_dir():
        raise EvaluationError("capture is unusable")
    for kind_dir in sorted(path for path in directory.iterdir() if not path.name.startswith(".")):
        if not kind_dir.is_dir():
            raise EvaluationError("capture is unusable")
        kind = kind_dir.name.removeprefix("input=")
        if kind not in ("candidate", "daily_bar") or kind_dir.name != f"input={kind}":
            raise EvaluationError("capture is unusable")
        for path in _files(kind_dir):
            symbol = _symbol_name(path)
            document = _object(path)
            if not isinstance(document, dict):
                raise EvaluationError("capture is unusable")
            try:
                capture = AbsenceCapture(
                    symbol, _moment(document["produced_at"]), str(document["source"])
                )
            except (KeyError, TypeError, ValueError) as error:
                raise EvaluationError("capture is unusable") from error
            if document.get("input") != kind or document.get("symbol") != symbol:
                raise EvaluationError("capture is unusable")
            if kind == "candidate":
                candidates.append(capture)
            else:
                bars.append(capture)
    return tuple(candidates), tuple(bars)


def _load_regime(root: Path, session: date) -> tuple[RegimeCapture, ...]:
    found: list[RegimeCapture] = []
    for path in _files(_regime_dir(root, session)):
        series = path.name.removesuffix(".json")
        document = _object(path)
        if not isinstance(document, dict) or document.get("series") != series:
            raise EvaluationError("capture is unusable")
        try:
            values = document["values"]
            units = document["units"]
            if not isinstance(values, dict) or not isinstance(units, dict):
                raise TypeError
            symbol = document["symbol"]
            observation = Observation(
                series=series,
                provider=str(document["provider"]),
                source_timestamp=None
                if document["source_timestamp"] is None
                else _moment(document["source_timestamp"]),
                observed_at=_moment(document["observed_at"]),
                symbol=None if symbol is None else str(symbol),
                values=tuple((str(name), Decimal(str(value))) for name, value in values.items()),
                units=tuple((str(name), str(unit)) for name, unit in units.items()),
            )
            capture = RegimeCapture(observation, _moment(document["captured_at"]))
        except (KeyError, TypeError, ValueError, ArithmeticError, RecorderError) as error:
            raise EvaluationError("capture is unusable") from error
        if series not in ("btc_dominance", "stablecoin_supply"):
            raise EvaluationError("capture is unusable")
        _checked_regime(session, capture)
        found.append(capture)
    return tuple(found)


def _symbol_name(path: Path) -> str:
    prefix = "symbol="
    if not path.name.startswith(prefix) or not path.name.endswith(".json"):
        raise EvaluationError("capture is unusable")
    symbol = path.name[len(prefix) : -len(".json")]
    return _ticker(symbol)


def _files(directory: Path) -> tuple[Path, ...]:
    if not directory.exists():
        return ()
    if not directory.is_dir():
        raise EvaluationError("capture is unusable")
    found: list[Path] = []
    for path in sorted(directory.iterdir()):
        if path.name.startswith("."):
            continue
        if not path.is_file():
            raise EvaluationError("capture is unusable")
        found.append(path)
    return tuple(found)


def _clocks(value: object) -> object:
    if isinstance(value, dict):
        return {key: _clocks(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clocks(item) for item in value]
    if isinstance(value, str) and value.endswith("Z") and "T" in value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    return value


def _object(path: Path) -> object:
    try:
        return json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise EvaluationError("capture is unusable") from error


def _moment(value: object) -> datetime:
    if not isinstance(value, str):
        raise EvaluationError("capture is unusable")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise EvaluationError("capture is unusable") from error
    _aware(parsed, "captured_at")
    return parsed.astimezone(UTC)


def _universe_body(session: date, capture: UniverseCapture) -> bytes:
    return _body(
        {
            "captured_at": _iso(capture.clock.captured_at),
            "observed_at": _iso(capture.observed_at),
            "session": session.isoformat(),
            "source": capture.clock.source,
            "source_timestamp": _stamp(capture.clock.source_timestamp),
            "symbols": list(capture.symbols),
        }
    )


def _bar_body(session: date, capture: BarCapture) -> bytes:
    return _body(
        {
            "bars": [_bar_row(bar) for bar in capture.bars],
            "captured_at": _iso(capture.clock.captured_at),
            "session": session.isoformat(),
            "source": capture.clock.source,
            "source_timestamp": _stamp(capture.clock.source_timestamp),
            "symbol": capture.symbol,
        }
    )


def _bar_row(bar: DailyBar) -> dict[str, object]:
    return {
        "close": format(bar.close, "f"),
        "high": format(bar.high, "f"),
        "low": format(bar.low, "f"),
        "open": format(bar.open, "f"),
        "open_date": bar.open_date.isoformat(),
        "quote_volume": format(bar.quote_volume, "f"),
        "symbol": bar.symbol,
        "taker_buy_base_volume": format(bar.taker_buy_base_volume, "f"),
        "taker_buy_quote_volume": format(bar.taker_buy_quote_volume, "f"),
        "trade_count": bar.trade_count,
        "volume": format(bar.volume, "f"),
    }


def _packet_body(session: date, capture: PacketCapture) -> bytes:
    return _body(
        {
            "candidate": capture.candidate.model_dump(mode="json"),
            "captured_at": _iso(capture.clock.captured_at),
            "session": session.isoformat(),
            "source": capture.clock.source,
            "source_timestamp": _stamp(capture.clock.source_timestamp),
            "symbol": capture.symbol,
        }
    )


def _absence_body(session: date, kind: str, capture: AbsenceCapture) -> bytes:
    return _body(
        {
            "input": kind,
            "produced_at": _iso(capture.produced_at),
            "session": session.isoformat(),
            "source": capture.source,
            "symbol": capture.symbol,
        }
    )


def _regime_body(session: date, capture: RegimeCapture) -> bytes:
    observation = capture.observation
    return _body(
        {
            "captured_at": _iso(capture.captured_at),
            "observed_at": _iso(observation.observed_at),
            "provider": observation.provider,
            "series": observation.series,
            "session": session.isoformat(),
            "source_timestamp": _stamp(observation.source_timestamp),
            "symbol": observation.symbol,
            "units": dict(observation.units),
            "values": {name: format(value, "f") for name, value in observation.values},
        }
    )


def _body(document: dict[str, object]) -> bytes:
    return json.dumps(document, sort_keys=True).encode()


def _stamp(moment: datetime | None) -> str | None:
    return None if moment is None else _iso(moment)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _ticker(symbol: str) -> str:
    if _SYMBOL.fullmatch(symbol) is None:
        raise EvaluationError("symbol must be 1 to 20 uppercase letters or digits")
    return symbol


def _universe_path(root: Path, session: date) -> Path:
    return root / "captures" / f"session={session.isoformat()}" / "universe.json"


def _history_path(root: Path, session: date, symbol: str) -> Path:
    return _history_dir(root, session) / f"symbol={symbol}.json"


def _history_dir(root: Path, session: date) -> Path:
    return root / "captures" / f"session={session.isoformat()}" / "history"


def _final_path(root: Path, session: date, symbol: str) -> Path:
    return _final_dir(root, session) / f"symbol={symbol}.json"


def _final_dir(root: Path, session: date) -> Path:
    return root / "captures" / f"session={session.isoformat()}" / "final"


def _packet_path(root: Path, session: date, symbol: str) -> Path:
    return _packet_dir(root, session) / f"symbol={symbol}.json"


def _packet_dir(root: Path, session: date) -> Path:
    return root / "captures" / f"session={session.isoformat()}" / "candidates"


def _absence_path(root: Path, session: date, kind: str, symbol: str) -> Path:
    return (
        root
        / "captures"
        / f"session={session.isoformat()}"
        / "absences"
        / f"input={kind}"
        / f"symbol={symbol}.json"
    )


def _regime_path(root: Path, session: date, series: str) -> Path:
    return _regime_dir(root, session) / f"{series}.json"


def _regime_dir(root: Path, session: date) -> Path:
    return root / "captures" / f"session={session.isoformat()}" / "regime"


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
