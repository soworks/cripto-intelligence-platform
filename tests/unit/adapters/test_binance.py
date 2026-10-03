import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from cip.adapters.binance import (
    PAUSE_AT_WEIGHT,
    WEIGHT_LIMIT_1M,
    BinanceMarketClient,
    WeightLimiter,
)
from cip.adapters.market import probe_exchange_info
from cip.domain.errors import ExchangeBannedError, ExchangeGeoBlockedError, MarketDataError

FIXTURES = Path(__file__).parents[2] / "fixtures" / "binance"
BASE_URL = "https://data-api.binance.vision"


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    base_url: str = BASE_URL,
    pause_at: int = 100,
    clock: _Clock | None = None,
    sleep: Callable[[float], None] | None = None,
) -> BinanceMarketClient:
    ticks = clock or _Clock()
    pauses: list[float] = []

    def default_sleep(seconds: float) -> None:
        pauses.append(seconds)
        ticks.now += seconds

    return BinanceMarketClient(
        base_url,
        transport=httpx.MockTransport(handler),
        limiter=WeightLimiter(
            clock=ticks, sleep=sleep or default_sleep, pause_at=pause_at, limit=100
        ),
        sleep=sleep or default_sleep,
        clock=ticks,
    )


def test_recorded_fixtures_parse_from_the_configured_host() -> None:
    seen: list[httpx.Request] = []
    bodies = {
        "/api/v3/exchangeInfo": _load("exchange_info.json"),
        "/api/v3/ticker/24hr": _load("ticker_24hr.json"),
        "/api/v3/klines": _load("klines.json"),
        "/api/v3/depth": _load("depth.json"),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json=bodies[request.url.path], headers={"X-MBX-USED-WEIGHT-1M": "20"}
        )

    with _client(handler) as client:
        info = client.exchange_info()
        tickers = client.ticker_24hr()
        candles = client.klines("BTCUSDT", interval="1d", limit=2)
        book = client.depth("BTCUSDT", limit=5)

    assert info.symbols[0].symbol == "BTCUSDT"
    assert info.symbols[0].status == "TRADING"
    assert (info.symbols[0].base_asset, info.symbols[0].quote_asset) == ("BTC", "USDT")
    assert tickers[0].symbol == "BTCUSDT"
    assert tickers[0].last_price == Decimal("84705.64000000")
    assert tickers[1].quote_volume == Decimal("236233913.93586500")
    assert candles[0].open_time == 1790899200000
    assert candles[0].close == Decimal("84518.01000000")
    assert candles[0].trade_count == 3801781
    assert book.last_update_id == 101012879323
    assert book.bids[0].price == Decimal("84705.63000000")
    assert book.asks[0].quantity == Decimal("0.53003000")
    assert [request.url.host for request in seen] == ["data-api.binance.vision"] * 4
    assert seen[2].url.params["interval"] == "1d"
    assert seen[3].url.params["limit"] == "5"


def test_probe_counts_symbols() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_load("exchange_info.json"))

    with _client(handler) as client:
        assert probe_exchange_info(client) == 1


def test_client_builds_a_limiter_when_one_is_not_supplied() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_load("exchange_info.json"), headers={"X-MBX-USED-WEIGHT-1M": "1"}
        )

    client = BinanceMarketClient(BASE_URL, transport=httpx.MockTransport(handler))
    try:
        assert client.exchange_info().symbols[0].symbol == "BTCUSDT"
    finally:
        client.close()


def test_trailing_slash_stays_on_the_configured_host() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/exchangeInfo"
        return httpx.Response(200, json=_load("exchange_info.json"))

    with _client(handler, base_url=f"{BASE_URL}/") as client:
        assert client.exchange_info().symbols[0].symbol == "BTCUSDT"


@pytest.mark.parametrize("base_url", ["http://data-api.binance.vision", "data-api.binance.vision"])
def test_non_https_base_url_is_rejected(base_url: str) -> None:
    with pytest.raises(MarketDataError, match="https"):
        BinanceMarketClient(base_url)


def test_status_418_stops_without_retry() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(418, json={"msg": "banned"})

    with _client(handler) as client, pytest.raises(ExchangeBannedError):
        client.exchange_info()
    assert calls == 1


@pytest.mark.parametrize(
    ("status", "error"),
    [(418, ExchangeBannedError), (451, ExchangeGeoBlockedError)],
)
def test_block_statuses_win_over_a_bad_weight_header(status: int, error: type[Exception]) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            status, headers={"X-MBX-USED-WEIGHT-1M": "nope"}, json={"msg": "blocked"}
        )

    with _client(handler) as client, pytest.raises(error):
        client.exchange_info()
    assert calls == 1


def test_status_451_stops_without_retry() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(451, json={"msg": "unavailable"})

    with _client(handler) as client, pytest.raises(ExchangeGeoBlockedError):
        client.exchange_info()
    assert calls == 1


def test_status_429_honors_retry_after_then_succeeds() -> None:
    sleeps: list[float] = []
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "1.5"}, json={"msg": "slow"})
        return httpx.Response(200, json=_load("exchange_info.json"))

    with _client(handler, sleep=sleeps.append) as client:
        assert client.exchange_info().symbols[0].symbol == "BTCUSDT"
    assert sleeps == [1.5]
    assert calls == 2


def test_status_429_without_retry_after_backs_off() -> None:
    sleeps: list[float] = []
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(429, json={"msg": "slow"})
        return httpx.Response(200, json=_load("ticker_24hr.json"))

    with _client(handler, sleep=sleeps.append) as client:
        assert len(client.ticker_24hr()) == 2
    assert sleeps == [1.0, 2.0]


