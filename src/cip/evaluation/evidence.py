"""Build one weekly scorecard from stored records. Missing evidence stays missing."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError

from cip.domain.errors import ScorecardError
from cip.evaluation.decision import DecisionRecord, ForwardOutcome, decision_id
from cip.evaluation.scorecard import Scorecard, report_scorecard

_PORTFOLIO = (
    "btc_dca_excess",
    "btc_eth_dca_excess",
    "equal_weight_excess",
    "random_baseline_excess",
    "sharpe",
    "sortino",
    "exposure",
    "turnover",
    "fee_drag_usd",
)
_TRADE = frozenset({"decision_id", "realized_r", "mae", "mfe", "fees_usd", "slippage"})
_MONEY = frozenset({"realized_r", "mae", "mfe", "fees_usd", "slippage"})


def previous_sunday(as_of: datetime) -> date:
    """The Sunday on or before the UTC date. A Monday run names the Sunday that just ended."""
    if as_of.tzinfo is None or as_of.utcoffset() != timedelta(0):
        raise ScorecardError("as_of must be timezone-aware UTC")
    day = as_of.astimezone(UTC).date()
    return day - timedelta(days=(day.weekday() + 1) % 7)


def scorecard_from_evidence(
    *,
    decisions: Sequence[tuple[str, bytes]],
    outcomes: Sequence[bytes],
    trades: Sequence[bytes],
    portfolio: bytes | None,
) -> Scorecard:
    """Count stored decisions and attach only the outcomes and trades that name them."""
    parsed, counts = _decisions(decisions)
    signals = _signals(outcomes, parsed)
    trips = _trades(trades, parsed)
    comparisons = _portfolio(portfolio)
    return report_scorecard(
        decisions=counts["decisions"],
        reproducible=counts["reproducible"],
        policy_violations=counts["policy_violations"],
        stale_or_missing=counts["stale_or_missing"],
        replay_disagreements=counts["replay_disagreements"],
        invalid_decisions=counts["invalid_decisions"],
        signals=signals,
        trades=trips,
        portfolio=comparisons,
    )


def _decisions(
    files: Sequence[tuple[str, bytes]],
) -> tuple[dict[str, DecisionRecord], dict[str, int]]:
    counts = {
        "decisions": 0,
        "reproducible": 0,
        "policy_violations": 0,
        "stale_or_missing": 0,
        "replay_disagreements": 0,
        "invalid_decisions": 0,
    }
    parsed: dict[str, DecisionRecord] = {}
    for key, body in files:
        counts["decisions"] += 1
        record = _parse_decision(body)
        if record is None:
            counts["invalid_decisions"] += 1
            continue
        identity = decision_id(record)
        if identity in parsed:
            raise ScorecardError("decision id is ambiguous")
        parsed[identity] = record
        name = key.rsplit("/", 1)[-1]
        canonical = json.dumps(record.to_document(), sort_keys=True).encode()
        if name != f"{identity}.json" or body != canonical:
            counts["replay_disagreements"] += 1
        else:
            counts["reproducible"] += 1
        if "policy_violation" in record.reason_codes:
            counts["policy_violations"] += 1
        if any(_stale(code) for code in record.reason_codes):
            counts["stale_or_missing"] += 1
    return parsed, counts


def _stale(code: str) -> bool:
    return code.startswith("missing_") or code.startswith("stale")


def _parse_decision(body: bytes) -> DecisionRecord | None:
    document = _json(body)
    if not isinstance(document, dict):
        return None
    try:
        return DecisionRecord.from_document(document)
    except (KeyError, TypeError, ValueError, ValidationError):
        return None


def _signals(
    outcomes: Sequence[bytes], parsed: Mapping[str, DecisionRecord]
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for body in outcomes:
        outcome = _parse_outcome(body)
        record = parsed.get(outcome.decision_id)
        if record is None:
            raise ScorecardError("outcome has no stored decision")
        relative = outcome.universe_relative_return
        rows.append(
            {
                "decision_id": outcome.decision_id,
                "horizon_days": outcome.horizon_days,
                "absolute_return": outcome.absolute_return,
                "btc_return": outcome.btc_return,
                "excess_return": outcome.excess_return,
                "universe_relative_return": relative,
                "rank": record.rank,
                "coefficient": None,
            }
        )
    return rows


def _parse_outcome(body: bytes) -> ForwardOutcome:
    document = _json(body)
    if not isinstance(document, dict):
        raise ScorecardError("outcome is unusable")
    try:
        return ForwardOutcome.model_validate(document)
    except ValidationError as error:
        raise ScorecardError("outcome is unusable") from error


def _trades(
    trades: Sequence[bytes], parsed: Mapping[str, DecisionRecord]
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for body in trades:
        document = _object(body, "trade")
        if set(document) != _TRADE:
            raise ScorecardError("trade uses the stored fields")
        identity = document["decision_id"]
        if type(identity) is not str or identity not in parsed:
            raise ScorecardError("trade has no stored decision")
        row: dict[str, object] = {"decision_id": identity}
        for name in _MONEY:
            row[name] = _decimal(document[name], "trade")
        rows.append(row)
    return rows


def _portfolio(body: bytes | None) -> dict[str, object]:
    if body is None:
        return {name: None for name in _PORTFOLIO}
    document = _object(body, "portfolio")
    if set(document) != set(_PORTFOLIO):
        raise ScorecardError("portfolio results name each comparison")
    values: dict[str, object] = {}
    for name in _PORTFOLIO:
        item = document[name]
        values[name] = None if item is None else _decimal(item, "portfolio")
    return values


def _object(body: bytes, label: str) -> dict[str, Any]:
    document = _json(body)
    if not isinstance(document, dict):
        raise ScorecardError(f"{label} is unusable")
    return document


def _json(body: bytes) -> object:
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None


def _decimal(value: object, label: str) -> Decimal:
    if type(value) is not str:
        raise ScorecardError(f"{label} results are decimal strings")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ScorecardError(f"{label} results are decimal strings") from error
    if not parsed.is_finite() or (parsed.is_zero() and parsed.is_signed()):
        raise ScorecardError(f"{label} results are decimal strings")
    return parsed
