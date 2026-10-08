import json
import runpy
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from cip.adapters.market import Depth, DepthLevel
from cip.domain.errors import (
    ExchangeBannedError,
    ExchangeGeoBlockedError,
    MarketDataError,
    RateLimited,
    RecorderError,
)
from cip.recorders.cli import main
from cip.recorders.collect import CollectionResult, collect, collect_live, persist
from cip.recorders.observation import CollectionFailure, Observation
from cip.recorders.sources import (
    book_observations,
    fetch_json,
    parse_btc_dominance,
    parse_funding,
    parse_open_interest,
    parse_stablecoin_supply,
)
from cip.recorders.store import append_failure, append_observation

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
SOURCE = datetime(2026, 10, 4, 11, 0, tzinfo=UTC)


def _dominance() -> Observation:
    return parse_btc_dominance(
        {"data": {"market_cap_percentage": {"btc": 54.2}, "updated_at": 1_759_000_000}},
        observed_at=NOW,
    )


def _supply() -> Observation:
    return parse_stablecoin_supply(
        [{"date": 1_759_000_000, "totalCirculatingUSD": {"peggedUSD": "180000000000"}}],
        observed_at=NOW,
    )


def _book() -> Depth:
    return Depth(
        last_update_id=1,
        bids=(
            DepthLevel(price=Decimal("100"), quantity=Decimal("2")),
            DepthLevel(price=Decimal("90"), quantity=Decimal("100")),
        ),
        asks=(
            DepthLevel(price=Decimal("102"), quantity=Decimal("1")),
            DepthLevel(price=Decimal("120"), quantity=Decimal("50")),
        ),
    )


def test_parsers_keep_source_time_and_units() -> None:
    dominance = _dominance()
    supply = _supply()
    funding = parse_funding(
        {"symbol": "BTCUSDT", "lastFundingRate": "0.0001", "time": 1_759_000_000_000},
        observed_at=NOW,
        symbol="BTCUSDT",
    )
    interest = parse_open_interest(
        {"symbol": "BTCUSDT", "openInterest": "12.5", "time": 1_759_000_000_000},
        observed_at=NOW,
        symbol="BTCUSDT",
    )
    spread, depth = book_observations(
        "BTCUSDT", _book(), observed_at=NOW, depth_band=Decimal("0.02")
    )

    assert dominance.family == "market_regime"
    assert dominance.source_timestamp == datetime.fromtimestamp(1_759_000_000, tz=UTC)
    assert dominance.observed_at == NOW
    assert dominance.symbol is None
    assert dict(dominance.units) == {"btc_dominance": "percent"}
    assert supply.family == "market_regime"
    assert funding.family == "derivatives_positioning"
    assert interest.values == (("open_interest", Decimal("12.5")),)
    assert spread.family == "execution_liquidity"
    assert dict(spread.values)["spread_bps"] > 0
    assert dict(depth.values) == {"bid_usd": Decimal("200"), "ask_usd": Decimal("102")}


def test_a_string_source_timestamp_is_accepted() -> None:
    observation = parse_stablecoin_supply(
        [{"date": "1759000000", "totalCirculatingUSD": {"peggedUSD": "1"}}],
        observed_at=NOW,
    )
    assert observation.source_timestamp == datetime.fromtimestamp(1_759_000_000, tz=UTC)


def test_depth_band_comes_from_the_caller() -> None:
    _spread, depth = book_observations(
        "BTCUSDT", _book(), observed_at=NOW, depth_band=Decimal("0.005")
    )
    assert dict(depth.values) == {"bid_usd": Decimal(0), "ask_usd": Decimal(0)}


