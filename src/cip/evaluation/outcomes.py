"""Forward outcomes from stored decisions and later daily bars. No second scoring path."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal, cast

from cip.domain.errors import EvaluationError
from cip.evaluation.decision import DecisionRecord, Disposition, ForwardOutcome, decision_id
from cip.evaluation.store import append_outcome
from cip.history.bars import DailyBar

_HORIZONS = frozenset({7, 14, 30, 60})
_BTC = "BTCUSDT"
_ELIGIBLE = frozenset({Disposition.SCORED, Disposition.BUY})


def measure_outcome(
    *,
    record: DecisionRecord,
    horizon_days: int,
    as_of: datetime,
    bars: Sequence[DailyBar],
    btc_bars: Sequence[DailyBar],
    peers: Sequence[tuple[DecisionRecord, Sequence[DailyBar]]] = (),
) -> ForwardOutcome:
    """Measure one horizon. The decision's disposition is not restated or revised."""
    if as_of.tzinfo is None or as_of.utcoffset() != timedelta(0):
        raise EvaluationError("as_of must be timezone-aware UTC")
    if horizon_days not in _HORIZONS:
        raise EvaluationError("horizon must be 7, 14, 30, or 60 days")
    close = record.evaluated_at
    if (close.hour, close.minute, close.second, close.microsecond) != (0, 0, 0, 0):
        raise EvaluationError("decision time is not a daily close")
    if as_of < close + timedelta(days=horizon_days):
        raise EvaluationError("horizon has not elapsed")
    entry_day = close.date() - timedelta(days=1)
    end_day = entry_day + timedelta(days=horizon_days)
    window = _window(bars, record.symbol, entry_day, end_day)
    entry = window[0].close
    absolute = window[-1].close / entry - 1
    btc_window = _window(btc_bars, _BTC, entry_day, end_day)
    btc_return = btc_window[-1].close / btc_window[0].close - 1
    path = window[1:]
    favorable = max(bar.high / entry - 1 for bar in path)
    adverse = min(bar.low / entry - 1 for bar in path)
    price_time = close + timedelta(days=horizon_days)
    return ForwardOutcome(
        decision_id=decision_id(record),
        horizon_days=cast(Literal[7, 14, 30, 60], horizon_days),
        absolute_return=absolute,
        btc_return=btc_return,
        excess_return=absolute - btc_return,
        universe_relative_return=_relative(record, absolute, entry_day, end_day, peers),
        mfe=favorable if favorable > 0 else Decimal(0),
        mae=adverse if adverse < 0 else Decimal(0),
        price_timestamp=price_time,
        btc_price_timestamp=price_time,
    )


def record_forward(
    root: Path,
    record: DecisionRecord,
    *,
    horizon_days: int,
    as_of: datetime,
    bars: Sequence[DailyBar],
    btc_bars: Sequence[DailyBar],
    peers: Sequence[tuple[DecisionRecord, Sequence[DailyBar]]] = (),
) -> ForwardOutcome:
    """Write one outcome document. The decision file is left as it was."""
    outcome = measure_outcome(
        record=record,
        horizon_days=horizon_days,
        as_of=as_of,
        bars=bars,
        btc_bars=btc_bars,
        peers=peers,
    )
    append_outcome(root, outcome, as_of=as_of)
    return outcome


def _window(
    bars: Sequence[DailyBar], symbol: str, entry_day: date, end_day: date
) -> tuple[DailyBar, ...]:
    selected: dict[date, DailyBar] = {}
    for bar in bars:
        if bar.open_date < entry_day or bar.open_date > end_day:
            continue
        if bar.symbol != symbol:
            raise EvaluationError("bar symbol does not match the decision")
        if bar.open_date in selected:
            raise EvaluationError("duplicate bar date")
        _require_price(bar)
        selected[bar.open_date] = bar
    day = entry_day
    ordered: list[DailyBar] = []
    while day <= end_day:
        found = selected.get(day)
        if found is None:
            raise EvaluationError("horizon bars are not contiguous")
        ordered.append(found)
        day += timedelta(days=1)
    return tuple(ordered)


def _require_price(bar: DailyBar) -> None:
    for amount in (bar.open, bar.high, bar.low, bar.close):
        if type(amount) is not Decimal or not amount.is_finite() or amount <= 0:
            raise EvaluationError("invalid price")
    if bar.high < max(bar.low, bar.open, bar.close) or bar.low > min(bar.open, bar.close):
        raise EvaluationError("invalid price")


def _relative(
    record: DecisionRecord,
    absolute: Decimal,
    entry_day: date,
    end_day: date,
    peers: Sequence[tuple[DecisionRecord, Sequence[DailyBar]]],
) -> Decimal | None:
    returns: list[Decimal] = []
    if record.disposition in _ELIGIBLE:
        returns.append(absolute)
    seen = {record.symbol}
    for peer, peer_bars in peers:
        if peer.cohort != record.cohort or peer.evaluated_at != record.evaluated_at:
            continue
        if peer.disposition not in _ELIGIBLE:
            continue
        if peer.symbol in seen:
            raise EvaluationError("eligible universe repeats a symbol")
        seen.add(peer.symbol)
        peer_return = _peer_return(peer.symbol, peer_bars, entry_day, end_day)
        if peer_return is None:
            return None
        returns.append(peer_return)
    if not returns:
        return None
    mean = sum(returns, Decimal(0)) / Decimal(len(returns))
    return absolute - mean


def _peer_return(
    symbol: str,
    bars: Sequence[DailyBar],
    entry_day: date,
    end_day: date,
) -> Decimal | None:
    try:
        window = _window(bars, symbol, entry_day, end_day)
    except EvaluationError:
        return None
    return window[-1].close / window[0].close - 1
