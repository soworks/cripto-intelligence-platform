from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from cip.adapters.market import (
    Depth,
    ExchangeInfo,
    Kline,
    Ticker24h,
    parse_depth,
    parse_exchange_info,
    parse_klines,
    parse_tickers,
)
from cip.domain.errors import ExchangeBannedError, ExchangeGeoBlockedError, MarketDataError

WEIGHT_LIMIT_1M = 6000
PAUSE_AT_WEIGHT = 4800
WINDOW_SECONDS = 60.0
REQUEST_TIMEOUT_SECONDS = 10.0
_MAX_ATTEMPTS = 3
_WEIGHT_HEADER = "X-MBX-USED-WEIGHT-1M"


class WeightLimiter:
    """Pause when Binance reports the 1-minute request weight near its ceiling."""

    def __init__(
        self,
        *,
        clock: Callable[[], float],
        sleep: Callable[[float], None],
        limit: int = WEIGHT_LIMIT_1M,
        pause_at: int = PAUSE_AT_WEIGHT,
        window_seconds: float = WINDOW_SECONDS,
    ) -> None:
        if limit < 1 or pause_at < 1 or pause_at > limit or window_seconds <= 0:
            raise MarketDataError("invalid weight limiter settings")
        self._clock = clock
        self._sleep = sleep
        self.limit = limit
        self.pause_at = pause_at
        self.window_seconds = window_seconds
        self._used = 0
        self._observed_at: float | None = None

    def acquire(self) -> None:
        if self._observed_at is None:
            return
        elapsed = self._clock() - self._observed_at
        if elapsed >= self.window_seconds:
            self._used = 0
            self._observed_at = None
            return
        if self._used >= self.pause_at:
            self._sleep(self.window_seconds - elapsed)
            self._used = 0
            self._observed_at = None

    def observe(self, used_weight: int) -> None:
        if used_weight < 0:
            raise MarketDataError("Binance used weight is negative")
        self._used = used_weight
        self._observed_at = self._clock()


class BinanceMarketClient:
    """Public Binance market data. The base URL comes from the caller, not from code."""

    def __init__(
        self,
        base_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
        limiter: WeightLimiter | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        url = base_url.strip().rstrip("/")
        if not url.startswith("https://"):
            raise MarketDataError("market data base URL must use https")
        self._sleep = sleep
        self._limiter = limiter or WeightLimiter(clock=clock, sleep=sleep)
        self._client = httpx.Client(
            base_url=url,
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> BinanceMarketClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def exchange_info(self) -> ExchangeInfo:
        return parse_exchange_info(self._get("/api/v3/exchangeInfo", {}))

    def ticker_24hr(self) -> tuple[Ticker24h, ...]:
        return parse_tickers(self._get("/api/v3/ticker/24hr", {}))

    def klines(self, symbol: str, *, interval: str, limit: int) -> tuple[Kline, ...]:
        self._require_symbol(symbol)
        if interval == "":
            raise MarketDataError("interval is required")
        if limit < 1:
            raise MarketDataError("limit must be at least 1")
        payload = self._get(
            "/api/v3/klines",
            {"symbol": symbol, "interval": interval, "limit": str(limit)},
        )
        return parse_klines(payload)

    def depth(self, symbol: str, *, limit: int) -> Depth:
        self._require_symbol(symbol)
        if limit < 1:
            raise MarketDataError("limit must be at least 1")
        return parse_depth(self._get("/api/v3/depth", {"symbol": symbol, "limit": str(limit)}))

    def _require_symbol(self, symbol: str) -> None:
        if symbol == "":
            raise MarketDataError("symbol is required")

    def _get(self, path: str, params: dict[str, str]) -> Any:
        attempt = 1
        while True:
            self._limiter.acquire()
            response = self._client.get(path, params=params)
            status = response.status_code
            if status == 418:
                raise ExchangeBannedError("Binance returned 418; the scan must stop")
            if status == 451:
                raise ExchangeGeoBlockedError(
                    "Binance returned 451 from the configured market-data host"
                )
            self._observe_weight(response)
            if status != 429:
                if status != 200:
                    raise MarketDataError(f"Binance returned HTTP {status}")
                try:
                    body: Any = response.json()
                except ValueError as error:
                    raise MarketDataError("Binance returned a body that is not JSON") from error
                return body
            if attempt == _MAX_ATTEMPTS:
                raise MarketDataError("Binance returned 429 after 3 attempts")
            self._sleep(self._retry_delay(response, attempt))
            attempt += 1

    def _observe_weight(self, response: httpx.Response) -> None:
        raw = response.headers.get(_WEIGHT_HEADER)
        if raw is None:
            return
        try:
            used = int(raw)
        except ValueError as error:
            raise MarketDataError("Binance used-weight header is not an integer") from error
        self._limiter.observe(used)

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        raw = response.headers.get("Retry-After")
        if raw is None:
            return float(2 ** (attempt - 1))
        try:
            delay = float(raw)
        except ValueError as error:
            raise MarketDataError("Binance Retry-After is not a number") from error
        if delay < 0:
            raise MarketDataError("Binance Retry-After is negative")
        return delay
