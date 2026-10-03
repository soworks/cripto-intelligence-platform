import hashlib
import io
import runpy
import zipfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from cip.domain.errors import ExchangeGeoBlockedError, HistoryError
from cip.history import cli, sync
from cip.history.client import DumpClient, daily_key, monthly_key
from cip.history.store import load_bars, month_path, read_source_sha256
from cip.history.sync import days_through_yesterday, open_tail, sync_history
from cip.history.universe import read_listings

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "tests" / "fixtures" / "binance-vision"
CONTINUITY = REPO / "data" / "symbol-continuity.yaml"
TODAY = date(2026, 10, 3)
NS = "http://s3.amazonaws.com/doc/2006-03-01"
CATALOG_HOST = "s3-ap-northeast-1.amazonaws.com"
SYMBOLS_PREFIX = "data/spot/monthly/klines/"
BTC_JAN_2025_SHA = "3a3eb1b723d944deb4dbef5ae361bbe39340a17fe987fe22e397ef03dca268d2"


def _row(day: date, close: str) -> str:
    midnight = datetime(day.year, day.month, day.day, tzinfo=UTC)
    scale = 1_000_000 if day.year >= 2025 else 1_000
    opened = int(midnight.timestamp()) * scale
    closed = opened + 86_400 * scale - 1
    return f"{opened},{close},{close},{close},{close},1.00000000,{closed},1.00000000,1,1.0,1.0,0"


def _zip(symbol: str, period: str, days: list[date], close: str = "1.00000000") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        rows = "\n".join(_row(day, close) for day in days) + "\n"
        archive.writestr(f"{symbol}-1d-{period}.csv", rows)
    return buffer.getvalue()


def _checksum(payload: bytes, name: str) -> str:
    return f"{hashlib.sha256(payload).hexdigest()}  {name}"


class Server:
    def __init__(self) -> None:
        self.symbols: list[str] = []
        self.months: dict[str, list[str]] = {}
        self.files: dict[str, bytes | str] = {}
        self.status: dict[str, int] = {}
        self.file_requests: list[str] = []
        self.catalog_prefixes: list[str] = []

    def add_zip(self, key: str, payload: bytes, checksum: str | None = None) -> None:
        self.files[key] = payload
        self.files[f"{key}.CHECKSUM"] = checksum or _checksum(payload, key.rsplit("/", 1)[-1])

    def add_monthly(
        self, symbol: str, year: int, month: int, payload: bytes, checksum: str | None = None
    ) -> None:
        key = monthly_key(symbol, year, month)
        self.add_symbol(symbol)
        self.months.setdefault(symbol, []).append(key)
        self.add_zip(key, payload, checksum)

    def add_daily(self, symbol: str, day: date, payload: bytes | None = None) -> None:
        self.add_symbol(symbol)
        body = payload or _zip(symbol, day.isoformat(), [day])
        self.add_zip(daily_key(symbol, day), body)

    def add_symbol(self, symbol: str) -> None:
        if symbol not in self.symbols:
            self.symbols.append(symbol)

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == CATALOG_HOST:
            return self._catalog(request.url.params["prefix"])
        key = request.url.path.lstrip("/")
        self.file_requests.append(key)
        if key in self.status:
            return httpx.Response(self.status[key])
        content = self.files.get(key)
        if content is None:
            return httpx.Response(404)
        if isinstance(content, str):
            return httpx.Response(200, text=content)
        return httpx.Response(200, content=content)

    def _catalog(self, prefix: str) -> httpx.Response:
        self.catalog_prefixes.append(prefix)
        if prefix == SYMBOLS_PREFIX:
            body = "".join(
                f"<CommonPrefixes><Prefix>{SYMBOLS_PREFIX}{symbol}/</Prefix></CommonPrefixes>"
                for symbol in self.symbols
            )
        else:
            symbol = prefix.split("/")[4]
            body = "".join(
                f"<Contents><Key>{key}</Key></Contents>" for key in self.months.get(symbol, [])
            )
        xml = (
            f'<ListBucketResult xmlns="{NS}">{body}'
            "<IsTruncated>false</IsTruncated></ListBucketResult>"
        )
        return httpx.Response(200, text=xml)

    def client(self) -> DumpClient:
        return DumpClient(transport=httpx.MockTransport(self.handler), sleep=lambda _: None)

    def zips(self) -> list[str]:
        return [key for key in self.file_requests if key.endswith(".zip")]

    def checksums(self) -> list[str]:
        return [key for key in self.file_requests if key.endswith(".CHECKSUM")]