@pytest.mark.parametrize(
    "parse",
    [
        lambda: parse_btc_dominance({"data": {}}, observed_at=NOW),
        lambda: parse_btc_dominance([], observed_at=NOW),
        lambda: parse_btc_dominance(
            {"data": {"market_cap_percentage": {"btc": 101}, "updated_at": 1}},
            observed_at=NOW,
        ),
        lambda: parse_btc_dominance(
            {"data": {"market_cap_percentage": {"btc": "nope"}, "updated_at": 1}},
            observed_at=NOW,
        ),
        lambda: parse_btc_dominance(
            {"data": {"market_cap_percentage": {"btc": True}, "updated_at": 1}},
            observed_at=NOW,
        ),
        lambda: parse_stablecoin_supply([], observed_at=NOW),
        lambda: parse_stablecoin_supply([{"totalCirculatingUSD": "x"}], observed_at=NOW),
        lambda: parse_stablecoin_supply(
            [{"date": 1, "totalCirculatingUSD": {"peggedUSD": -1}}], observed_at=NOW
        ),
        lambda: parse_funding(
            {"symbol": "ETHUSDT", "lastFundingRate": "0.1", "time": 1},
            observed_at=NOW,
            symbol="BTCUSDT",
        ),
        lambda: parse_funding({"symbol": "BTCUSDT", "time": 1}, observed_at=NOW, symbol="BTCUSDT"),
        lambda: parse_open_interest(
            {"symbol": "BTCUSDT", "openInterest": -1, "time": 1}, observed_at=NOW, symbol="BTCUSDT"
        ),
        lambda: parse_funding(
            {"symbol": "BTCUSDT", "lastFundingRate": "0.1", "time": -1},
            observed_at=NOW,
            symbol="BTCUSDT",
        ),
        lambda: parse_funding(
            {"symbol": "BTCUSDT", "lastFundingRate": "0.1", "time": "1.5"},
            observed_at=NOW,
            symbol="BTCUSDT",
        ),
        lambda: parse_funding(
            {"symbol": "BTCUSDT", "lastFundingRate": "0.1", "time": "nope"},
            observed_at=NOW,
            symbol="BTCUSDT",
        ),
        lambda: parse_funding(
            {"symbol": "BTCUSDT", "lastFundingRate": "0.1", "time": None},
            observed_at=NOW,
            symbol="BTCUSDT",
        ),
        lambda: book_observations(
            "BTCUSDT", Depth(1, (), ()), observed_at=NOW, depth_band=Decimal("0.02")
        ),
        lambda: book_observations("BTCUSDT", _book(), observed_at=NOW, depth_band=Decimal(0)),
        lambda: book_observations(
            "BTCUSDT",
            Depth(
                1,
                (DepthLevel(Decimal("10"), Decimal("1")),),
                (DepthLevel(Decimal("9"), Decimal("1")),),
            ),
            observed_at=NOW,
            depth_band=Decimal("0.02"),
        ),
    ],
)
def test_malformed_or_missing_payloads_do_not_become_observations(parse: object) -> None:
    with pytest.raises(RecorderError):
        parse()  # type: ignore[operator]


def test_fetch_json_reports_timeout_server_error_and_malformed_body() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow", request=request)

    def status(request: httpx.Request) -> httpx.Response:
        if "500" in str(request.url):
            return httpx.Response(500)
        if "451" in str(request.url):
            return httpx.Response(451)
        return httpx.Response(200, content=b"{")

    with (
        httpx.Client(transport=httpx.MockTransport(timeout)) as client,
        pytest.raises(RecorderError, match="timeout"),
    ):
        fetch_json(client, "https://example.test/slow")
    with httpx.Client(transport=httpx.MockTransport(status)) as client:
        with pytest.raises(RecorderError, match="status 500"):
            fetch_json(client, "https://example.test/500")
        with pytest.raises(ExchangeGeoBlockedError):
            fetch_json(client, "https://example.test/451")
        with pytest.raises(RecorderError, match="malformed"):
            fetch_json(client, "https://example.test/bad")

    def limited(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(429, headers={"Retry-After": "30"})

    with (
        httpx.Client(transport=httpx.MockTransport(limited)) as client,
        pytest.raises(RateLimited, match="status 429") as caught,
    ):
        fetch_json(client, "https://example.test/limited")
    assert caught.value.retry_after == "30"


def test_a_transport_error_is_a_recorder_error() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(broken)) as client,
        pytest.raises(RecorderError, match="transport"),
    ):
        fetch_json(client, "https://example.test/down")


