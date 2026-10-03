from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Annotated, Protocol

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictStr, ValidationError

from cip.domain.errors import MarketDataError


def _decimal_string(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError("must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("must be a decimal string") from error
    if not parsed.is_finite():
        raise ValueError("must be finite")
    return parsed


def _whole_number(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("must be an integer")
    return value


DecimalString = Annotated[Decimal, BeforeValidator(_decimal_string)]

_KLINE_WIDTH = 9


class _Payload(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class SymbolInfo(_Payload):
    symbol: StrictStr = Field(min_length=1)
    status: StrictStr = Field(min_length=1)
    base_asset: StrictStr = Field(min_length=1, validation_alias="baseAsset")
    quote_asset: StrictStr = Field(min_length=1, validation_alias="quoteAsset")


class ExchangeInfo(_Payload):
    symbols: tuple[SymbolInfo, ...]


class Ticker24h(_Payload):
    symbol: StrictStr = Field(min_length=1)
    last_price: DecimalString = Field(validation_alias="lastPrice")
    quote_volume: DecimalString = Field(validation_alias="quoteVolume")


@dataclass(frozen=True)
class Kline:
    open_time: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    close_time: int
    quote_volume: Decimal
    trade_count: int


@dataclass(frozen=True)
class DepthLevel:
    price: Decimal
    quantity: Decimal


@dataclass(frozen=True)
class Depth:
    last_update_id: int
    bids: tuple[DepthLevel, ...]
    asks: tuple[DepthLevel, ...]


class MarketData(Protocol):
    def exchange_info(self) -> ExchangeInfo: ...

    def ticker_24hr(self) -> tuple[Ticker24h, ...]: ...

    def klines(self, symbol: str, *, interval: str, limit: int) -> tuple[Kline, ...]: ...

    def depth(self, symbol: str, *, limit: int) -> Depth: ...


def parse_exchange_info(payload: object) -> ExchangeInfo:
    try:
        return ExchangeInfo.model_validate(payload)
    except ValidationError as error:
        raise MarketDataError("invalid exchangeInfo payload") from error


def parse_tickers(payload: object) -> tuple[Ticker24h, ...]:
    if not isinstance(payload, list):
        raise MarketDataError("invalid ticker payload")
    try:
        return tuple(Ticker24h.model_validate(item) for item in payload)
    except ValidationError as error:
        raise MarketDataError("invalid ticker payload") from error


def parse_klines(payload: object) -> tuple[Kline, ...]:
    if not isinstance(payload, list):
        raise MarketDataError("invalid klines payload")
    rows: list[Kline] = []
    for row in payload:
        if not isinstance(row, list) or len(row) < _KLINE_WIDTH:
            raise MarketDataError("invalid klines payload")
        try:
            rows.append(
                Kline(
                    open_time=_whole_number(row[0]),
                    open=_decimal_string(row[1]),
                    high=_decimal_string(row[2]),
                    low=_decimal_string(row[3]),
                    close=_decimal_string(row[4]),
                    volume=_decimal_string(row[5]),
                    close_time=_whole_number(row[6]),
                    quote_volume=_decimal_string(row[7]),
                    trade_count=_whole_number(row[8]),
                )
            )
        except ValueError as error:
            raise MarketDataError("invalid klines payload") from error
    return tuple(rows)


def _levels(payload: object) -> tuple[DepthLevel, ...]:
    if not isinstance(payload, list):
        raise ValueError("must be a list of levels")
    levels: list[DepthLevel] = []
    for level in payload:
        if not isinstance(level, list) or len(level) < 2:
            raise ValueError("must be a price and quantity")
        levels.append(
            DepthLevel(price=_decimal_string(level[0]), quantity=_decimal_string(level[1]))
        )
    return tuple(levels)


def parse_depth(payload: object) -> Depth:
    if not isinstance(payload, dict):
        raise MarketDataError("invalid depth payload")
    try:
        return Depth(
            last_update_id=_whole_number(payload.get("lastUpdateId")),
            bids=_levels(payload.get("bids")),
            asks=_levels(payload.get("asks")),
        )
    except ValueError as error:
        raise MarketDataError("invalid depth payload") from error


def probe_exchange_info(market: MarketData) -> int:
    """Read exchange info. A 418 or 451 from the client propagates unchanged."""
    return len(market.exchange_info().symbols)