class FakeUploader:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bytes]] = []

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> object:
        self.calls.append((Bucket, Key, Body))
        return {}


def _run(tmp_path: Path, server: Server, **kwargs: object) -> Path:
    output = tmp_path / "out"
    sync_history(
        output=output,
        client=server.client(),
        today=TODAY,
        continuity_path=CONTINUITY,
        **kwargs,  # type: ignore[arg-type]
    )
    return output


def _recorded_btc_january(server: Server) -> None:
    payload = (FIXTURES / "BTCUSDT-1d-2025-01.zip").read_bytes()
    checksum = (FIXTURES / "BTCUSDT-1d-2025-01.zip.CHECKSUM").read_text()
    server.add_monthly("BTCUSDT", 2025, 1, payload, checksum)


def _days(first: date, last: date) -> list[date]:
    return [first + timedelta(days=offset) for offset in range((last - first).days + 1)]


def test_open_tail_includes_previous_and_current_month_when_unlisted() -> None:
    assert open_tail(TODAY, set()) == ((2026, 9), (2026, 10))


def test_open_tail_skips_listed_months() -> None:
    assert open_tail(TODAY, {(2026, 9)}) == ((2026, 10),)
    assert open_tail(TODAY, {(2026, 10)}) == ((2026, 9),)
    assert open_tail(TODAY, {(2026, 9), (2026, 10)}) == ()


def test_open_tail_crosses_the_year_boundary() -> None:
    assert open_tail(date(2026, 1, 15), set()) == ((2025, 12), (2026, 1))


def test_days_through_yesterday_stops_at_yesterday_in_the_current_month() -> None:
    assert days_through_yesterday(2026, 10, TODAY) == (date(2026, 10, 1), date(2026, 10, 2))


def test_days_through_yesterday_covers_a_whole_past_month() -> None:
    assert days_through_yesterday(2026, 9, TODAY) == tuple(
        _days(date(2026, 9, 1), date(2026, 9, 30))
    )
    assert len(days_through_yesterday(2026, 2, TODAY)) == 28
    assert len(days_through_yesterday(2024, 2, TODAY)) == 29


def test_days_through_yesterday_is_empty_on_the_first_of_the_month() -> None:
    assert days_through_yesterday(2026, 10, date(2026, 10, 1)) == ()


def test_listed_month_is_fetched_and_its_daily_keys_are_not_read(tmp_path: Path) -> None:
    server = Server()
    _recorded_btc_january(server)

    output = _run(tmp_path, server)

    path = month_path(output, "BTCUSDT", 2025, 1)
    assert read_source_sha256(path) == BTC_JAN_2025_SHA
    assert monthly_key("BTCUSDT", 2025, 1) in server.zips()
    assert [key for key in server.file_requests if "/daily/" in key and "2025-01" in key] == []
    assert len(load_bars(output)["BTCUSDT"]) == 31


def test_month_outside_the_open_tail_is_not_requested(tmp_path: Path) -> None:
    server = Server()
    server.add_monthly("BTCUSDT", 2024, 6, _zip("BTCUSDT", "2024-06", [date(2024, 6, 1)]))

    _run(tmp_path, server)

    assert [key for key in server.file_requests if "2024-07" in key] == []


