import json
import warnings
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from cip.domain.errors import FilterError
from cip.domain.policy import load_policy
from cip.portfolio.symbol_filter import SymbolScreen, screen_symbol

_POLICY = load_policy(Path(__file__).parents[3] / "policies" / "investment-policy.yaml")
_FIXTURE = Path(__file__).parents[2] / "fixtures" / "binance" / "exchange_info.json"


def _filters() -> list[dict[str, object]]:
    return [
        {
            "filterType": "PRICE_FILTER",
            "minPrice": "0.01",
            "maxPrice": "100000",
            "tickSize": "0.01",
        },
        {"filterType": "LOT_SIZE", "minQty": "0.01", "maxQty": "100000", "stepSize": "0.01"},
        {
            "filterType": "MARKET_LOT_SIZE",
            "minQty": "0.00",
            "maxQty": "100000",
            "stepSize": "0",
        },
        {"filterType": "NOTIONAL", "minNotional": "5", "maxNotional": "1000000"},
        {
            "filterType": "PERCENT_PRICE_BY_SIDE",
            "bidMultiplierUp": "1.2",
            "bidMultiplierDown": "0.5",
            "askMultiplierUp": "2",
            "askMultiplierDown": "0.8",
        },
        {"filterType": "MAX_NUM_ALGO_ORDERS", "maxNumAlgoOrders": 5},
    ]


def _info(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "symbol": "SOLUSDT",
        "status": "TRADING",
        "quoteAsset": "USDT",
        "isSpotTradingAllowed": True,
        "orderTypes": ["LIMIT", "LIMIT_MAKER", "MARKET", "STOP_LOSS_LIMIT"],
        "filters": _filters(),
    }
    payload.update(overrides)
    return payload


def _screen(**overrides: object) -> SymbolScreen:
    values: dict[str, object] = {
        "info": _info(),
        "price": Decimal("10"),
        "exit_price": Decimal("9"),
        "size_usd": Decimal("100"),
        "reference_price": Decimal("10"),
        "policy": _POLICY,
    }
    values.update(overrides)
    return screen_symbol(**values)  # type: ignore[arg-type]


def test_a_rounded_lot_that_can_exit_after_fees_passes() -> None:
    screen = _screen()
    assert screen.passed is True
    assert screen.reasons == ()
    assert screen.quantity == Decimal("10")
    document = screen.to_document()
    assert set(document) == {"passed", "reasons", "quantity"}
    assert document["quantity"] == "10.00"
    with pytest.raises(ValidationError):
        SymbolScreen.model_validate({**screen.model_dump(), "order_id": "1"})


def test_the_btc_fixture_admits_a_seventy_five_dollar_entry() -> None:
    payload = json.loads(_FIXTURE.read_text())
    screen = _screen(
        info=payload["symbols"][0],
        price=Decimal("100000.00"),
        exit_price=Decimal("85000.00"),
        size_usd=Decimal("75"),
        reference_price=Decimal("100000.00"),
    )
    assert screen.passed is True
    assert screen.quantity == Decimal("0.00075")


def test_an_off_tick_price_and_a_short_lot_are_skipped() -> None:
    assert _screen(price=Decimal("10.001")).reasons == ("price_filter",)
    assert _screen(size_usd=Decimal("0.05")).reasons == ("lot_size",)
    assert _screen(size_usd=Decimal("4")).reasons == ("min_notional", "full_exit", "partial_exit")


def test_the_taker_fee_can_push_an_exit_under_the_minimum() -> None:
    filters = _filters()
    filters[1] = {"filterType": "LOT_SIZE", "minQty": "1", "maxQty": "1000", "stepSize": "1"}
    filters[4] = {
        "filterType": "PERCENT_PRICE_BY_SIDE",
        "bidMultiplierUp": "1.2",
        "bidMultiplierDown": "0.2",
        "askMultiplierUp": "2",
        "askMultiplierDown": "0.2",
    }
    screen = _screen(
        info=_info(filters=filters),
        price=Decimal("10"),
        size_usd=Decimal("20"),
        exit_price=Decimal("5"),
    )
    assert screen.passed is False
    assert screen.quantity is None
    assert screen.reasons == ("full_exit", "partial_exit")


def test_a_zero_step_keeps_the_unrounded_quantity() -> None:
    filters = _filters()
    filters[0] = {
        "filterType": "PRICE_FILTER",
        "minPrice": "0.01",
        "maxPrice": "100000",
        "tickSize": "0",
    }
    filters[1] = {
        "filterType": "LOT_SIZE",
        "minQty": "0.00000001",
        "maxQty": "100000",
        "stepSize": "0",
    }
    screen = _screen(
        info=_info(filters=filters),
        price=Decimal("3"),
        exit_price=Decimal("3"),
        size_usd=Decimal("100"),
        reference_price=Decimal("3"),
    )
    assert screen.passed is True
    assert screen.quantity == Decimal("100") / Decimal("3")


