"""Size one discovery position in dollars. A size is not an order."""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_DOWN, Decimal, InvalidOperation
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, model_validator

from cip.domain.errors import SizeError
from cip.domain.policy import DiscoveryState, LoadedPolicy

_CENT = Decimal("0.01")
_REGIMES = ("RISK_ON", "NEUTRAL", "RISK_OFF")
Reason = Literal["sized", "below_minimum", "risk_off", "no_regime"]


def _exact_decimal(value: object) -> Decimal:
    if isinstance(value, (bool, float, int)):
        raise ValueError("money values are Decimal or decimal strings")
    if isinstance(value, str):
        try:
            parsed = Decimal(value)
        except InvalidOperation as error:
            raise ValueError("money values are Decimal or decimal strings") from error
    elif isinstance(value, Decimal):
        parsed = value
    else:
        raise ValueError("money values are Decimal or decimal strings")
    if not parsed.is_finite():
        raise ValueError("money values are finite")
    return parsed


def _optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    return _exact_decimal(value)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class PositionSize(_Strict):
    """Dollar size or an explicit absence. There is no quantity and no order id."""

    size_usd: Annotated[Decimal | None, BeforeValidator(_optional_decimal)]
    minimum_usd: Annotated[Decimal, BeforeValidator(_exact_decimal)]
    reason: Reason

    @model_validator(mode="after")
    def _matches_reason(self) -> Self:
        if self.reason == "sized":
            if self.size_usd is None or self.size_usd < self.minimum_usd:
                raise ValueError("a sized position meets the minimum")
        elif self.size_usd is not None:
            raise ValueError("no size is absent")
        return self

    def to_document(self) -> dict[str, Any]:
        return {
            "size_usd": None if self.size_usd is None else format(self.size_usd, "f"),
            "minimum_usd": format(self.minimum_usd, "f"),
            "reason": self.reason,
        }


def size_position(
    *,
    portfolio_usd: Decimal,
    stop_distance_pct: Decimal,
    beta: Decimal,
    regime: str | None,
    symbol_min_notional: Decimal,
    policy: LoadedPolicy,
) -> PositionSize:
    """Apply the policy formula. Below the minimum, the size is absent."""
    portfolio = _input(portfolio_usd, "portfolio", positive=True)
    stop = _input(stop_distance_pct, "stop distance", positive=False)
    if stop <= 0 or stop > 1:
        raise SizeError("stop distance must be a fraction")
    measured_beta = _input(beta, "beta", positive=False)
    notional = _input(symbol_min_notional, "minimum notional", positive=True)
    minimum = _minimum(notional, policy)
    if regime is None:
        return _absent(minimum, "no_regime")
    state = _state(regime, policy)
    if not state.new_entries or state.size_mult is None:
        return _absent(minimum, "risk_off")
    risk = portfolio * _policy_number(
        policy.policy.hypotheses.sizing.risk_per_trade_pct_of_portfolio
    )
    raw = risk / stop
    trade_cap = _policy_number(policy.policy.risk.max_trade_usd)
    asset_cap = portfolio * _policy_number(policy.policy.risk.max_discovery_asset_pct)
    capped = min(raw, trade_cap, asset_cap)
    sized = capped * _policy_number(state.size_mult) / max(measured_beta, Decimal(1))
    cents = sized.quantize(_CENT, rounding=ROUND_DOWN)
    if cents < minimum:
        return _absent(minimum, "below_minimum")
    return PositionSize(size_usd=cents, minimum_usd=minimum, reason="sized")


def _absent(minimum: Decimal, reason: Reason) -> PositionSize:
    return PositionSize(size_usd=None, minimum_usd=minimum, reason=reason)


def _state(regime: str, policy: LoadedPolicy) -> DiscoveryState:
    if type(regime) is not str or regime not in _REGIMES:
        raise SizeError("regime must be RISK_ON, NEUTRAL, or RISK_OFF")
    hypotheses = policy.policy.hypotheses.regime
    return {
        "RISK_ON": hypotheses.risk_on,
        "NEUTRAL": hypotheses.neutral,
        "RISK_OFF": hypotheses.risk_off,
    }[regime]


def _minimum(notional: Decimal, policy: LoadedPolicy) -> Decimal:
    sizing = policy.policy.hypotheses.sizing
    floor = _policy_number(sizing.min_position_usd_floor)
    multiple = Decimal(sizing.min_notional_multiple) * notional
    return max(floor, multiple).quantize(_CENT, rounding=ROUND_CEILING)


def _policy_number(value: float) -> Decimal:
    return Decimal(str(value))


def _input(value: object, label: str, *, positive: bool) -> Decimal:
    if isinstance(value, (bool, float, int)):
        raise SizeError(f"{label} must be a decimal")
    if isinstance(value, str):
        try:
            parsed = Decimal(value)
        except InvalidOperation as error:
            raise SizeError(f"{label} must be a decimal") from error
    elif isinstance(value, Decimal):
        parsed = value
    else:
        raise SizeError(f"{label} must be a decimal")
    if not parsed.is_finite():
        raise SizeError(f"{label} must be finite")
    if positive and parsed <= 0:
        raise SizeError(f"{label} must be positive")
    return parsed