def test_unlisted_open_tail_reads_daily_keys_through_yesterday(tmp_path: Path) -> None:
    server = Server()
    server.add_symbol("BTCUSDT")
    for day in _days(date(2026, 9, 1), date(2026, 10, 2)):
        if day == date(2026, 10, 1):
            server.add_zip(
                daily_key("BTCUSDT", day),
                (FIXTURES / "BTCUSDT-1d-2026-10-01.zip").read_bytes(),
                (FIXTURES / "BTCUSDT-1d-2026-10-01.zip.CHECKSUM").read_text(),
            )
        else:
            server.add_daily("BTCUSDT", day)

    output = _run(tmp_path, server)

    expected = [daily_key("BTCUSDT", day) for day in _days(date(2026, 9, 1), date(2026, 10, 2))]
    assert server.zips() == expected
    assert server.checksums() == [f"{key}.CHECKSUM" for key in expected]
    assert [key for key in server.file_requests if "2026-10-03" in key] == []
    bars = load_bars(output)["BTCUSDT"]
    assert bars[0].open_date == date(2026, 9, 1)
    assert bars[-1].open_date == date(2026, 10, 2)
    assert {bar.open_date: bar.close for bar in bars}[date(2026, 10, 1)] == Decimal("84880.05")
    listing = read_listings(output / "universe" / "listings.parquet")[0]
    assert (listing.first_open_date, listing.last_open_date) == (
        date(2026, 9, 1),
        date(2026, 10, 2),
    )


def test_seven_consecutive_daily_misses_stop_the_walk(tmp_path: Path) -> None:
    server = Server()
    server.add_symbol("BTCUSDT")

    _run(tmp_path, server)

    expected = [
        f"{daily_key('BTCUSDT', day)}.CHECKSUM" for day in _days(date(2026, 9, 1), date(2026, 9, 7))
    ]
    assert server.file_requests == expected
    assert not (tmp_path / "out" / "klines").exists()


def test_an_existing_day_resets_the_miss_count(tmp_path: Path) -> None:
    server = Server()
    server.add_daily("BTCUSDT", date(2026, 9, 4))

    output = _run(tmp_path, server)

    requested = {key for key in server.checksums()}
    assert f"{daily_key('BTCUSDT', date(2026, 9, 11))}.CHECKSUM" in requested
    assert f"{daily_key('BTCUSDT', date(2026, 9, 12))}.CHECKSUM" not in requested
    assert server.zips() == [daily_key("BTCUSDT", date(2026, 9, 4))]
    assert [bar.open_date for bar in load_bars(output)["BTCUSDT"]] == [date(2026, 9, 4)]


def test_misses_run_across_the_previous_and_current_month(tmp_path: Path) -> None:
    server = Server()
    for day in _days(date(2026, 9, 1), date(2026, 9, 25)):
        server.add_daily("BTCUSDT", day)

    sync_history(
        output=tmp_path / "out",
        client=server.client(),
        today=date(2026, 10, 10),
        continuity_path=CONTINUITY,
    )

    requested = server.checksums()
    assert f"{daily_key('BTCUSDT', date(2026, 10, 2))}.CHECKSUM" in requested
    assert f"{daily_key('BTCUSDT', date(2026, 10, 3))}.CHECKSUM" not in requested


def test_monthly_zip_with_a_bar_outside_its_month_is_rejected(tmp_path: Path) -> None:
    server = Server()
    server.add_monthly("BTCUSDT", 2024, 1, _zip("BTCUSDT", "2024-01", [date(2024, 2, 1)]))

    with pytest.raises(HistoryError, match="outside its month"):
        _run(tmp_path, server)


def test_daily_zip_for_another_day_is_rejected(tmp_path: Path) -> None:
    server = Server()
    server.add_daily("BTCUSDT", date(2026, 9, 4), _zip("BTCUSDT", "2026-09-04", [date(2026, 9, 5)]))

    with pytest.raises(HistoryError, match="its own day"):
        _run(tmp_path, server)


def test_matching_monthly_checksum_skips_the_second_download(tmp_path: Path) -> None:
    server = Server()
    _recorded_btc_january(server)
    output = _run(tmp_path, server)
    path = month_path(output, "BTCUSDT", 2025, 1)
    first_mtime = path.stat().st_mtime_ns
    server.file_requests.clear()

    _run(tmp_path, server)

    assert monthly_key("BTCUSDT", 2025, 1) not in server.zips()
    assert f"{monthly_key('BTCUSDT', 2025, 1)}.CHECKSUM" in server.checksums()
    assert path.stat().st_mtime_ns == first_mtime