def test_append_is_idempotent_and_refuses_a_conflicting_payload(tmp_path: Path) -> None:
    observation = _dominance()
    assert append_observation(tmp_path, observation) is True
    assert append_observation(tmp_path, observation) is False
    conflict = Observation(
        series="btc_dominance",
        provider="coingecko",
        source_timestamp=observation.source_timestamp,
        observed_at=NOW,
        symbol=None,
        values=(("btc_dominance", Decimal("1")),),
        units=(("btc_dominance", "percent"),),
    )
    with pytest.raises(RecorderError, match="different payload"):
        append_observation(tmp_path, conflict)
    stored = list(tmp_path.rglob("observations/**/*.json"))
    assert len(stored) == 1
    document = json.loads(stored[0].read_text())
    assert document["collection_status"] == "ok"
    assert document["schema_version"] == 1
    assert document["values"]["btc_dominance"] == "54.2"


def test_a_failure_is_not_stored_as_an_observation(tmp_path: Path) -> None:
    failure = CollectionFailure(
        series="btc_dominance",
        provider="coingecko",
        observed_at=NOW,
        symbol=None,
        error="timeout",
    )
    assert append_failure(tmp_path, failure) is True
    assert append_failure(tmp_path, failure) is False
    assert list(tmp_path.rglob("observations/**/*.json")) == []
    assert len(list(tmp_path.rglob("observation-failures/**/*.json"))) == 1


def test_one_provider_failure_does_not_drop_another(tmp_path: Path) -> None:
    def boom() -> Observation:
        raise RecorderError("timeout")

    result = collect(
        observed_at=NOW,
        symbols=("BTCUSDT",),
        dominance=boom,
        stablecoins=_supply,
        funding=lambda _symbol: parse_funding(
            {"symbol": "BTCUSDT", "lastFundingRate": "0.0001", "time": 1_759_000_000_000},
            observed_at=NOW,
            symbol="BTCUSDT",
        ),
        open_interest=lambda _symbol: (_ for _ in ()).throw(ExchangeGeoBlockedError("451")),
        book=lambda _symbol: book_observations(
            "BTCUSDT", _book(), observed_at=NOW, depth_band=Decimal("0.02")
        ),
    )
    persist(tmp_path, result)

    series = {item.series for item in result.observations}
    assert series == {"stablecoin_supply", "funding", "spread", "depth"}
    assert {item.series for item in result.failures} == {"btc_dominance", "open_interest"}
    assert list((tmp_path / "observations").rglob("*btc_dominance*")) == []
    assert list((tmp_path / "observations").rglob("*stablecoin_supply*"))


def test_observation_shape_is_rejected() -> None:
    from cip.recorders.observation import CollectionFailure

    cases = [
        dict(
            series="btc_dominance",
            provider="",
            source_timestamp=None,
            observed_at=NOW,
            symbol=None,
            values=(("btc_dominance", Decimal("1")),),
            units=(("btc_dominance", "percent"),),
        ),
        dict(
            series="funding",
            provider="binance",
            source_timestamp=None,
            observed_at=NOW,
            symbol=None,
            values=(("funding_rate", Decimal("1")),),
            units=(("funding_rate", "fraction"),),
        ),
        dict(
            series="btc_dominance",
            provider="coingecko",
            source_timestamp=None,
            observed_at=NOW,
            symbol="BTCUSDT",
            values=(("btc_dominance", Decimal("1")),),
            units=(("btc_dominance", "percent"),),
        ),
        dict(
            series="btc_dominance",
            provider="coingecko",
            source_timestamp=None,
            observed_at=NOW,
            symbol=None,
            values=(),
            units=(),
        ),
        dict(
            series="btc_dominance",
            provider="coingecko",
            source_timestamp=None,
            observed_at=NOW,
            symbol=None,
            values=(("btc_dominance", Decimal("1")), ("btc_dominance", Decimal("2"))),
            units=(("btc_dominance", "percent"), ("btc_dominance", "percent")),
        ),
        dict(
            series="btc_dominance",
            provider="coingecko",
            source_timestamp=None,
            observed_at=NOW,
            symbol=None,
            values=(("btc_dominance", Decimal("1")),),
            units=(("other", "percent"),),
        ),
        dict(
            series="btc_dominance",
            provider="coingecko",
            source_timestamp=None,
            observed_at=NOW,
            symbol=None,
            values=(("btc_dominance", Decimal("1")),),
            units=(("btc_dominance", ""),),
        ),
        dict(
            series="btc_dominance",
            provider="coingecko",
            source_timestamp=NOW.replace(tzinfo=None),
            observed_at=NOW,
            symbol=None,
            values=(("btc_dominance", Decimal("1")),),
            units=(("btc_dominance", "percent"),),
        ),
    ]
    for fields in cases:
        with pytest.raises(RecorderError):
            Observation(**fields)
    with pytest.raises(RecorderError):
        CollectionFailure(
            series="btc_dominance", provider="", observed_at=NOW, symbol=None, error="timeout"
        )
    with pytest.raises(RecorderError):
        parse_btc_dominance(
            {"data": {"market_cap_percentage": {"btc": -1}, "updated_at": 1}},
            observed_at=NOW,
        )
    with pytest.raises(RecorderError):
        parse_stablecoin_supply([1], observed_at=NOW)
    with pytest.raises(RecorderError):
        parse_open_interest(
            {"symbol": "ETHUSDT", "openInterest": "1", "time": 1},
            observed_at=NOW,
            symbol="BTCUSDT",
        )
    with pytest.raises(RecorderError):
        parse_funding(
            {"symbol": "BTCUSDT", "lastFundingRate": "0.1", "time": Decimal("1.5")},
            observed_at=NOW,
            symbol="BTCUSDT",
        )
    book = book_observations("BTCUSDT", _book(), observed_at=NOW, depth_band=Decimal("0.02"))[0]
    assert book.identity_time == NOW


