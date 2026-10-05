"""Report four assurance results. A report is not a grade."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, ValidationError, model_validator

from cip.domain.errors import ScorecardError

_DECISION = re.compile(r"^[0-9a-f]{64}$")
_HORIZONS = frozenset({7, 14, 30, 60})
_ONE = Decimal(1)
_SIGNAL_KEYS = frozenset(
    {
        "decision_id",
        "horizon_days",
        "absolute_return",
        "btc_return",
        "excess_return",
        "universe_relative_return",
        "rank",
        "coefficient",
    }
)
_TRADE_KEYS = frozenset({"decision_id", "realized_r", "mae", "mfe", "fees_usd", "slippage"})
_PORTFOLIO_KEYS = (
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
_COUNT_NAMES = (
    "reproducible",
    "policy_violations",
    "stale_or_missing",
    "replay_disagreements",
    "invalid_decisions",
)


def _exact_int(value: object) -> int:
    if type(value) is not int:
        raise ValueError("counts are integers")
    return value


def _exact_text(value: object) -> str:
    if type(value) is not str:
        raise ValueError("text values are strings")
    return value


def _exact_decimal(value: object) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise ValueError("results are Decimal")
    if value.is_zero() and value.is_signed():
        raise ValueError("a signed zero is not a result")
    return value


def _optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    return _exact_decimal(value)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return _exact_int(value)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Integrity(_Strict):
    """Counts from stored decisions. A missing count is not zero."""

    decisions: Annotated[int, BeforeValidator(_exact_int)]
    reproducible: Annotated[int, BeforeValidator(_exact_int)]
    policy_violations: Annotated[int, BeforeValidator(_exact_int)]
    stale_or_missing: Annotated[int, BeforeValidator(_exact_int)]
    replay_disagreements: Annotated[int, BeforeValidator(_exact_int)]
    invalid_decisions: Annotated[int, BeforeValidator(_exact_int)]

    @model_validator(mode="after")
    def _bounds(self) -> Integrity:
        counts = tuple(getattr(self, name) for name in _COUNT_NAMES)
        if self.decisions < 0 or any(count < 0 for count in counts):
            raise ValueError("counts are non-negative")
        if any(count > self.decisions for count in counts):
            raise ValueError("a count cannot exceed the decisions")
        return self


class SignalRow(_Strict):
    """One stored outcome. Excess is absolute minus BTC. There is no verdict."""

    decision_id: Annotated[str, BeforeValidator(_exact_text)]
    horizon_days: Annotated[int, BeforeValidator(_exact_int)]
    absolute_return: Annotated[Decimal, BeforeValidator(_exact_decimal)]
    btc_return: Annotated[Decimal, BeforeValidator(_exact_decimal)]
    excess_return: Annotated[Decimal, BeforeValidator(_exact_decimal)]
    universe_relative_return: Annotated[Decimal | None, BeforeValidator(_optional_decimal)]
    rank: Annotated[int | None, BeforeValidator(_optional_int)]
    coefficient: Annotated[Decimal | None, BeforeValidator(_optional_decimal)]

    @model_validator(mode="after")
    def _excess(self) -> SignalRow:
        if _DECISION.fullmatch(self.decision_id) is None:
            raise ValueError("decision id must be a sha256")
        if self.horizon_days not in _HORIZONS:
            raise ValueError("horizon must be 7, 14, 30, or 60 days")
        if self.excess_return != self.absolute_return - self.btc_return:
            raise ValueError("excess return is the asset return minus the BTC return")
        if self.rank is not None and self.rank < 1:
            raise ValueError("rank starts at 1")
        if self.coefficient is not None and (self.coefficient < -_ONE or self.coefficient > _ONE):
            raise ValueError("a coefficient is not a score")
        return self


class TradeRow(_Strict):
    """One closed round trip supplied by the caller. There is no order id."""

    decision_id: Annotated[str, BeforeValidator(_exact_text)]
    realized_r: Annotated[Decimal, BeforeValidator(_exact_decimal)]
    mae: Annotated[Decimal, BeforeValidator(_exact_decimal)]
    mfe: Annotated[Decimal, BeforeValidator(_exact_decimal)]
    fees_usd: Annotated[Decimal, BeforeValidator(_exact_decimal)]
    slippage: Annotated[Decimal, BeforeValidator(_exact_decimal)]

    @model_validator(mode="after")
    def _shape(self) -> TradeRow:
        if _DECISION.fullmatch(self.decision_id) is None:
            raise ValueError("decision id must be a sha256")
        if self.mae > 0 or self.mfe < 0:
            raise ValueError("MAE is at most zero and MFE is at least zero")
        if self.fees_usd < 0 or self.slippage < 0:
            raise ValueError("costs are non-negative")
        return self


class PortfolioResults(_Strict):
    """Stored comparisons. A missing figure stays missing. It is not zero."""

    btc_dca_excess: Annotated[Decimal | None, BeforeValidator(_optional_decimal)]
    btc_eth_dca_excess: Annotated[Decimal | None, BeforeValidator(_optional_decimal)]
    equal_weight_excess: Annotated[Decimal | None, BeforeValidator(_optional_decimal)]
    random_baseline_excess: Annotated[Decimal | None, BeforeValidator(_optional_decimal)]
    sharpe: Annotated[Decimal | None, BeforeValidator(_optional_decimal)]
    sortino: Annotated[Decimal | None, BeforeValidator(_optional_decimal)]
    exposure: Annotated[Decimal | None, BeforeValidator(_optional_decimal)]
    turnover: Annotated[Decimal | None, BeforeValidator(_optional_decimal)]
    fee_drag_usd: Annotated[Decimal | None, BeforeValidator(_optional_decimal)]


class Scorecard(_Strict):
    """Four results, kept separate. There is no grade and no order id."""

    decision_integrity: Integrity
    signal_quality: tuple[SignalRow, ...]
    trade_status: Literal["no_trades", "reported"]
    trade_quality: tuple[TradeRow, ...]
    portfolio_quality: PortfolioResults

    @model_validator(mode="after")
    def _separate(self) -> Scorecard:
        seen: set[tuple[str, int]] = set()
        for row in self.signal_quality:
            key = (row.decision_id, row.horizon_days)
            if key in seen:
                raise ValueError("a decision has one outcome per horizon")
            seen.add(key)
        if self.trade_status == "no_trades" and self.trade_quality:
            raise ValueError("no trades have no round trips")
        if self.trade_status == "reported" and not self.trade_quality:
            raise ValueError("reported trades list the round trips")
        return self

    def to_document(self) -> dict[str, Any]:
        try:
            checked = Scorecard.model_validate(self.model_dump())
        except ValidationError as error:
            raise ScorecardError("scorecard is invalid") from error
        return {
            "decision_integrity": {
                "decisions": checked.decision_integrity.decisions,
                "reproducible": checked.decision_integrity.reproducible,
                "policy_violations": checked.decision_integrity.policy_violations,
                "stale_or_missing": checked.decision_integrity.stale_or_missing,
                "replay_disagreements": checked.decision_integrity.replay_disagreements,
                "invalid_decisions": checked.decision_integrity.invalid_decisions,
            },
            "signal_quality": [_signal_document(row) for row in checked.signal_quality],
            "trade_quality": {
                "status": checked.trade_status,
                "trades": [_trade_document(row) for row in checked.trade_quality],
            },
            "portfolio_quality": {
                name: _plain(getattr(checked.portfolio_quality, name)) for name in _PORTFOLIO_KEYS
            },
        }


def report_scorecard(
    *,
    decisions: int,
    reproducible: int,
    policy_violations: int,
    stale_or_missing: int,
    replay_disagreements: int,
    invalid_decisions: int,
    signals: Sequence[Mapping[str, object]],
    trades: Sequence[Mapping[str, object]],
    portfolio: Mapping[str, object],
) -> Scorecard:
    """Assemble stored results. The same loss is not one verdict."""
    rows = _rows(signals, "signals", _SIGNAL_KEYS)
    trips = _rows(trades, "trades", _TRADE_KEYS)
    comparisons = _portfolio(portfolio)
    try:
        return Scorecard(
            decision_integrity=Integrity(
                decisions=decisions,
                reproducible=reproducible,
                policy_violations=policy_violations,
                stale_or_missing=stale_or_missing,
                replay_disagreements=replay_disagreements,
                invalid_decisions=invalid_decisions,
            ),
            signal_quality=tuple(SignalRow.model_validate(row) for row in rows),
            trade_status="reported" if trips else "no_trades",
            trade_quality=tuple(TradeRow.model_validate(row) for row in trips),
            portfolio_quality=PortfolioResults.model_validate(comparisons),
        )
    except ValidationError as error:
        raise ScorecardError("scorecard is invalid") from error


def _rows(items: object, label: str, keys: frozenset[str]) -> tuple[Mapping[str, object], ...]:
    if isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
        raise ScorecardError(f"{label} are a list")
    rows: list[Mapping[str, object]] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise ScorecardError(f"{label} are stored records")
        if set(item) != keys:
            raise ScorecardError(f"{label} use the stored fields")
        rows.append(item)
    return tuple(rows)


def _portfolio(portfolio: object) -> dict[str, object]:
    if not isinstance(portfolio, Mapping):
        raise ScorecardError("portfolio results are a record")
    if set(portfolio) != set(_PORTFOLIO_KEYS):
        raise ScorecardError("portfolio results name each comparison")
    return dict(portfolio)


def _signal_document(row: SignalRow) -> dict[str, Any]:
    return {
        "decision_id": row.decision_id,
        "horizon_days": row.horizon_days,
        "absolute_return": format(row.absolute_return, "f"),
        "btc_return": format(row.btc_return, "f"),
        "excess_return": format(row.excess_return, "f"),
        "universe_relative_return": _plain(row.universe_relative_return),
        "rank": row.rank,
        "coefficient": _plain(row.coefficient),
    }


def _trade_document(row: TradeRow) -> dict[str, Any]:
    return {
        "decision_id": row.decision_id,
        "realized_r": format(row.realized_r, "f"),
        "mae": format(row.mae, "f"),
        "mfe": format(row.mfe, "f"),
        "fees_usd": format(row.fees_usd, "f"),
        "slippage": format(row.slippage, "f"),
    }


def _plain(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")