def test_matching_daily_digest_skips_the_second_download(tmp_path: Path) -> None:
    server = Server()
    server.add_daily("BTCUSDT", date(2026, 9, 4))
    server.add_daily("BTCUSDT", date(2026, 9, 5))
    output = _run(tmp_path, server)
    stored = read_source_sha256(month_path(output, "BTCUSDT", 2026, 9))
    lines = "".join(
        f"{day.isoformat()} {hashlib.sha256(_zip('BTCUSDT', day.isoformat(), [day])).hexdigest()}\n"
        for day in (date(2026, 9, 4), date(2026, 9, 5))
    )
    assert stored == hashlib.sha256(lines.encode()).hexdigest()
    server.file_requests.clear()

    _run(tmp_path, server)

    assert server.zips() == []
    assert len(server.checksums()) > 0


def test_changed_daily_digest_downloads_again(tmp_path: Path) -> None:
    server = Server()
    server.add_daily("BTCUSDT", date(2026, 9, 4))
    output = _run(tmp_path, server)
    server.add_daily(
        "BTCUSDT", date(2026, 9, 4), _zip("BTCUSDT", "2026-09-04", [date(2026, 9, 4)], "2.00000000")
    )
    server.file_requests.clear()

    _run(tmp_path, server)

    assert server.zips() == [daily_key("BTCUSDT", date(2026, 9, 4))]
    assert load_bars(output)["BTCUSDT"][0].close == Decimal("2")


def test_changed_checksum_downloads_and_replaces_the_parquet(tmp_path: Path) -> None:
    server = Server()
    january = [date(2024, 1, 1)]
    server.add_monthly("BTCUSDT", 2024, 1, _zip("BTCUSDT", "2024-01", january, "1.00000000"))
    output = _run(tmp_path, server)
    path = month_path(output, "BTCUSDT", 2024, 1)
    before = read_source_sha256(path)
    server.file_requests.clear()
    server.files.clear()
    server.months.clear()
    server.add_monthly("BTCUSDT", 2024, 1, _zip("BTCUSDT", "2024-01", january, "2.00000000"))

    _run(tmp_path, server)

    assert monthly_key("BTCUSDT", 2024, 1) in server.zips()
    assert read_source_sha256(path) != before
    assert load_bars(output)["BTCUSDT"][0].close == Decimal("2")


def test_failed_verification_keeps_the_existing_parquet(tmp_path: Path) -> None:
    server = Server()
    january = [date(2024, 1, 1)]
    server.add_monthly("BTCUSDT", 2024, 1, _zip("BTCUSDT", "2024-01", january, "1.00000000"))
    output = _run(tmp_path, server)
    path = month_path(output, "BTCUSDT", 2024, 1)
    before = path.read_bytes()
    server.files.clear()
    server.months.clear()
    server.add_monthly(
        "BTCUSDT",
        2024,
        1,
        _zip("BTCUSDT", "2024-01", january, "2.00000000"),
        checksum=f"{'0' * 64}  BTCUSDT-1d-2024-01.zip",
    )

    with pytest.raises(HistoryError, match="checksum mismatch"):
        _run(tmp_path, server)

    assert path.read_bytes() == before


@pytest.mark.parametrize(("status", "error"), [(500, HistoryError), (451, ExchangeGeoBlockedError)])
@pytest.mark.parametrize("suffix", ["", ".CHECKSUM"])
def test_fetch_failures_raise_typed_errors(
    tmp_path: Path, status: int, error: type[Exception], suffix: str
) -> None:
    server = Server()
    _recorded_btc_january(server)
    server.status[f"{monthly_key('BTCUSDT', 2025, 1)}{suffix}"] = status

    with pytest.raises(error):
        _run(tmp_path, server)


def test_listed_month_without_a_checksum_is_skipped_and_later_month_syncs(
    tmp_path: Path,
) -> None:
    server = Server()
    _recorded_btc_january(server)
    del server.files[f"{monthly_key('BTCUSDT', 2025, 1)}.CHECKSUM"]
    server.add_monthly("BTCUSDT", 2025, 2, _zip("BTCUSDT", "2025-02", [date(2025, 2, 1)]))

    output = _run(tmp_path, server)

    assert monthly_key("BTCUSDT", 2025, 1) not in server.zips()
    assert monthly_key("BTCUSDT", 2025, 2) in server.zips()
    assert [bar.open_date for bar in load_bars(output)["BTCUSDT"]] == [date(2025, 2, 1)]
    assert read_listings(output / "universe" / "listings.parquet")[0].bar_count == 1


