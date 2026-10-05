"""Decide whether one symbol can be entered and exited. A skip is not an order."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, ValidationError, model_validator

from cip.domain.errors import FilterError
from cip.domain.policy import LoadedPolicy

Reason = Literal[
    "not_trading",
    "quote_asset",
    "not_spot",
    "order_types",
    "filters_missing",
    "price_filter",
    "lot_size",
    "market_lot",
    "min_notional",
    "max_notional",
    "percent_price",
    "full_exit",
    "partial_exit",
    "algo_orders",
]
_ORDER_TYPES = frozenset({"LIMIT", "LIMIT_MAKER", "STOP_LOSS_LIMIT"})
_NEEDED = (
    "PRICE_FILTER",
    "LOT_SIZE",
    "MARKET_LOT_SIZE",
    "PERCENT_PRICE_BY_SIDE",
    "MAX_NUM_ALGO_ORDERS",
)
_KNOWN = frozenset({*_NEEDED, "NOTIONAL", "MIN_NOTIONAL"})


def _exact_bool(value: object) -> bool:
    if type(value) is not bool:
        raise ValueError("passed is a boolean")
    return value


def _exact_decimal(value: object) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise ValueError("quantity is a Decimal")
    return value


def _optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    return _exact_decimal(value)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class SymbolScreen(_Strict):
    """Passed, or the reasons the name is skipped. There is no order id."""

    passed: Annotated[bool, BeforeValidator(_exact_bool)]
    reasons: tuple[Reason, ...]
    quantity: Annotated[Decimal | None, BeforeValidator(_optional_decimal)]

    @model_validator(mode="after")
    def _matches(self) -> Self:
        if self.passed is not (len(self.reasons) == 0):
            raise ValueError("a pass has no reason")
        if self.passed:
            if self.quantity is None or self.quantity <= 0:
                raise ValueError("a pass has the rounded quantity")
        elif self.quantity is not None:
            raise ValueError("a skip has no quantity")
        return self

    def to_document(self) -> dict[str, Any]:
        try:
            checked = SymbolScreen.model_validate(self.model_dump())
        except ValidationError as error:
            raise FilterError("symbol screen is invalid") from error
        return {
            "passed": checked.passed,
            "reasons": list(checked.reasons),
            "quantity": None if checked.quantity is None else format(checked.quantity, "f"),
        }


@dataclass(frozen=True)
class _Lot:
    minimum: Decimal
    maximum: Decimal
    step: Decimal


@dataclass(frozen=True)
class _Rules:
    price_min: Decimal
    price_max: Decimal
    tick: Decimal
    lot: _Lot
    market: _Lot
    min_notional: Decimal
    max_notional: Decimal | None
    bid_down: Decimal
    bid_up: Decimal
    ask_down: Decimal
    ask_up: Decimal
    algo_max: int


def screen_symbol(
    *,
    info: object,
    price: Decimal,
    exit_price: Decimal,
    size_usd: Decimal,
    reference_price: Decimal,
    policy: LoadedPolicy,
) -> SymbolScreen:
    """Apply the symbol's exchange filters and the taker fee to both exits."""
    entry = _money(price, "price")
    stop = _money(exit_price, "exit price")
    size = _money(size_usd, "size")
    reference = _money(reference_price, "reference price")
    status, quote, spot, order_types, rules = _read(info)
    fee = Decimal(str(policy.policy.venue.taker_fee_rate))
    fraction = Decimal(str(policy.policy.hypotheses.exits.partial_take_profit_fraction))
    reasons: list[Reason] = []
    if status != "TRADING":
        reasons.append("not_trading")
    if quote != "USDT":
        reasons.append("quote_asset")
    if not spot:
        reasons.append("not_spot")
    if not order_types >= _ORDER_TYPES:
        reasons.append("order_types")
    if rules is None:
        reasons.append("filters_missing")
        return _screen(reasons, None)
    if not _on_grid(entry, rules.price_min, rules.price_max, rules.tick) or not _on_grid(
        stop, rules.price_min, rules.price_max, rules.tick
    ):
        reasons.append("price_filter")
    quantity = _floor(size / entry, rules.lot)
    if quantity is None:
        reasons.append("lot_size")
    elif not _conforms(quantity, rules.market):
        reasons.append("market_lot")
    if quantity is not None:
        notional = entry * quantity
        if notional < rules.min_notional:
            reasons.append("min_notional")
        if rules.max_notional is not None and notional > rules.max_notional:
            reasons.append("max_notional")
        _exits(reasons, quantity, stop, fee, fraction, rules)
    if not (reference * rules.bid_down <= entry <= reference * rules.bid_up) or not (
        reference * rules.ask_down <= stop <= reference * rules.ask_up
    ):
        reasons.append("percent_price")
    if rules.algo_max < 1:
        reasons.append("algo_orders")
    return _screen(reasons, quantity)


def _exits(
    reasons: list[Reason],
    quantity: Decimal,
    stop: Decimal,
    fee: Decimal,
    fraction: Decimal,
    rules: _Rules,
) -> None:
    held = _floor(quantity * (1 - fee), rules.lot)
    if held is None or stop * held * (1 - fee) < rules.min_notional:
        reasons.append("full_exit")
    partial = None if held is None else _floor(held * fraction, rules.lot)
    partial_net = None if partial is None else stop * partial * (1 - fee)
    if partial_net is None or partial_net < rules.min_notional:
        reasons.append("partial_exit")