def test_a_fee_that_rounds_the_whole_lot_away_skips_the_exit() -> None:
    filters = _filters()
    filters[1] = {"filterType": "LOT_SIZE", "minQty": "1", "maxQty": "1000", "stepSize": "1"}
    screen = _screen(
        info=_info(filters=filters),
        price=Decimal("10"),
        exit_price=Decimal("10"),
        size_usd=Decimal("10"),
    )
    assert screen.reasons == ("full_exit", "partial_exit")


def test_a_notional_above_the_maximum_is_skipped() -> None:
    filters = _filters()
    filters[3] = {"filterType": "NOTIONAL", "minNotional": "5", "maxNotional": "50"}
    assert _screen(info=_info(filters=filters)).reasons == ("max_notional",)


def test_a_one_third_exit_that_rounds_away_is_skipped() -> None:
    coarse = _filters()
    coarse[1] = {"filterType": "LOT_SIZE", "minQty": "1", "maxQty": "1000", "stepSize": "1"}
    screen = _screen(
        info=_info(filters=coarse),
        price=Decimal("10"),
        exit_price=Decimal("10"),
        size_usd=Decimal("20"),
    )
    assert screen.reasons == ("partial_exit",)


def test_percent_price_is_inclusive_and_the_market_lot_can_bind() -> None:
    assert _screen(price=Decimal("12")).passed is True
    assert _screen(price=Decimal("12.001")).reasons == ("price_filter", "percent_price")
    assert _screen(exit_price=Decimal("8")).passed is True
    assert _screen(exit_price=Decimal("7.99")).reasons == ("percent_price",)
    tight = _filters()
    tight[2] = {
        "filterType": "MARKET_LOT_SIZE",
        "minQty": "0.01",
        "maxQty": "1",
        "stepSize": "0.01",
    }
    assert _screen(info=_info(filters=tight)).reasons == ("market_lot",)


def test_a_name_that_cannot_trade_or_stop_is_skipped() -> None:
    assert _screen(info=_info(status="BREAK")).reasons == ("not_trading",)
    assert _screen(info=_info(quoteAsset="BTC")).reasons == ("quote_asset",)
    assert _screen(info=_info(isSpotTradingAllowed=False)).reasons == ("not_spot",)
    assert _screen(info=_info(orderTypes=["LIMIT", "LIMIT_MAKER"])).reasons == ("order_types",)
    missing = [item for item in _filters() if item["filterType"] != "NOTIONAL"]
    assert _screen(info=_info(filters=missing)).reasons == ("filters_missing",)
    without_price = [item for item in _filters() if item["filterType"] != "PRICE_FILTER"]
    assert _screen(info=_info(filters=without_price)).reasons == ("filters_missing",)
    capped = _filters()
    capped[5] = {"filterType": "MAX_NUM_ALGO_ORDERS", "maxNumAlgoOrders": 0}
    assert _screen(info=_info(filters=capped)).reasons == ("algo_orders",)


def test_the_older_minimum_notional_filter_still_binds() -> None:
    filters = [item for item in _filters() if item["filterType"] != "NOTIONAL"]
    filters.append({"filterType": "MIN_NOTIONAL", "minNotional": "5"})
    assert _screen(info=_info(filters=filters), size_usd=Decimal("4")).reasons == (
        "min_notional",
        "full_exit",
        "partial_exit",
    )
    assert _screen(info=_info(filters=filters)).passed is True


