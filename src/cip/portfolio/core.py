"""Plan one week of the core sleeve. A plan is not an order."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, ValidationError, model_validator

from cip.backtest.schedule import allocate
from cip.domain.errors import CoreError
from cip.domain.policy import LoadedPolicy

_CENT = Decimal("0.01")


def _exact_int(value: object) -> int:
    if type(value) is not int:
        raise ValueError("week counts are integers")
    return value


def _exact_decimal(value: object) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise ValueError("money values are Decimal")
    return value


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class CoreWeek(_Strict):
    """One week's core dollars. The reserve is not deployed. There is no order id."""

    week_index: Annotated[int, BeforeValidator(_exact_int)]
    week_count: Annotated[int, BeforeValidator(_exact_int)]
    btc_usd: Annotated[Decimal, BeforeValidator(_exact_decimal)]
    eth_usd: Annotated[Decimal, BeforeValidator(_exact_decimal)]
    reserve_deployed_usd: Annotated[Decimal, BeforeValidator(_exact_decimal)]
    core_usd: Annotated[Decimal, BeforeValidator(_exact_decimal)]

    @model_validator(mode="after")
    def _matches(self) -> CoreWeek:
        if self.week_count < 1:
            raise ValueError("the month has at least one week")
        if self.week_index < 1 or self.week_index > self.week_count:
            raise ValueError("the week falls inside the month")
        if self.btc_usd <= 0 or self.eth_usd <= 0 or self.core_usd <= 0:
            raise ValueError("a core week is positive")
        if self.btc_usd + self.eth_usd != self.core_usd:
            raise ValueError("the mix spends the week's core budget")
        if self.reserve_deployed_usd != 0:
            raise ValueError("the reserve stays in USDC")
        return self

    def to_document(self) -> dict[str, Any]:
        try:
            checked = CoreWeek.model_validate(self.model_dump())
        except ValidationError as error:
            raise CoreError("core week is invalid") from error
        return {
            "week_index": checked.week_index,
            "week_count": checked.week_count,
            "btc_usd": format(checked.btc_usd, "f"),
            "eth_usd": format(checked.eth_usd, "f"),
            "reserve_deployed_usd": format(checked.reserve_deployed_usd, "f"),
        }


def plan_core_week(*, week_index: int, week_count: int, policy: LoadedPolicy) -> CoreWeek:
    """Split the monthly core budget into this week. Discovery and reserve stay put."""
    index = _count(week_index, "week index")
    count = _count(week_count, "week count")
    if count < 1:
        raise CoreError("the month has at least one week")
    if index < 1 or index > count:
        raise CoreError("the week falls inside the month")
    monthly = policy.policy.portfolio.core_monthly_usd
    share = (monthly / Decimal(count)).quantize(_CENT, rounding=ROUND_HALF_UP)
    if share <= 0:
        raise CoreError("a weekly core share is positive")
    residual = monthly - share * (count - 1)
    if residual <= 0:
        raise CoreError("a weekly core share is positive")
    budget = residual if index == count else share
    mix = {
        symbol: Decimal(str(weight)) for symbol, weight in policy.policy.portfolio.core_mix.items()
    }
    legs = allocate(budget, mix)
    return CoreWeek(
        week_index=index,
        week_count=count,
        btc_usd=legs["BTCUSDT"],
        eth_usd=legs["ETHUSDT"],
        reserve_deployed_usd=Decimal("0.00"),
        core_usd=budget,
    )


def _count(value: object, label: str) -> int:
    if type(value) is not int:
        raise CoreError(f"{label} must be an integer")
    return value
