"""Judge whether one discovery entry is allowed. A refusal is not an order."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from cip.domain.errors import LimitError
from cip.domain.policy import LoadedPolicy

_SYMBOL = re.compile(r"^[A-Z0-9]{1,20}$")
Reason = Literal[
    "review_and_flatten",
    "halt_new_entries",
    "loss_streak",
    "monthly_loss",
    "open_position_cap",
    "sector_cap",
    "beta_exposure",
    "averaging_down",
    "reentry_cooldown",
    "no_size",
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class DiscoveryLine(_Strict):
    """One open discovery position the caller already knows about."""

    symbol: str
    sector: str
    beta: Decimal
    value_usd: Decimal


class LimitDecision(_Strict):
    """Allowed, or the reasons it is not. There is no order id."""

    allowed: bool
    flatten: bool
    reasons: tuple[Reason, ...]

    @model_validator(mode="after")
    def _matches_reasons(self) -> Self:
        if self.allowed is not (len(self.reasons) == 0):
            raise ValueError("an admission is allowed only with no reason")
        if self.flatten is not ("review_and_flatten" in self.reasons):
            raise ValueError("flatten is the review drawdown")
        return self

    def to_document(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "flatten": self.flatten,
            "reasons": list(self.reasons),
        }


def admit_entry(
    *,
    portfolio_usd: Decimal,
    symbol: str,
    sector: str,
    beta: Decimal,
    size_usd: Decimal | None,
    positions: tuple[DiscoveryLine, ...],
    drawdown: Decimal,
    consecutive_losses: int,
    days_since_last_loss: int | None,
    monthly_realized_loss_pct: Decimal,
    days_since_symbol_exit: int | None,
    policy: LoadedPolicy,
) -> LimitDecision:
    """Apply the published caps and halts. The caller supplies every fact."""
    portfolio = _money(portfolio_usd, "portfolio", positive=True)
    name = _symbol(symbol)
    group = _sector(sector)
    measured_beta = _money(beta, "beta", positive=False)
    size = _optional_money(size_usd)
    sleeve_drawdown = _money(drawdown, "drawdown", positive=False)
    if sleeve_drawdown < 0:
        raise LimitError("drawdown cannot be negative")
    losses = _count(consecutive_losses, "losses")
    since_loss = _optional_count(days_since_last_loss, "days since the last loss")
    month_loss = _money(monthly_realized_loss_pct, "monthly loss", positive=False)
    if month_loss < 0:
        raise LimitError("monthly loss cannot be negative")
    since_exit = _optional_count(days_since_symbol_exit, "days since the exit")
    book = _book(positions)

    limits = policy.policy.hypotheses.limits
    breakers = policy.policy.hypotheses.circuit_breakers
    reasons: list[Reason] = []
    if sleeve_drawdown >= _policy_number(breakers.review_and_flatten_drawdown):
        reasons.append("review_and_flatten")
    if sleeve_drawdown >= _policy_number(breakers.halt_new_entries_drawdown):
        reasons.append("halt_new_entries")
    if losses >= breakers.consecutive_losses and (
        since_loss is None or since_loss < breakers.pause_days
    ):
        reasons.append("loss_streak")
    if month_loss >= _policy_number(breakers.monthly_realized_loss_pct_portfolio):
        reasons.append("monthly_loss")
    if len(book) >= limits.max_open_discovery_positions:
        reasons.append("open_position_cap")
    if sum(1 for line in book if line.sector == group) >= limits.max_positions_per_sector:
        reasons.append("sector_cap")
    weighted = sum((line.beta * line.value_usd for line in book), Decimal(0))
    if size is not None:
        weighted += measured_beta * size
    if weighted > portfolio * _policy_number(limits.beta_weighted_discovery_exposure_max):
        reasons.append("beta_exposure")
    if any(line.symbol == name for line in book):
        reasons.append("averaging_down")
    if since_exit is not None and since_exit < breakers.same_asset_reentry_cooldown_days:
        reasons.append("reentry_cooldown")
    if size is None:
        reasons.append("no_size")
    return LimitDecision(
        allowed=not reasons,
        flatten="review_and_flatten" in reasons,
        reasons=tuple(reasons),
    )


def _book(positions: object) -> tuple[DiscoveryLine, ...]:
    if type(positions) is not tuple:
        raise LimitError("open positions are a tuple")
    seen: set[str] = set()
    book: list[DiscoveryLine] = []
    for line in positions:
        if type(line) is not DiscoveryLine:
            raise LimitError("open positions are discovery lines")
        _symbol(line.symbol)
        _sector(line.sector)
        if line.symbol in seen:
            raise LimitError("duplicate discovery symbols")
        seen.add(line.symbol)
        book.append(line)
    return tuple(book)


def _policy_number(value: float) -> Decimal:
    return Decimal(str(value))


def _money(value: object, label: str, *, positive: bool) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise LimitError(f"{label} must be a decimal")
    if positive and value <= 0:
        raise LimitError(f"{label} must be positive")
    return value


def _optional_money(value: object) -> Decimal | None:
    if value is None:
        return None
    return _money(value, "size", positive=True)


def _count(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise LimitError(f"{label} must be a count of days or losses")
    return value


def _optional_count(value: object, label: str) -> int | None:
    if value is None:
        return None
    return _count(value, label)


def _symbol(value: object) -> str:
    if type(value) is not str or _SYMBOL.fullmatch(value) is None:
        raise LimitError("symbol is a Binance name")
    return value


def _sector(value: object) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise LimitError("sector is required")
    return value