def test_listed_month_without_a_zip_is_skipped(tmp_path: Path) -> None:
    server = Server()
    _recorded_btc_january(server)
    del server.files[monthly_key("BTCUSDT", 2025, 1)]

    _run(tmp_path, server)


def test_daily_checksum_without_a_zip_is_skipped_and_not_stored(tmp_path: Path) -> None:
    server = Server()
    server.add_daily("BTCUSDT", date(2026, 9, 4))
    del server.files[daily_key("BTCUSDT", date(2026, 9, 4))]
    server.add_daily("BTCUSDT", date(2026, 9, 5))

    output = _run(tmp_path, server)

    assert [bar.open_date for bar in load_bars(output)["BTCUSDT"]] == [date(2026, 9, 5)]


def test_daily_checksum_without_any_available_zip_writes_no_month(tmp_path: Path) -> None:
    server = Server()
    server.add_daily("BTCUSDT", date(2026, 9, 4))
    del server.files[daily_key("BTCUSDT", date(2026, 9, 4))]

    output = _run(tmp_path, server)

    assert not month_path(output, "BTCUSDT", 2026, 9).exists()


@pytest.mark.parametrize("symbol", ["BTCUSD", "USDT", "../EVILUSDT", "btcusdt"])
def test_invalid_symbol_is_rejected_before_any_request(tmp_path: Path, symbol: str) -> None:
    server = Server()

    with pytest.raises(HistoryError, match="symbol"):
        _run(tmp_path, server, symbol=symbol)

    assert server.file_requests == []
    assert server.catalog_prefixes == []


def test_symbol_flag_skips_the_symbol_catalog(tmp_path: Path) -> None:
    server = Server()
    _recorded_btc_january(server)
    server.add_monthly("ETHUSDT", 2025, 1, b"unused")

    _run(tmp_path, server, symbol="BTCUSDT")

    assert server.catalog_prefixes == [f"{SYMBOLS_PREFIX}BTCUSDT/1d/"]


def test_every_catalog_symbol_is_synced_and_listed(tmp_path: Path) -> None:
    server = Server()
    _recorded_btc_january(server)
    server.add_monthly("ETHUSDT", 2024, 1, _zip("ETHUSDT", "2024-01", [date(2024, 1, 1)]))

    output = _run(tmp_path, server)

    assert SYMBOLS_PREFIX in server.catalog_prefixes
    listings = read_listings(output / "universe" / "listings.parquet")
    assert [(item.symbol, item.bar_count) for item in listings] == [("BTCUSDT", 31), ("ETHUSDT", 1)]


def test_catalog_symbol_with_unsafe_name_is_rejected(tmp_path: Path) -> None:
    server = Server()
    server.add_symbol("EV-ILUSDT")

    with pytest.raises(HistoryError, match="symbol"):
        _run(tmp_path, server)


def test_failed_sync_does_not_write_listings(tmp_path: Path) -> None:
    server = Server()
    _recorded_btc_january(server)
    server.status[monthly_key("BTCUSDT", 2025, 1)] = 500

    with pytest.raises(HistoryError):
        _run(tmp_path, server)

    assert not (tmp_path / "out" / "universe" / "listings.parquet").exists()


def test_bucket_receives_every_parquet_object(tmp_path: Path) -> None:
    server = Server()
    _recorded_btc_january(server)
    uploader = FakeUploader()

    output = _run(tmp_path, server, bucket="cip-dev-data", uploader=uploader)

    by_key = {key: (bucket, body) for bucket, key, body in uploader.calls}
    kline_key = "klines/interval=1d/quote=USDT/symbol=BTCUSDT/year=2025/month=01/part.parquet"
    assert set(by_key) == {kline_key, "universe/listings.parquet"}
    assert {bucket for bucket, _ in by_key.values()} == {"cip-dev-data"}
    assert by_key[kline_key][1] == (output / kline_key).read_bytes()