def _screen(reasons: list[Reason], quantity: Decimal | None) -> SymbolScreen:
    return SymbolScreen(
        passed=not reasons,
        reasons=tuple(reasons),
        quantity=None if reasons else quantity,
    )


def _read(
    info: object,
) -> tuple[str, str, bool, frozenset[str], _Rules | None]:
    if type(info) is not dict:
        raise FilterError("exchange info payload is one symbol")
    status = info.get("status")
    quote = info.get("quoteAsset")
    spot = info.get("isSpotTradingAllowed")
    order_types = info.get("orderTypes")
    filters = info.get("filters")
    if (
        type(status) is not str
        or type(quote) is not str
        or type(spot) is not bool
        or type(order_types) is not list
        or not all(type(item) is str for item in order_types)
        or type(filters) is not list
    ):
        raise FilterError("exchange info payload is one symbol")
    found = _index(filters)
    if any(name not in found for name in _NEEDED) or (
        "NOTIONAL" not in found and "MIN_NOTIONAL" not in found
    ):
        return status, quote, spot, frozenset(order_types), None
    price = found["PRICE_FILTER"]
    notion = found["NOTIONAL"] if "NOTIONAL" in found else found["MIN_NOTIONAL"]
    return (
        status,
        quote,
        spot,
        frozenset(order_types),
        _Rules(
            price_min=_number(price, "minPrice"),
            price_max=_number(price, "maxPrice"),
            tick=_number(price, "tickSize"),
            lot=_lot(found["LOT_SIZE"]),
            market=_lot(found["MARKET_LOT_SIZE"]),
            min_notional=_number(notion, "minNotional"),
            max_notional=_optional_number(notion, "maxNotional"),
            bid_down=_number(found["PERCENT_PRICE_BY_SIDE"], "bidMultiplierDown"),
            bid_up=_number(found["PERCENT_PRICE_BY_SIDE"], "bidMultiplierUp"),
            ask_down=_number(found["PERCENT_PRICE_BY_SIDE"], "askMultiplierDown"),
            ask_up=_number(found["PERCENT_PRICE_BY_SIDE"], "askMultiplierUp"),
            algo_max=_count(found["MAX_NUM_ALGO_ORDERS"].get("maxNumAlgoOrders")),
        ),
    )


def _index(filters: list[object]) -> dict[str, dict[str, object]]:
    found: dict[str, dict[str, object]] = {}
    for item in filters:
        if type(item) is not dict:
            raise FilterError("filter is unreadable")
        kind = item.get("filterType")
        if type(kind) is not str:
            raise FilterError("filter is unreadable")
        if kind not in _KNOWN:
            continue
        if kind in found:
            raise FilterError("duplicate filter")
        found[kind] = item
    return found


def _lot(payload: dict[str, object]) -> _Lot:
    lot = _Lot(
        minimum=_number(payload, "minQty"),
        maximum=_number(payload, "maxQty"),
        step=_number(payload, "stepSize"),
    )
    if lot.step < 0 or lot.minimum < 0 or lot.maximum <= 0 or lot.minimum > lot.maximum:
        raise FilterError("filter is unreadable")
    return lot


def _on_grid(price: Decimal, minimum: Decimal, maximum: Decimal, tick: Decimal) -> bool:
    if price < minimum or price > maximum:
        return False
    if tick == 0:
        return True
    if tick < 0:
        raise FilterError("filter is unreadable")
    return (price - minimum) % tick == 0


def _floor(quantity: Decimal, lot: _Lot) -> Decimal | None:
    if lot.step == 0:
        floored = quantity
    elif quantity < lot.minimum:
        return None
    else:
        steps = ((quantity - lot.minimum) / lot.step).to_integral_value(rounding=ROUND_DOWN)
        floored = lot.minimum + steps * lot.step
    if floored < lot.minimum or floored > lot.maximum or floored <= 0:
        return None
    return floored


def _conforms(quantity: Decimal, lot: _Lot) -> bool:
    if quantity <= 0 or quantity < lot.minimum or quantity > lot.maximum:
        return False
    if lot.step == 0:
        return True
    return (quantity - lot.minimum) % lot.step == 0


def _money(value: object, label: str) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise FilterError(f"{label} must be a decimal")
    if value <= 0:
        raise FilterError(f"{label} must be positive")
    return value


def _number(payload: dict[str, object], key: str) -> Decimal:
    value = payload.get(key)
    if type(value) is not str:
        raise FilterError("filter is unreadable")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise FilterError("filter is unreadable") from error
    if not parsed.is_finite():
        raise FilterError("filter is unreadable")
    return parsed


def _optional_number(payload: dict[str, object], key: str) -> Decimal | None:
    if key not in payload:
        return None
    return _number(payload, key)


def _count(value: object) -> int:
    if type(value) is not int or value < 0:
        raise FilterError("algo order cap is a count")
    return value
