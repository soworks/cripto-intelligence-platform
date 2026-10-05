"""Price one shadow fill from the next bar. A fill is not an order."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Literal, Self, cast

from pydantic import BaseModel, BeforeValidator, ConfigDict, ValidationError, model_validator

from cip.domain.errors import FillError
from cip.domain.policy import LoadedPolicy

Side = Literal["buy", "sell"]
Reason = Literal["filled", "partial", "no_next_bar", "buy_budget", "unfilled"]
_ONE = Decimal(1)


def _exact_decimal(value: object) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise ValueError("money values are Decimal")
    return value


def _optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    return _exact_decimal(value)


def _exact_side(value: object) -> str:
    if type(value) is not str or value not in ("buy", "sell"):
        raise ValueError("side must be buy or sell")
    return value


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class ShadowFill(_Strict):
    """The next-bar fill, or why there is none. There is no order id."""

    side: Annotated[Side, BeforeValidator(_exact_side)]
    reason: Reason
    fill_price: Annotated[Decimal | None, BeforeValidator(_optional_decimal)]
    quantity: Annotated[Decimal | None, BeforeValidator(_optional_decimal)]
    fee_drag_usd: Annotated[Decimal | None, BeforeValidator(_optional_decimal)]
    budget_usd: Annotated[Decimal, BeforeValidator(_exact_decimal)]
    requested: Annotated[Decimal, BeforeValidator(_exact_decimal)]

    @model_validator(mode="after")
    def _matches(self) -> Self:
        traded = self.reason in ("filled", "partial")
        present = (
            self.fill_price is not None
            and self.quantity is not None
            and self.fee_drag_usd is not None
        )
        if traded is not present:
            raise ValueError("a fill has a price, a quantity, and a fee")
        if not traded:
            if self.budget_usd != 0:
                raise ValueError("an unfilled shadow does not spend the buy budget")
            return self
        price = cast(Decimal, self.fill_price)
        quantity = cast(Decimal, self.quantity)
        drag = cast(Decimal, self.fee_drag_usd)
        if price <= 0 or quantity <= 0 or drag <= 0:
            raise ValueError("a fill is positive")
        if self.reason == "filled" and quantity != self.requested:
            raise ValueError("a full fill takes the requested quantity")
        if self.reason == "partial" and not (quantity < self.requested):
            raise ValueError("a partial fill is smaller than the request")
        if self.side == "buy" and self.budget_usd != quantity * price:
            raise ValueError("a buy spends its filled notional")
        if self.side == "sell" and self.budget_usd != 0:
            raise ValueError("a sell does not spend the buy budget")
        return self

    def to_document(self) -> dict[str, Any]:
        try:
            checked = ShadowFill.model_validate(self.model_dump())
        except ValidationError as error:
            raise FillError("shadow fill is invalid") from error
        return {
            "side": checked.side,
            "reason": checked.reason,
            "fill_price": _text(checked.fill_price),
            "quantity": _text(checked.quantity),
            "fee_drag_usd": _text(checked.fee_drag_usd),
            "budget_usd": _plain(checked.budget_usd),
        }


def shadow_fill(
    *,
    side: str,
    next_open: Decimal | None,
    quantity: Decimal,
    filled_quantity: Decimal | None,
    spread: Decimal,
    slippage: Decimal,
    buy_spent_today_usd: Decimal,
    buy_spent_month_usd: Decimal,
    policy: LoadedPolicy,
) -> ShadowFill:
    """Fill at the next open, or skip. A sell ignores the buy budget."""
    name = _side(side)
    requested = _money(quantity, "quantity", positive=True)
    filled = requested
    if filled_quantity is not None:
        filled = _money(filled_quantity, "filled quantity", positive=False)
    if filled < 0 or filled > requested:
        raise FillError("filled quantity must not exceed the request")
    width = _cost(spread, "spread")
    slip = _cost(slippage, "slippage")
    spent_today = _money(buy_spent_today_usd, "buy spent today", positive=False)
    spent_month = _money(buy_spent_month_usd, "buy spent this month", positive=False)
    if spent_today < 0 or spent_month < 0:
        raise FillError("buy spent must be a non-negative decimal")
    opened = None if next_open is None else _money(next_open, "next open", positive=True)
    fee = Decimal(str(policy.policy.venue.taker_fee_rate))
    drag = fee + width / 2 + slip
    if drag >= _ONE:
        raise FillError("cost drag must be below 1")
    if opened is None:
        return _absent(name, "no_next_bar", requested)
    if filled == 0:
        return _absent(name, "unfilled", requested)
    price = opened * (_ONE + drag) if name == "buy" else opened * (_ONE - drag)
    cost = filled * opened * (_ONE + drag)
    if name == "buy" and _over_budget(cost, spent_today, spent_month, policy):
        return _absent(name, "buy_budget", requested)
    return ShadowFill(
        side=name,
        reason="filled" if filled == requested else "partial",
        fill_price=price,
        quantity=filled,
        fee_drag_usd=filled * opened * drag,
        budget_usd=cost if name == "buy" else Decimal(0),
        requested=requested,
    )


def _absent(side: Side, reason: Reason, requested: Decimal) -> ShadowFill:
    return ShadowFill(
        side=side,
        reason=reason,
        fill_price=None,
        quantity=None,
        fee_drag_usd=None,
        budget_usd=Decimal(0),
        requested=requested,
    )


def _over_budget(cost: Decimal, today: Decimal, month: Decimal, policy: LoadedPolicy) -> bool:
    risk = policy.policy.risk
    daily = Decimal(str(risk.max_daily_trade_usd))
    monthly = Decimal(str(risk.max_monthly_trade_usd))
    return today + cost > daily or month + cost > monthly


def _text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return _plain(value)


def _plain(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." not in rendered:
        return rendered
    return rendered.rstrip("0").rstrip(".")


def _side(value: object) -> Side:
    if type(value) is not str or value not in ("buy", "sell"):
        raise FillError("side must be buy or sell")
    return value  # type: ignore[return-value]


def _money(value: object, label: str, *, positive: bool) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise FillError(f"{label} must be a decimal")
    if positive and value <= 0:
        raise FillError(f"{label} must be positive")
    return value


def _cost(value: object, label: str) -> Decimal:
    parsed = _money(value, label, positive=False)
    if parsed < 0:
        raise FillError(f"{label} must be a non-negative decimal")
    return parsed
