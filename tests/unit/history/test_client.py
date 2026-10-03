from collections.abc import Callable
from datetime import date

import httpx
import pytest

from cip.domain.errors import ExchangeGeoBlockedError, HistoryError
from cip.history.client import (
    CATALOG_URL,
    FILES_URL,
    DumpClient,
    daily_key,
    monthly_key,
    months_in_keys,
    parse_list_page,
    parse_object_page,
)

NS = "http://s3.amazonaws.com/doc/2006-03-01"


def _page(prefixes: list[str], *, truncated: bool, marker: str | None) -> str:
    body = "".join(
        f"<CommonPrefixes><Prefix>{prefix}</Prefix></CommonPrefixes>" for prefix in prefixes
    )
    marker_xml = f"<NextMarker>{marker}</NextMarker>" if marker else ""
    flag = "true" if truncated else "false"
    return (
        f'<ListBucketResult xmlns="{NS}">{body}'
        f"<IsTruncated>{flag}</IsTruncated>{marker_xml}</ListBucketResult>"
    )


def _object_page(keys: list[str], *, truncated: bool, marker: str | None) -> str:
    body = "".join(f"<Contents><Key>{key}</Key></Contents>" for key in keys)
    marker_xml = f"<NextMarker>{marker}</NextMarker>" if marker else ""
    flag = "true" if truncated else "false"
    return (
        f'<ListBucketResult xmlns="{NS}">{body}'
        f"<IsTruncated>{flag}</IsTruncated>{marker_xml}</ListBucketResult>"
    )


def test_urls_are_https() -> None:
    assert CATALOG_URL.startswith("https://")
    assert FILES_URL.startswith("https://")


def test_list_page_keeps_usdt_symbols_and_paginates() -> None:
    page = parse_list_page(
        _page(
            [
                "data/spot/monthly/klines/BTCBNB/",
                "data/spot/monthly/klines/BTCUSDT/",
                "data/spot/monthly/klines/USDT/",
            ],
            truncated=True,
            marker="data/spot/monthly/klines/ETHUSDT/",
        )
    )
    assert page.symbols == ("BTCUSDT",)
    assert page.next_marker == "data/spot/monthly/klines/ETHUSDT/"


def test_object_page_reads_keys_and_marker() -> None:
    page = parse_object_page(
        _object_page(["data/one.zip", "data/two.zip"], truncated=True, marker="data/two.zip")
    )
    assert page.keys == ("data/one.zip", "data/two.zip")
    assert page.next_marker == "data/two.zip"


@pytest.mark.parametrize("parser", [parse_list_page, parse_object_page])
def test_truncated_page_without_a_marker_is_rejected(
    parser: Callable[[str], object],
) -> None:
    with pytest.raises(HistoryError):
        parser(_page(["data/spot/monthly/klines/BTCUSDT/"], truncated=True, marker=None))


@pytest.mark.parametrize("parser", [parse_list_page, parse_object_page])
def test_bad_xml_is_rejected(parser: Callable[[str], object]) -> None:
    with pytest.raises(HistoryError):
        parser("<not-xml")


def test_months_ignore_checksum_other_symbols_and_invalid_dates() -> None:
    keys = (
        "data/spot/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-2024-01.zip",
        "data/spot/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-2024-01.zip.CHECKSUM",
        "data/spot/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-2024-13.zip",
        "data/spot/monthly/klines/ETHUSDT/1d/ETHUSDT-1d-2024-02.zip",
        "data/spot/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-2023-12.zip",
    )
    assert months_in_keys("BTCUSDT", keys) == ((2023, 12), (2024, 1))


def test_key_builders() -> None:
    assert monthly_key("BTCUSDT", 2024, 1).endswith("BTCUSDT-1d-2024-01.zip")
    assert daily_key("BTCUSDT", date(2026, 10, 1)).endswith("BTCUSDT-1d-2026-10-01.zip")


