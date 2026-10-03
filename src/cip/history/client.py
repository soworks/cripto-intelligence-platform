from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Literal, overload

import httpx

from cip.domain.errors import ExchangeGeoBlockedError, HistoryError

CATALOG_URL = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
FILES_URL = "https://data.binance.vision"

_NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01"}
_MONTHLY_PREFIX = "data/spot/monthly/klines"


@dataclass(frozen=True)
class ListPage:
    symbols: tuple[str, ...]
    next_marker: str | None


@dataclass(frozen=True)
class ObjectPage:
    keys: tuple[str, ...]
    next_marker: str | None


def parse_list_page(xml_text: str) -> ListPage:
    root = _xml_root(xml_text)
    prefixes = (prefix.text or "" for prefix in root.findall("s3:CommonPrefixes/s3:Prefix", _NS))
    symbols = tuple(
        symbol
        for prefix in prefixes
        if (symbol := prefix.rstrip("/").rsplit("/", 1)[-1]).endswith("USDT") and symbol != "USDT"
    )
    return ListPage(symbols=symbols, next_marker=_next_marker(root))


def parse_object_page(xml_text: str) -> ObjectPage:
    root = _xml_root(xml_text)
    keys = tuple(key.text or "" for key in root.findall("s3:Contents/s3:Key", _NS))
    return ObjectPage(keys=keys, next_marker=_next_marker(root))


def monthly_key(symbol: str, year: int, month: int) -> str:
    return f"{_MONTHLY_PREFIX}/{symbol}/1d/{symbol}-1d-{year:04d}-{month:02d}.zip"


def daily_key(symbol: str, day: date) -> str:
    return f"data/spot/daily/klines/{symbol}/1d/{symbol}-1d-{day.isoformat()}.zip"


def months_in_keys(symbol: str, keys: tuple[str, ...]) -> tuple[tuple[int, int], ...]:
    pattern = re.compile(rf"/{re.escape(symbol)}-1d-(\d{{4}})-(\d{{2}})\.zip$")
    months: set[tuple[int, int]] = set()
    for key in keys:
        match = pattern.search(key)
        if match is not None:
            year, month = (int(part) for part in match.groups())
            if 1 <= month <= 12:
                months.add((year, month))
    return tuple(sorted(months))


class DumpClient:
    def __init__(
        self,
        *,
        catalog_url: str = CATALOG_URL,
        files_url: str = FILES_URL,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._catalog_url = catalog_url.rstrip("/")
        self._files_url = files_url.rstrip("/")
        _require_https(self._catalog_url)
        _require_https(self._files_url)
        self._client = httpx.Client(
            transport=transport,
            follow_redirects=True,
            timeout=30,
        )
        self._sleep = sleep

    def __enter__(self) -> DumpClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def list_usdt_symbols(self) -> tuple[str, ...]:
        marker: str | None = None
        symbols: list[str] = []
        while True:
            params = {
                "prefix": f"{_MONTHLY_PREFIX}/",
                "delimiter": "/",
            }
            if marker is not None:
                params["marker"] = marker
            response = self._fetch(self._catalog_url, params=params)
            page = parse_list_page(response.text)
            symbols.extend(page.symbols)
            marker = page.next_marker
            if marker is None:
                break
        result = tuple(symbols)
        if not result:
            raise HistoryError("Binance vision catalog has no USDT symbols")
        return result

    def list_months(self, symbol: str) -> tuple[tuple[int, int], ...]:
        marker: str | None = None
        keys: list[str] = []
        while True:
            params = {"prefix": f"{_MONTHLY_PREFIX}/{symbol}/1d/"}
            if marker is not None:
                params["marker"] = marker
            response = self._fetch(self._catalog_url, params=params)
            page = parse_object_page(response.text)
            keys.extend(page.keys)
            marker = page.next_marker
            if marker is None:
                break
        return months_in_keys(symbol, tuple(keys))

    def get_bytes(self, key: str) -> bytes | None:
        response = self._fetch(f"{self._files_url}/{key.lstrip('/')}", allow_missing=True)
        return None if response is None else response.content

    def get_text(self, key: str) -> str | None:
        response = self._fetch(f"{self._files_url}/{key.lstrip('/')}", allow_missing=True)
        return None if response is None else response.text

    @overload
    def _fetch(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        allow_missing: Literal[False] = False,
    ) -> httpx.Response: ...

    @overload
    def _fetch(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        allow_missing: Literal[True],
    ) -> httpx.Response | None: ...

    def _fetch(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        allow_missing: bool = False,
    ) -> httpx.Response | None:
        _require_https(url)
        attempt = 0
        while True:
            response = self._client.get(url, params=params)
            _require_https(str(response.url))
            if response.status_code == 404 and allow_missing:
                return None
            if response.status_code == 451:
                raise ExchangeGeoBlockedError("Binance vision returned 451")
            if response.status_code == 429:
                attempt += 1
                if attempt == 3:
                    raise HistoryError("Binance vision returned 429 after retries")
                self._sleep(float(attempt))
                continue
            if response.status_code != 200:
                raise HistoryError(f"Binance vision returned {response.status_code}")
            return response


def _xml_root(xml_text: str) -> ET.Element:
    try:
        return ET.fromstring(xml_text)  # noqa: S314 - trusted fixed-shape S3 listing
    except ET.ParseError as error:
        raise HistoryError("Binance vision catalog XML is invalid") from error


def _next_marker(root: ET.Element) -> str | None:
    truncated = root.findtext("s3:IsTruncated", default="false", namespaces=_NS) == "true"
    if not truncated:
        return None
    marker = root.findtext("s3:NextMarker", namespaces=_NS)
    if marker is None:
        raise HistoryError("truncated Binance vision catalog page has no marker")
    return marker


def _require_https(url: str) -> None:
    if not url.startswith("https://"):
        raise HistoryError("Binance vision URLs must use https")
