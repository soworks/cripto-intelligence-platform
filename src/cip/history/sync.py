from __future__ import annotations

import calendar
import hashlib
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol, cast

import boto3

from cip.domain.errors import HistoryError
from cip.history.bars import DailyBar, declared_sha256, parse_kline_zip, verified_sha256
from cip.history.client import DumpClient, daily_key, monthly_key
from cip.history.continuity import load_continuity
from cip.history.store import load_bars, month_path, read_source_sha256, write_month
from cip.history.universe import build_listings, write_listings

_MAX_DAILY_MISSES = 7
_SYMBOL = re.compile(r"[A-Z0-9]+USDT")


class _Uploader(Protocol):
    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> object: ...


def open_tail(today: date, listed: set[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    current = (today.year, today.month)
    previous_day = today.replace(day=1) - timedelta(days=1)
    previous = (previous_day.year, previous_day.month)
    return tuple(month for month in (previous, current) if month not in listed)


def days_through_yesterday(year: int, month: int, today: date) -> tuple[date, ...]:
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    end = min(today - timedelta(days=1), last)
    return tuple(first + timedelta(days=offset) for offset in range((end - first).days + 1))


def sync_history(
    *,
    output: Path,
    client: DumpClient,
    today: date,
    continuity_path: Path,
    symbol: str | None = None,
    bucket: str | None = None,
    uploader: object | None = None,
) -> None:
    continuity = load_continuity(continuity_path)
    symbols = (symbol,) if symbol is not None else client.list_usdt_symbols()
    for name in symbols:
        _require_symbol(name)
    for name in symbols:
        _sync_symbol(client, output, name, today)
    listings = build_listings(load_bars(output), continuity)
    write_listings(output / "universe" / "listings.parquet", listings)
    if bucket is not None:
        target = cast(_Uploader, uploader if uploader is not None else boto3.client("s3"))
        _upload(output, bucket, target)


def _require_symbol(symbol: str) -> None:
    if _SYMBOL.fullmatch(symbol) is None:
        raise HistoryError(f"invalid symbol {symbol!r}: expected an uppercase USDT pair")


def _sync_symbol(client: DumpClient, output: Path, symbol: str, today: date) -> None:
    months = client.list_months(symbol)
    for year, month in months:
        _sync_monthly(client, output, symbol, year, month)
    _sync_open_tail(client, output, symbol, today, set(months))


def _sync_monthly(client: DumpClient, output: Path, symbol: str, year: int, month: int) -> None:
    period = f"{year:04d}-{month:02d}"
    name = f"{symbol}-1d-{period}.zip"
    key = monthly_key(symbol, year, month)
    checksum_text = client.get_text(f"{key}.CHECKSUM")
    if checksum_text is None:
        return
    path = month_path(output, symbol, year, month)
    if read_source_sha256(path) == declared_sha256(checksum_text, name):
        return
    payload = client.get_bytes(key)
    if payload is None:
        return
    digest = verified_sha256(payload, checksum_text, name)
    bars = parse_kline_zip(payload, symbol=symbol, period=period, checksum_text=checksum_text)
    if any((bar.open_date.year, bar.open_date.month) != (year, month) for bar in bars):
        raise HistoryError(f"{name} has a bar outside its month")
    write_month(path, bars, source_sha256=digest)


def _sync_open_tail(
    client: DumpClient, output: Path, symbol: str, today: date, listed: set[tuple[int, int]]
) -> None:
    misses = 0
    for year, month in open_tail(today, listed):
        found: list[tuple[date, str, str]] = []
        for day in days_through_yesterday(year, month, today):
            name = f"{symbol}-1d-{day.isoformat()}.zip"
            checksum_text = client.get_text(f"{daily_key(symbol, day)}.CHECKSUM")
            if checksum_text is None:
                misses += 1
                if misses == _MAX_DAILY_MISSES:
                    break
                continue
            misses = 0
            found.append((day, checksum_text, declared_sha256(checksum_text, name)))
        if found:
            _sync_daily_month(client, output, symbol, year, month, found)
        if misses == _MAX_DAILY_MISSES:
            return


def _sync_daily_month(
    client: DumpClient,
    output: Path,
    symbol: str,
    year: int,
    month: int,
    found: list[tuple[date, str, str]],
) -> None:
    lines = "".join(f"{day.isoformat()} {digest}\n" for day, _, digest in found)
    source_sha256 = hashlib.sha256(lines.encode()).hexdigest()
    path = month_path(output, symbol, year, month)
    if read_source_sha256(path) == source_sha256:
        return
    bars: list[DailyBar] = []
    stored: list[tuple[date, str]] = []
    for day, checksum_text, digest in found:
        name = f"{symbol}-1d-{day.isoformat()}.zip"
        payload = client.get_bytes(daily_key(symbol, day))
        if payload is None:
            continue
        verified_sha256(payload, checksum_text, name)
        parsed = parse_kline_zip(
            payload, symbol=symbol, period=day.isoformat(), checksum_text=checksum_text
        )
        if [bar.open_date for bar in parsed] != [day]:
            raise HistoryError(f"{name} does not hold exactly its own day")
        bars.extend(parsed)
        stored.append((day, digest))
    if not bars:
        return
    lines = "".join(f"{day.isoformat()} {digest}\n" for day, digest in stored)
    source_sha256 = hashlib.sha256(lines.encode()).hexdigest()
    write_month(path, tuple(bars), source_sha256=source_sha256)


def _upload(output: Path, bucket: str, uploader: _Uploader) -> None:
    for path in sorted(output.rglob("*.parquet")):
        uploader.put_object(
            Bucket=bucket, Key=path.relative_to(output).as_posix(), Body=path.read_bytes()
        )