def test_bad_payloads_are_refused() -> None:
    with pytest.raises(FilterError, match="payload"):
        _screen(info=[])
    with pytest.raises(FilterError, match="payload"):
        _screen(info=_info(filters="no"))
    with pytest.raises(ValidationError):
        SymbolScreen(passed=True, reasons=(), quantity=None)
    with pytest.raises(ValidationError):
        SymbolScreen(passed=True, reasons=(), quantity=Decimal("0"))
    with pytest.raises(ValidationError):
        SymbolScreen(passed=False, reasons=("lot_size",), quantity=Decimal("1"))
    with pytest.raises(ValidationError):
        SymbolScreen(passed=False, reasons=("lot_size",), quantity=1)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        SymbolScreen(passed=False, reasons=("lot_size",), quantity=Decimal("NaN"))
    with pytest.raises(FilterError, match="decimal"):
        _screen(price=10)  # type: ignore[arg-type]
    with pytest.raises(FilterError, match="positive"):
        _screen(size_usd=Decimal("0"))
    with pytest.raises(FilterError, match="filter"):
        _screen(info=_info(filters=[*_filters(), _filters()[0]]))
    counted = _filters()
    counted[5] = {"filterType": "MAX_NUM_ALGO_ORDERS", "maxNumAlgoOrders": True}
    with pytest.raises(FilterError, match="count"):
        _screen(info=_info(filters=counted))
    negative = _filters()
    negative[5] = {"filterType": "MAX_NUM_ALGO_ORDERS", "maxNumAlgoOrders": -1}
    with pytest.raises(FilterError, match="count"):
        _screen(info=_info(filters=negative))
    with pytest.raises(FilterError, match="decimal"):
        _screen(price=Decimal("NaN"))
    with pytest.raises(FilterError, match="payload"):
        _screen(info=_info(status=1))
    with pytest.raises(FilterError, match="payload"):
        _screen(info=_info(quoteAsset=1))
    with pytest.raises(FilterError, match="payload"):
        _screen(info=_info(isSpotTradingAllowed="true"))
    with pytest.raises(FilterError, match="payload"):
        _screen(info=_info(orderTypes="LIMIT"))
    with pytest.raises(FilterError, match="payload"):
        _screen(info=_info(orderTypes=["LIMIT", 1]))
    with pytest.raises(FilterError, match="filter"):
        _screen(info=_info(filters=["ICEBERG"]))
    with pytest.raises(FilterError, match="filter"):
        _screen(info=_info(filters=[{"filterType": 1}]))
    broken = _filters()
    broken[1] = {"filterType": "LOT_SIZE", "minQty": "1", "maxQty": "10", "stepSize": "-1"}
    with pytest.raises(FilterError, match="filter"):
        _screen(info=_info(filters=broken))
    below = _filters()
    below[1] = {"filterType": "LOT_SIZE", "minQty": "-1", "maxQty": "10", "stepSize": "1"}
    with pytest.raises(FilterError, match="filter"):
        _screen(info=_info(filters=below))
    empty_max = _filters()
    empty_max[1] = {"filterType": "LOT_SIZE", "minQty": "0", "maxQty": "0", "stepSize": "0"}
    with pytest.raises(FilterError, match="filter"):
        _screen(info=_info(filters=empty_max))
    inverted = _filters()
    inverted[1] = {"filterType": "LOT_SIZE", "minQty": "5", "maxQty": "1", "stepSize": "1"}
    with pytest.raises(FilterError, match="filter"):
        _screen(info=_info(filters=inverted))
    unticked = _filters()
    unticked[0] = {
        "filterType": "PRICE_FILTER",
        "minPrice": "0.01",
        "maxPrice": "100000",
        "tickSize": "-0.01",
    }
    with pytest.raises(FilterError, match="filter"):
        _screen(info=_info(filters=unticked))
    notion = _filters()
    notion[3] = {"filterType": "NOTIONAL", "minNotional": 5, "maxNotional": "50"}
    with pytest.raises(FilterError, match="filter"):
        _screen(info=_info(filters=notion))
    word = _filters()
    word[3] = {"filterType": "NOTIONAL", "minNotional": "nope", "maxNotional": "50"}
    with pytest.raises(FilterError, match="filter"):
        _screen(info=_info(filters=word))
    missing_number = _filters()
    missing_number[3] = {"filterType": "NOTIONAL", "minNotional": "NaN", "maxNotional": "50"}
    with pytest.raises(FilterError, match="filter"):
        _screen(info=_info(filters=missing_number))
    dust = _filters()
    dust[1] = {"filterType": "LOT_SIZE", "minQty": "0", "maxQty": "100000", "stepSize": "0.01"}
    assert _screen(info=_info(filters=dust), size_usd=Decimal("0.05")).reasons == ("lot_size",)
    capped_lot = _filters()
    capped_lot[1] = {"filterType": "LOT_SIZE", "minQty": "0.01", "maxQty": "1", "stepSize": "0"}
    assert _screen(info=_info(filters=capped_lot)).reasons == ("lot_size",)
    skewed = _filters()
    skewed[2] = {
        "filterType": "MARKET_LOT_SIZE",
        "minQty": "0",
        "maxQty": "100000",
        "stepSize": "0.03",
    }
    assert _screen(info=_info(filters=skewed)).reasons == ("market_lot",)
    aligned = _filters()
    aligned[2] = {
        "filterType": "MARKET_LOT_SIZE",
        "minQty": "0",
        "maxQty": "100000",
        "stepSize": "0.01",
    }
    assert _screen(info=_info(filters=aligned)).passed is True
    assert _screen(info=_info(filters=[])).reasons == ("filters_missing",)
    assert _screen(price=Decimal("100000.01")).reasons[0] == "price_filter"
    with pytest.raises(ValidationError):
        SymbolScreen(passed=True, reasons=("lot_size",), quantity=None)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        forged = _screen().model_copy(update={"passed": "yes"})
        with pytest.raises(FilterError, match="invalid"):
            forged.to_document()
