"""Name an exit from stored prices and the policy. This does not fill."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cip.domain.errors import ExitError
from cip.domain.policy import ExitHypotheses
from cip.portfolio.position import PositionState

_OPEN = frozenset({PositionState.OPEN, PositionState.PARTIAL_EXIT})


class ExitRule(StrEnum):
    FORCED = "forced"
    INITIAL_STOP = "initial_stop"
    CHANDELIER = "chandelier"
    MAX_HOLDING = "max_holding"
    TIME_STOP = "time_stop"
    PARTIAL_TAKE_PROFIT = "partial_take_profit"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class NamedExit(_Strict):
    """The rule that fired. It has no price, quantity, or order id."""

    rule: ExitRule
    reason: str = Field(min_length=1)
    triggers: tuple[str, ...] = ()

    def to_document(self) -> dict[str, Any]:
        return {"rule": self.rule.value, "reason": self.reason, "triggers": list(self.triggers)}


def name_exit(
    *,
    state: PositionState,
    entry_price: Decimal,
    mark_price: Decimal,
    highest_price: Decimal,
    atr: Decimal,
    days_held: int,
    return_vs_btc: Decimal,
    observed_triggers: frozenset[str],
    partial_already_taken: bool,
    exits: ExitHypotheses,
) -> NamedExit | None:
    """Name at most one exit. A missing exit is a hold, not a fill."""
    if type(state) is not PositionState or state not in _OPEN:
        raise ExitError("an exit is named only for an open position")
    if type(days_held) is not int or days_held < 0:
        raise ExitError("days held cannot be negative")
    if type(partial_already_taken) is not bool:
        raise ExitError("partial_already_taken must be a boolean")
    entry = _positive(entry_price, "entry price")
    mark = _positive(mark_price, "mark price")
    highest = _positive(highest_price, "highest price")
    measured = _positive(atr, "atr")
    relative = _decimal(return_vs_btc, "return versus btc")
    if highest < mark:
        raise ExitError("the watermark is below the mark")
    triggers = _triggers(observed_triggers, exits)
    if triggers:
        return NamedExit(rule=ExitRule.FORCED, reason=triggers[0], triggers=triggers)
    risk = _risk(entry, measured, exits)
    initial = entry - risk
    peak_r = (highest - entry) / risk
    activated = peak_r >= _policy(exits.trailing_activate_after_r)
    chandelier = highest - measured * _policy(exits.trailing_atr_mult)
    binding = _binding_stop(mark, initial, chandelier if activated else None)
    if binding is not None:
        return NamedExit(rule=binding, reason=binding.value)
    if days_held >= exits.max_holding_days:
        return NamedExit(rule=ExitRule.MAX_HOLDING, reason=ExitRule.MAX_HOLDING.value)
    ceiling = _policy(exits.time_stop_return_vs_btc_ceiling)
    if days_held >= exits.time_stop_days and relative <= ceiling:
        return NamedExit(rule=ExitRule.TIME_STOP, reason=ExitRule.TIME_STOP.value)
    gained = (mark - entry) / risk
    if (
        state is PositionState.OPEN
        and not partial_already_taken
        and gained >= _policy(exits.partial_take_profit_r)
    ):
        return NamedExit(
            rule=ExitRule.PARTIAL_TAKE_PROFIT, reason=ExitRule.PARTIAL_TAKE_PROFIT.value
        )
    return None


def _binding_stop(mark: Decimal, initial: Decimal, chandelier: Decimal | None) -> ExitRule | None:
    breached: list[tuple[Decimal, ExitRule]] = []
    if mark <= initial:
        breached.append((initial, ExitRule.INITIAL_STOP))
    if chandelier is not None and mark <= chandelier:
        breached.append((chandelier, ExitRule.CHANDELIER))
    if not breached:
        return None
    price, rule = breached[0]
    for other_price, other in breached[1:]:
        if other_price > price:
            price, rule = other_price, other
    return rule


def _risk(entry: Decimal, atr: Decimal, exits: ExitHypotheses) -> Decimal:
    return min(
        atr * _policy(exits.initial_stop_atr_mult),
        entry * _policy(exits.initial_stop_max_pct),
    )


def _triggers(observed: frozenset[str], exits: ExitHypotheses) -> tuple[str, ...]:
    if type(observed) is not frozenset:
        raise ExitError("observed triggers are a frozenset")
    allowed = set(exits.forced_exit_triggers)
    if not observed <= allowed:
        raise ExitError("unknown forced exit")
    return tuple(trigger for trigger in exits.forced_exit_triggers if trigger in observed)


def _policy(value: float) -> Decimal:
    return Decimal(str(value))


def _positive(value: object, label: str) -> Decimal:
    parsed = _decimal(value, label)
    if parsed <= 0:
        raise ExitError(f"{label} must be positive")
    return parsed


def _decimal(value: object, label: str) -> Decimal:
    if isinstance(value, (bool, float, int)):
        raise ExitError(f"{label} must be a decimal")
    if isinstance(value, str):
        try:
            parsed = Decimal(value)
        except InvalidOperation as error:
            raise ExitError(f"{label} must be a decimal") from error
    elif isinstance(value, Decimal):
        parsed = value
    else:
        raise ExitError(f"{label} must be a decimal")
    if not parsed.is_finite():
        raise ExitError(f"{label} must be finite")
    return parsed