def test_repeated_429_fails_closed() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "0"}, json={"msg": "slow"})

    with (
        _client(handler, sleep=lambda _seconds: None) as client,
        pytest.raises(MarketDataError, match="429"),
    ):
        client.exchange_info()


@pytest.mark.parametrize("status", [302, 500])
def test_other_http_statuses_fail_closed(status: int) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, json={"msg": "no"})

    with _client(handler) as client, pytest.raises(MarketDataError, match=str(status)):
        client.exchange_info()
    assert calls == 1


def test_non_json_body_is_rejected() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    with _client(handler) as client, pytest.raises(MarketDataError, match="not JSON"):
        client.exchange_info()


@pytest.mark.parametrize("header", ["nope", "-1"])
def test_bad_weight_header_is_rejected(header: str) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_load("depth.json"), headers={"X-MBX-USED-WEIGHT-1M": header}
        )

    with _client(handler) as client, pytest.raises(MarketDataError):
        client.depth("BTCUSDT", limit=5)


def test_missing_weight_header_is_allowed() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_load("depth.json"))

    with _client(handler) as client:
        assert client.depth("BTCUSDT", limit=5).last_update_id == 101012879323


def test_high_weight_pauses_until_the_window_rolls() -> None:
    sleeps: list[float] = []
    clock = _Clock()
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        weight = "10" if calls == 1 else "0"
        return httpx.Response(
            200, json=_load("exchange_info.json"), headers={"X-MBX-USED-WEIGHT-1M": weight}
        )

    with _client(handler, pause_at=10, clock=clock, sleep=sleeps.append) as client:
        client.exchange_info()
        clock.now += 15
        client.exchange_info()
    assert sleeps == [45.0]


def test_weight_window_resets_without_a_pause() -> None:
    sleeps: list[float] = []
    clock = _Clock()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_load("exchange_info.json"), headers={"X-MBX-USED-WEIGHT-1M": "10"}
        )

    with _client(handler, pause_at=10, clock=clock, sleep=sleeps.append) as client:
        client.exchange_info()
        clock.now += 60
        client.exchange_info()
    assert sleeps == []


def test_weight_below_the_pause_line_does_not_sleep() -> None:
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_load("exchange_info.json"), headers={"X-MBX-USED-WEIGHT-1M": "9"}
        )

    with _client(handler, pause_at=10, sleep=sleeps.append) as client:
        client.exchange_info()
        client.exchange_info()
    assert sleeps == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": 0},
        {"pause_at": 0},
        {"pause_at": 11, "limit": 10},
        {"window_seconds": 0},
    ],
)
def test_invalid_limiter_settings_are_rejected(kwargs: dict[str, float]) -> None:
    with pytest.raises(MarketDataError, match="limiter"):
        WeightLimiter(clock=_Clock(), sleep=lambda _seconds: None, **kwargs)


def test_default_weight_ceiling_matches_the_recorded_exchange_limit() -> None:
    limiter = WeightLimiter(clock=_Clock(), sleep=lambda _seconds: None)
    assert (limiter.limit, limiter.pause_at) == (WEIGHT_LIMIT_1M, PAUSE_AT_WEIGHT)


@pytest.mark.parametrize("header", ["soon", "-1"])
def test_bad_retry_after_is_rejected(header: str) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": header}, json={"msg": "slow"})

    with _client(handler) as client, pytest.raises(MarketDataError, match="Retry-After"):
        client.exchange_info()


@pytest.mark.parametrize(
    ("method", "kwargs"),
    [
        ("klines", {"symbol": "", "interval": "1d", "limit": 2}),
        ("klines", {"symbol": "BTCUSDT", "interval": "", "limit": 2}),
        ("klines", {"symbol": "BTCUSDT", "interval": "1d", "limit": 0}),
        ("depth", {"symbol": "", "limit": 5}),
        ("depth", {"symbol": "BTCUSDT", "limit": 0}),
    ],
)
def test_blank_query_values_are_rejected(method: str, kwargs: dict[str, object]) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("request must not be sent")

    with _client(handler) as client, pytest.raises(MarketDataError):
        getattr(client, method)(**kwargs)


@pytest.mark.parametrize(
    ("parser", "payload"),
    [
        ("exchange", {"timezone": "UTC"}),
        ("tickers", {"symbol": "BTCUSDT"}),
        ("tickers", [{"symbol": "BTCUSDT", "lastPrice": "1", "quoteVolume": 1}]),
        ("tickers", [{"symbol": "BTCUSDT", "lastPrice": True, "quoteVolume": "1"}]),
        ("tickers", [{"symbol": "BTCUSDT", "lastPrice": "Infinity", "quoteVolume": "1"}]),
        ("tickers", [{"symbol": "BTCUSDT", "lastPrice": "nope", "quoteVolume": "1"}]),
        ("depth", {"lastUpdateId": "x", "bids": [], "asks": []}),
        ("klines", {"rows": []}),
        ("klines", [[True, "1", "1", "1", "1", "1", 1, "1", 1]]),
        ("klines", [["nope", "1", "1", "1", "1", "1", 1, "1", 1]]),
        ("klines", [[1, "1", "1", "1", "1", "1", 1, "1"]]),
        ("depth", []),
        ("depth", {"lastUpdateId": 1}),
        ("depth", {"lastUpdateId": 1, "bids": [["1"]], "asks": []}),
    ],
)
def test_malformed_payloads_are_rejected(parser: str, payload: object) -> None:
    from cip.adapters import market as market_mod

    functions = {
        "exchange": market_mod.parse_exchange_info,
        "tickers": market_mod.parse_tickers,
        "klines": market_mod.parse_klines,
        "depth": market_mod.parse_depth,
    }
    with pytest.raises(MarketDataError, match="invalid"):
        functions[parser](payload)
