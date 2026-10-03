from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import cast

from cip.domain.errors import HistoryError

_DAY_MS = 86_400_000
_DAY_US = 86_400_000_000
_MICROSECOND_FLOOR = 10**15
_MAX_FRACTION_DIGITS = 8
_MAX_INTEGER_DIGITS = 30


@dataclass(frozen=True)
class DailyBar:
    symbol: str
    open_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal
    trade_count: int
    taker_buy_base_volume: Decimal
    taker_buy_quote_volume: Decimal


def parse_kline_zip(
    payload: bytes, *, symbol: str, period: str, checksum_text: str
) -> tuple[DailyBar, ...]:
    zip_name = f"{symbol}-1d-{period}.zip"
    _verify_checksum(payload, checksum_text, zip_name)
    csv_name = f"{symbol}-1d-{period}.csv"
    try:
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            names = archive.namelist()
            if names != [csv_name]:
                raise HistoryError("kline zip must contain one matching CSV")
            text = archive.read(csv_name).decode("utf-8")
    except (zipfile.BadZipFile, UnicodeError) as error:
        raise HistoryError("kline zip is unreadable") from error
    rows = [line for line in text.splitlines() if line != ""]
    if not rows:
        raise HistoryError("kline file has no bars")
    bars = tuple(_parse_row(symbol, row) for row in rows)
    dates = [bar.open_date for bar in bars]
    if dates != sorted(set(dates)):
        raise HistoryError("kline dates must be unique and ascending")
    return bars


def _verify_checksum(payload: bytes, checksum_text: str, zip_name: str) -> None:
    parts = checksum_text.strip().split(None, 1)
    if len(parts) != 2 or parts[1] != zip_name or len(parts[0]) != 64:
        raise HistoryError("invalid kline checksum")
    try:
        digest = bytes.fromhex(parts[0]).hex()
    except ValueError as error:
        raise HistoryError("invalid kline checksum") from error
    if hashlib.sha256(payload).hexdigest() != digest:
        raise HistoryError("kline checksum mismatch")


def _parse_row(symbol: str, line: str) -> DailyBar:
    fields = line.split(",")
    if len(fields) != 12:
        raise HistoryError("kline row must have 12 fields")
    open_time = _whole(fields[0])
    close_time = _whole(fields[6])
    unit = "us" if open_time >= _MICROSECOND_FLOOR else "ms"
    step = _DAY_US if unit == "us" else _DAY_MS
    if open_time % step != 0 or not (open_time < close_time < open_time + step):
        raise HistoryError("kline bar must cover one UTC day")
    opened = datetime.fromtimestamp(open_time / (1_000_000 if unit == "us" else 1_000), UTC)
    prices = [_decimal(fields[index]) for index in (1, 2, 3, 4)]
    volumes = [_decimal(fields[index]) for index in (5, 7, 9, 10)]
    open_, high, low, close = prices
    if high < low or high < open_ or high < close or low > open_ or low > close:
        raise HistoryError("kline high and low must bound the bar")
    if any(volume < 0 for volume in volumes):
        raise HistoryError("kline volume is negative")
    trade_count = _whole(fields[8])
    if trade_count < 0:
        raise HistoryError("kline trade count is negative")
    return DailyBar(
        symbol=symbol,
        open_date=opened.date(),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volumes[0],
        quote_volume=volumes[1],
        trade_count=trade_count,
        taker_buy_base_volume=volumes[2],
        taker_buy_quote_volume=volumes[3],
    )


def _whole(text: str) -> int:
    if text == "":
        raise HistoryError("kline integer field is invalid")
    sign = 1
    body = text
    if text[0] == "-":
        sign = -1
        body = text[1:]
    if body == "" or not body.isdigit():
        raise HistoryError("kline integer field is invalid")
    return sign * int(body)


def _decimal(text: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation as error:
        raise HistoryError("kline decimal field is invalid") from error
    if not value.is_finite():
        raise HistoryError("kline decimal field is invalid")
    exponent = cast(int, value.as_tuple().exponent)
    digit_count = len(value.as_tuple().digits)
    fraction_digits = -exponent if exponent < 0 else 0
    integer_digits = max(digit_count + exponent, 0)
    if fraction_digits > _MAX_FRACTION_DIGITS or integer_digits > _MAX_INTEGER_DIGITS:
        raise HistoryError("kline decimal field does not fit decimal128(38, 8)")
    return value