def test_client_paginates_symbols_and_rejects_an_empty_usdt_catalog() -> None:
    pages = {
        "": _page(["data/spot/monthly/klines/BTCBNB/"], truncated=True, marker="next"),
        "next": _page(["data/spot/monthly/klines/ETHUSDT/"], truncated=False, marker=None),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["prefix"] == "data/spot/monthly/klines/"
        assert request.url.params["delimiter"] == "/"
        marker = request.url.params.get("marker", "")
        return httpx.Response(200, text=pages[marker])

    with DumpClient(transport=httpx.MockTransport(handler), sleep=lambda _s: None) as client:
        assert client.list_usdt_symbols() == ("ETHUSDT",)

    def empty(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_page([], truncated=False, marker=None))

    with (
        DumpClient(transport=httpx.MockTransport(empty), sleep=lambda _s: None) as client,
        pytest.raises(HistoryError),
    ):
        client.list_usdt_symbols()


def test_client_paginates_month_keys() -> None:
    prefix = "data/spot/monthly/klines/BTCUSDT/1d/"
    pages = {
        "": _object_page([f"{prefix}BTCUSDT-1d-2024-02.zip"], truncated=True, marker="next"),
        "next": _object_page([f"{prefix}BTCUSDT-1d-2024-01.zip"], truncated=False, marker=None),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["prefix"] == prefix
        assert "delimiter" not in request.url.params
        return httpx.Response(200, text=pages[request.url.params.get("marker", "")])

    with DumpClient(transport=httpx.MockTransport(handler), sleep=lambda _s: None) as client:
        assert client.list_months("BTCUSDT") == ((2024, 1), (2024, 2))


def test_download_status_handling_and_text() -> None:
    sleeps: list[float] = []
    tries: list[None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/429.zip"):
            tries.append(None)
            if len(tries) < 2:
                return httpx.Response(429)
        if request.url.path.endswith("/missing.zip"):
            return httpx.Response(404)
        if request.url.path.endswith("/banned.zip"):
            return httpx.Response(451)
        if request.url.path.endswith("/bad.zip"):
            return httpx.Response(500)
        if request.url.path.endswith("/text.txt"):
            return httpx.Response(200, text="hello")
        return httpx.Response(200, content=b"ok")

    transport = httpx.MockTransport(handler)
    with DumpClient(transport=transport, sleep=sleeps.append) as client:
        assert client.get_bytes("data/missing.zip") is None
        assert client.get_text("data/missing.zip") is None
        assert client.get_bytes("data/429.zip") == b"ok"
        assert client.get_text("data/text.txt") == "hello"
        with pytest.raises(ExchangeGeoBlockedError, match="Binance vision returned 451"):
            client.get_bytes("data/banned.zip")
        with pytest.raises(HistoryError):
            client.get_bytes("data/bad.zip")
    assert sleeps == [1.0]


def test_repeated_429_fails_after_two_backoffs() -> None:
    sleeps: list[float] = []
    calls: list[None] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(None)
        return httpx.Response(429)

    with (
        DumpClient(transport=httpx.MockTransport(handler), sleep=sleeps.append) as client,
        pytest.raises(HistoryError),
    ):
        client.get_bytes("data/nope.zip")
    assert calls == [None, None, None]
    assert sleeps == [1.0, 2.0]


@pytest.mark.parametrize(
    ("catalog_url", "files_url"),
    [
        ("http://example.com", FILES_URL),
        (CATALOG_URL, "http://example.com"),
    ],
)
def test_non_https_base_url_is_rejected(catalog_url: str, files_url: str) -> None:
    with pytest.raises(HistoryError, match="https"):
        DumpClient(catalog_url=catalog_url, files_url=files_url)


def test_non_https_final_url_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.scheme == "https":
            return httpx.Response(302, headers={"Location": "http://example.com/final"})
        return httpx.Response(200, content=b"unsafe")

    with (
        DumpClient(transport=httpx.MockTransport(handler), sleep=lambda _s: None) as client,
        pytest.raises(HistoryError, match="https"),
    ):
        client.get_bytes("data/file.zip")


def test_catalog_http_error_is_rejected() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with (
        DumpClient(transport=httpx.MockTransport(handler), sleep=lambda _s: None) as client,
        pytest.raises(HistoryError, match="404"),
    ):
        client.list_usdt_symbols()