def test_naive_timestamps_and_unknown_series_are_rejected() -> None:
    with pytest.raises(RecorderError):
        Observation(
            series="btc_dominance",
            provider="coingecko",
            source_timestamp=None,
            observed_at=NOW.replace(tzinfo=None),
            symbol=None,
            values=(("btc_dominance", Decimal("1")),),
            units=(("btc_dominance", "percent"),),
        )
    with pytest.raises(RecorderError):
        Observation(
            series="not-a-score",
            provider="coingecko",
            source_timestamp=None,
            observed_at=NOW,
            symbol=None,
            values=(("x", Decimal("1")),),
            units=(("x", "percent"),),
        )


def test_live_collect_appends_each_family(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "global" in url:
            return httpx.Response(
                200,
                json={"data": {"market_cap_percentage": {"btc": 50}, "updated_at": 1_759_000_000}},
            )
        if "stablecoincharts" in url:
            return httpx.Response(
                200,
                json=[{"date": 1_759_000_000, "totalCirculatingUSD": {"peggedUSD": 10}}],
            )
        if "premiumIndex" in url:
            return httpx.Response(
                200,
                json={"symbol": "BTCUSDT", "lastFundingRate": "0.0001", "time": 1_759_000_000_000},
            )
        if "openInterest" in url:
            return httpx.Response(
                200,
                json={"symbol": "BTCUSDT", "openInterest": "3", "time": 1_759_000_000_000},
            )
        return httpx.Response(500)

    class Spot:
        def depth(self, symbol: str, *, limit: int) -> Depth:
            assert symbol == "BTCUSDT"
            assert limit == 100
            return _book()

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as public,
        httpx.Client(transport=httpx.MockTransport(handler)) as futures,
    ):
        result = collect_live(
            observed_at=NOW,
            symbols=("BTCUSDT",),
            spot=Spot(),
            public=public,
            futures=futures,
            depth_band=Decimal("0.02"),
        )
    persist(tmp_path, result)
    families = {item.family for item in result.observations}
    assert families == {"market_regime", "derivatives_positioning", "execution_liquidity"}
    assert result.failures == ()


