from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from cip.adapters.market import Depth
from cip.domain.errors import ExchangeGeoBlockedError, RecorderError
from cip.recorders.observation import Observation

COINGECKO_GLOBAL = "https://api.coingecko.com/api/v3/global"
DEFILLAMA_STABLECOINS = "https://stablecoins.llama.fi/stablecoincharts/all"
FUTURES_BASE_URL = "https://fapi.binance.com"


def fetch_json(client: httpx.Client, url: str, params: dict[str, str] | None = None) -> object:
    try:
        response = client.get(url, params=params)
    except httpx.TimeoutException as error:
        raise RecorderError("timeout") from error
    except httpx.HTTPError as error:
        raise RecorderError("transport") from error
    if response.status_code == 451:
        raise ExchangeGeoBlockedError("host returned 451")
    if response.status_code != 200:
        raise RecorderError(f"status {response.status_code}")
    try:
        return json.loads(response.text, parse_float=Decimal, parse_constant=_reject_constant)
    except json.JSONDecodeError as error:
        raise RecorderError("malformed response") from error


def parse_btc_dominance(payload: object, *, observed_at: datetime) -> Observation:
    body = _child(_mapping(payload, "global"), "data")
    percentages = _child(body, "market_cap_percentage")
    dominance = _required_decimal(percentages, "btc")
    if dominance < 0 or dominance > 100:
        raise RecorderError("btc dominance is outside 0 to 100")
    return _observation(
        series="btc_dominance",
        provider="coingecko",
        source_timestamp=_unix_seconds(body.get("updated_at")),
        observed_at=observed_at,
        symbol=None,
        values=(("btc_dominance", dominance),),
        units=(("btc_dominance", "percent"),),
    )


def parse_stablecoin_supply(payload: object, *, observed_at: datetime) -> Observation:
    if not isinstance(payload, list) or not payload:
        raise RecorderError("stablecoin supply is missing")
    last = payload[-1]
    if not isinstance(last, dict):
        raise RecorderError("malformed stablecoin supply")
    circulating = _child(last, "totalCirculatingUSD")
    supply = _required_decimal(circulating, "peggedUSD")
    if supply < 0:
        raise RecorderError("stablecoin supply is negative")
    return _observation(
        series="stablecoin_supply",
        provider="defillama",
        source_timestamp=_unix_seconds(last.get("date")),
        observed_at=observed_at,
        symbol=None,
        values=(("stablecoin_supply_usd", supply),),
        units=(("stablecoin_supply_usd", "usd"),),
    )


def parse_funding(payload: object, *, observed_at: datetime, symbol: str) -> Observation:
    body = _mapping(payload, "funding")
    if body.get("symbol") != symbol:
        raise RecorderError("funding symbol does not match the request")
    rate = _required_decimal(body, "lastFundingRate")
    return _observation(
        series="funding",
        provider="binance",
        source_timestamp=_unix_millis(body.get("time")),
        observed_at=observed_at,
        symbol=symbol,
        values=(("funding_rate", rate),),
        units=(("funding_rate", "fraction"),),
    )


def parse_open_interest(payload: object, *, observed_at: datetime, symbol: str) -> Observation:
    body = _mapping(payload, "open interest")
    if body.get("symbol") != symbol:
        raise RecorderError("open interest symbol does not match the request")
    # Binance reports open interest in the base asset. Notional uses a later
    # point-in-time price; this recorder does not convert it.
    amount = _required_decimal(body, "openInterest")
    if amount < 0:
        raise RecorderError("open interest is negative")
    return _observation(
        series="open_interest",
        provider="binance",
        source_timestamp=_unix_millis(body.get("time")),
        observed_at=observed_at,
        symbol=symbol,
        values=(("open_interest", amount),),
        units=(("open_interest", "base_asset"),),
    )