def test_no_bucket_means_no_upload(tmp_path: Path) -> None:
    server = Server()
    _recorded_btc_january(server)
    uploader = FakeUploader()

    _run(tmp_path, server, uploader=uploader)

    assert uploader.calls == []


def test_bucket_without_an_uploader_builds_an_s3_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created: list[str] = []
    uploader = FakeUploader()

    def fake_client(service: str) -> FakeUploader:
        created.append(service)
        return uploader

    monkeypatch.setattr(sync.boto3, "client", fake_client)
    server = Server()
    _recorded_btc_january(server)

    _run(tmp_path, server, bucket="cip-dev-data")

    assert created == ["s3"]
    assert len(uploader.calls) == 2


def _patch_cli(monkeypatch: pytest.MonkeyPatch, server: Server) -> None:
    monkeypatch.setattr(cli, "DumpClient", server.client)
    monkeypatch.setattr(cli, "_today", lambda: TODAY)


def test_today_is_the_current_utc_date() -> None:
    assert abs((cli._today() - datetime.now(UTC).date()).days) <= 1


def test_main_sync_returns_zero_and_writes_listings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = Server()
    _recorded_btc_january(server)
    _patch_cli(monkeypatch, server)
    monkeypatch.chdir(REPO)
    output = tmp_path / "out"

    code = cli.main(["sync", "--output", str(output)])

    assert code == 0
    assert (output / "universe" / "listings.parquet").exists()


def test_main_sync_passes_symbol_continuity_and_bucket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = Server()
    _recorded_btc_january(server)
    uploader = FakeUploader()
    _patch_cli(monkeypatch, server)
    monkeypatch.setattr(sync.boto3, "client", lambda service: uploader)

    code = cli.main(
        [
            "sync",
            "--output",
            str(tmp_path / "out"),
            "--symbol",
            "BTCUSDT",
            "--bucket",
            "cip-dev-data",
            "--continuity",
            str(CONTINUITY),
        ]
    )

    assert code == 0
    assert server.catalog_prefixes == [f"{SYMBOLS_PREFIX}BTCUSDT/1d/"]
    assert len(uploader.calls) == 2


@pytest.mark.parametrize(("status", "text"), [(500, "returned 500"), (451, "returned 451")])
def test_main_returns_one_for_expected_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: int,
    text: str,
) -> None:
    server = Server()
    _recorded_btc_january(server)
    server.status[monthly_key("BTCUSDT", 2025, 1)] = status
    _patch_cli(monkeypatch, server)

    code = cli.main(["sync", "--output", str(tmp_path / "out"), "--continuity", str(CONTINUITY)])

    assert code == 1
    assert text in capsys.readouterr().err


def test_main_does_not_swallow_unexpected_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = Server()
    _patch_cli(monkeypatch, server)

    def boom(**_: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "sync_history", boom)

    with pytest.raises(RuntimeError, match="boom"):
        cli.main(["sync", "--output", str(tmp_path / "out")])


def test_main_universe_rebuilds_listings_without_downloading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = Server()
    _recorded_btc_january(server)
    output = _run(tmp_path, server)
    destination = tmp_path / "rebuilt" / "listings.parquet"

    code = cli.main(
        [
            "universe",
            "--klines",
            str(output),
            "--output",
            str(destination),
            "--continuity",
            str(CONTINUITY),
        ]
    )

    assert code == 0
    assert read_listings(destination)[0].symbol == "BTCUSDT"


def test_main_universe_reports_a_bad_continuity_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(
        [
            "universe",
            "--klines",
            str(tmp_path),
            "--output",
            str(tmp_path / "listings.parquet"),
            "--continuity",
            str(tmp_path / "missing.yaml"),
        ]
    )

    assert code == 1
    assert "continuity" in capsys.readouterr().err


def test_module_entry_point_exits_with_the_command_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = [
        "cip.history",
        "universe",
        "--klines",
        str(tmp_path),
        "--output",
        str(tmp_path / "listings.parquet"),
        "--continuity",
        str(tmp_path / "missing.yaml"),
    ]
    monkeypatch.setattr("sys.argv", argv)

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("cip.history", run_name="__main__")

    assert exit_info.value.code == 1