def test_cli_requires_a_symbol_and_prints_a_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    code = main(["run", "--output", str(tmp_path), "--symbols", " , "])
    assert code == 2
    assert capsys.readouterr().err.startswith("error:")

    monkeypatch.setattr(
        "cip.recorders.cli.collect_live",
        lambda **_kwargs: collect(
            observed_at=NOW,
            symbols=(),
            dominance=_dominance,
            stablecoins=_supply,
            funding=lambda _symbol: _dominance(),
            open_interest=lambda _symbol: _dominance(),
            book=lambda _symbol: (_dominance(), _dominance()),
        ),
    )
    code = main(
        [
            "run",
            "--output",
            str(tmp_path),
            "--symbols",
            "BTCUSDT",
            "--policy",
            "policies/investment-policy.yaml",
        ]
    )
    assert code == 0
    assert "observations=" in capsys.readouterr().out


def test_cli_reports_a_missing_policy(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["run", "--output", str(tmp_path), "--policy", str(tmp_path / "missing.yaml")])
    assert code == 1
    assert capsys.readouterr().err.startswith("error:")


def test_a_later_poll_of_the_same_point_keeps_the_first_file(tmp_path: Path) -> None:
    first = _dominance()
    later = Observation(
        series=first.series,
        provider=first.provider,
        source_timestamp=first.source_timestamp,
        observed_at=NOW + timedelta(hours=1),
        symbol=None,
        values=first.values,
        units=first.units,
    )
    supply = _supply()
    assert append_observation(tmp_path, first) is True
    assert append_observation(tmp_path, later) is False
    stored = json.loads(next(tmp_path.rglob("*.json")).read_text())
    assert stored["observed_at"] == NOW.isoformat()
    kept = persist(tmp_path, CollectionResult(observations=(later, supply), failures=()))
    assert [item.series for item in kept.observations] == ["btc_dominance", "stablecoin_supply"]
    assert kept.failures == ()
    dominance = _series_files(tmp_path / "observations", "btc_dominance")
    assert json.loads(dominance[0].read_text())["observed_at"] == NOW.isoformat()
    assert _series_files(tmp_path / "observations", "stablecoin_supply")


def test_a_revised_source_point_keeps_the_first_file_and_finishes_the_cycle(
    tmp_path: Path,
) -> None:
    first = _supply()
    assert append_observation(tmp_path, first) is True
    revised = Observation(
        series=first.series,
        provider=first.provider,
        source_timestamp=first.source_timestamp,
        observed_at=NOW + timedelta(hours=1),
        symbol=None,
        values=(("stablecoin_supply_usd", Decimal("180000000001")),),
        units=first.units,
    )
    stored = persist(
        tmp_path,
        CollectionResult(observations=(revised, _dominance()), failures=()),
    )
    assert [item.series for item in stored.observations] == ["btc_dominance"]
    assert [item.series for item in stored.failures] == ["stablecoin_supply"]
    assert "different payload" in stored.failures[0].error
    supply_files = _series_files(tmp_path / "observations", "stablecoin_supply")
    assert len(supply_files) == 1
    document = json.loads(supply_files[0].read_text())
    assert document["values"]["stablecoin_supply_usd"] == "180000000000"
    assert document["observed_at"] == NOW.isoformat()
    assert _series_files(tmp_path / "observations", "btc_dominance")
    failure_files = list((tmp_path / "observation-failures").rglob("*.json"))
    assert len(failure_files) == 1
    failure = json.loads(failure_files[0].read_text())
    assert failure["kind"] == "collection_failure"
    assert failure["series"] == "stablecoin_supply"
    assert "values" not in failure


def test_a_corrupt_failure_file_is_kept_and_reported(tmp_path: Path) -> None:
    failure = CollectionFailure(
        series="funding",
        provider="binance",
        observed_at=NOW,
        symbol="BTCUSDT",
        error="host returned 451",
    )
    assert append_failure(tmp_path, failure) is True
    path = next((tmp_path / "observation-failures").rglob("*.json"))
    path.write_bytes(b"not-json")
    with pytest.raises(RecorderError, match="different payload"):
        persist(tmp_path, CollectionResult(observations=(_dominance(),), failures=(failure,)))
    assert path.read_bytes() == b"not-json"
    assert _series_files(tmp_path / "observations", "btc_dominance")


def _series_files(root: Path, series: str) -> list[Path]:
    return [path for path in root.rglob("*.json") if f"series={series}" in path.parts]