def book_observations(
    symbol: str,
    book: Depth,
    *,
    observed_at: datetime,
    depth_band: Decimal,
    provider: str = "binance",
) -> tuple[Observation, Observation]:
    if not depth_band.is_finite() or depth_band <= 0 or depth_band > 1:
        raise RecorderError("depth band must be a fraction from above 0 through 1")
    if not book.bids or not book.asks:
        raise RecorderError("depth book is empty")
    best_bid = book.bids[0].price
    best_ask = book.asks[0].price
    if best_bid <= 0 or best_ask <= 0 or best_ask < best_bid:
        raise RecorderError("depth book is crossed or non-positive")
    mid = (best_bid + best_ask) / 2
    spread_bps = (best_ask - best_bid) / mid * Decimal(10_000)
    bid_depth = _band_notional(book.bids, mid, depth_band, below=True)
    ask_depth = _band_notional(book.asks, mid, depth_band, below=False)
    spread = _observation(
        series="spread",
        provider=provider,
        source_timestamp=None,
        observed_at=observed_at,
        symbol=symbol,
        values=(("spread_bps", spread_bps),),
        units=(("spread_bps", "bps"),),
    )
    depth = _observation(
        series="depth",
        provider=provider,
        source_timestamp=None,
        observed_at=observed_at,
        symbol=symbol,
        values=(("bid_usd", bid_depth), ("ask_usd", ask_depth)),
        units=(("bid_usd", "usd"), ("ask_usd", "usd")),
    )
    return spread, depth


def _band_notional(
    levels: tuple[Any, ...], mid: Decimal, depth_band: Decimal, *, below: bool
) -> Decimal:
    floor = mid * (1 - depth_band)
    ceiling = mid * (1 + depth_band)
    total = Decimal(0)
    for level in levels:
        price = level.price
        if below and price < floor:
            continue
        if not below and price > ceiling:
            continue
        total += price * level.quantity
    return total


def _observation(
    *,
    series: str,
    provider: str,
    source_timestamp: datetime | None,
    observed_at: datetime,
    symbol: str | None,
    values: tuple[tuple[str, Decimal], ...],
    units: tuple[tuple[str, str], ...],
) -> Observation:
    return Observation(
        series=series,
        provider=provider,
        source_timestamp=source_timestamp,
        observed_at=observed_at,
        symbol=symbol,
        values=values,
        units=units,
    )


def _mapping(payload: object, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RecorderError(f"malformed {label}")
    return payload


def _child(body: dict[str, Any], key: str) -> dict[str, Any]:
    value = body.get(key)
    if not isinstance(value, dict):
        raise RecorderError(f"missing {key}")
    return value


def _required_decimal(body: dict[str, Any], key: str) -> Decimal:
    if key not in body:
        raise RecorderError(f"missing {key}")
    value = body[key]
    if isinstance(value, bool) or not isinstance(value, (Decimal, str, int, float)):
        raise RecorderError(f"malformed {key}")
    try:
        number = Decimal(str(value))
    except InvalidOperation as error:
        raise RecorderError(f"malformed {key}") from error
    if not number.is_finite():
        raise RecorderError(f"non-finite {key}")
    return number


def _reject_constant(token: str) -> None:
    raise json.JSONDecodeError("non-finite number", token, 0)


def _unix_seconds(value: object) -> datetime:
    return datetime.fromtimestamp(_whole(value), tz=UTC)


def _unix_millis(value: object) -> datetime:
    return datetime.fromtimestamp(_whole(value) / 1000, tz=UTC)


def _whole(value: object) -> int:
    if isinstance(value, str):
        try:
            value = Decimal(value)
        except InvalidOperation as error:
            raise RecorderError("missing source timestamp") from error
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise RecorderError("missing source timestamp")
    if isinstance(value, Decimal) and value != value.to_integral_value():
        raise RecorderError("missing source timestamp")
    number = int(value)
    if number < 0:
        raise RecorderError("missing source timestamp")
    return number