def test_a_spot_error_records_spread_and_depth_and_keeps_the_rest() -> None:
    def book(_symbol: str) -> tuple[Observation, Observation]:
        raise MarketDataError("Binance returned HTTP 500")

    result = collect(
        observed_at=NOW,
        symbols=("BTCUSDT", "BTC&USDT"),
        dominance=_dominance,
        stablecoins=_supply,
        funding=lambda _symbol: parse_funding(
            {"symbol": "BTCUSDT", "lastFundingRate": "0.0001", "time": 1_759_000_000_000},
            observed_at=NOW,
            symbol="BTCUSDT",
        ),
        open_interest=lambda _symbol: (_ for _ in ()).throw(httpx.TimeoutException("slow")),
        book=book,
    )
    failed = {(item.series, item.symbol) for item in result.failures}
    assert ("spread", "BTCUSDT") in failed
    assert ("depth", "BTCUSDT") in failed
    assert ("funding", "BTC&USDT") in failed
    assert ("depth", "BTC&USDT") in failed
    assert {item.series for item in result.observations} == {
        "btc_dominance",
        "stablecoin_supply",
        "funding",
    }


def test_a_corrupt_existing_file_is_not_replaced(tmp_path: Path) -> None:
    observation = _dominance()
    append_observation(tmp_path, observation)
    path = next(tmp_path.rglob("*.json"))
    changed = Observation(
        series=observation.series,
        provider=observation.provider,
        source_timestamp=observation.source_timestamp,
        observed_at=observation.observed_at,
        symbol=None,
        values=(("btc_dominance", Decimal("1")),),
        units=observation.units,
    )
    path.write_bytes(b"not-json")
    with pytest.raises(RecorderError, match="different payload"):
        append_observation(tmp_path, changed)
    assert path.read_bytes() == b"not-json"
    path.write_bytes(b"[]")
    with pytest.raises(RecorderError, match="different payload"):
        append_observation(tmp_path, changed)
    assert path.read_bytes() == b"[]"


def test_a_ban_from_one_series_stops_the_collection() -> None:
    def funding(_symbol: str) -> Observation:
        raise ExchangeBannedError("Binance returned 418; the scan must stop")

    with pytest.raises(ExchangeBannedError):
        collect(
            observed_at=NOW,
            symbols=("BTCUSDT",),
            dominance=_dominance,
            stablecoins=_supply,
            funding=funding,
            open_interest=lambda _symbol: _supply(),
            book=lambda _symbol: book_observations(
                "BTCUSDT", _book(), observed_at=NOW, depth_band=Decimal("0.02")
            ),
        )


def test_a_ban_stops_the_collection() -> None:
    def book(_symbol: str) -> tuple[Observation, Observation]:
        raise ExchangeBannedError("Binance returned 418; the scan must stop")

    with pytest.raises(ExchangeBannedError):
        collect(
            observed_at=NOW,
            symbols=("BTCUSDT",),
            dominance=_dominance,
            stablecoins=_supply,
            funding=lambda _symbol: _dominance(),
            open_interest=lambda _symbol: _dominance(),
            book=book,
        )


def test_non_finite_numbers_are_rejected() -> None:
    with pytest.raises(RecorderError, match="non-finite"):
        parse_btc_dominance(
            {"data": {"market_cap_percentage": {"btc": "Infinity"}, "updated_at": 1}},
            observed_at=NOW,
        )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"btc": NaN}')

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(RecorderError, match="malformed"),
    ):
        fetch_json(client, "https://example.test/nan")


def test_cli_prints_a_market_data_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(**_kwargs: object) -> CollectionResult:
        raise MarketDataError("Binance returned HTTP 500")

    monkeypatch.setattr("cip.recorders.cli.collect_live", boom)
    code = main(
        [
            "run",
            "--output",
            str(tmp_path),
            "--symbols",
            "BTCUSDT",
            "--policy",
            "policies/investment-policy.yaml",
        ]
    )
    assert code == 1
    assert capsys.readouterr().err.startswith("error:")


def test_module_entry_point_exits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["cip.recorders", "run", "--output", str(tmp_path), "--symbols", " "],
    )
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("cip.recorders", run_name="__main__")
    assert exit_info.value.code == 2
