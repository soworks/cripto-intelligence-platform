import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from cip.domain.errors import EvaluationError, RecorderError
from cip.evaluation.classification_bundle import ensure_bundle, load_component, store_component
from cip.evaluation.classification_producer import (
    CatalogIncomplete,
    classify_stored,
    fetch_component,
)

OPEN = date(2026, 10, 8)
NOW = datetime(2026, 10, 8, 16, 24, tzinfo=UTC)
PAIRS = (("BTCUSDT", "BTC"), ("ETHUSDT", "ETH"))
_TICKER = {
    "base": "BTC",
    "coin_id": "bitcoin",
    "market": {"identifier": "binance"},
    "target": "USDT",
}


def _payload(url: str) -> object:
    if url.endswith("get-all-asset"):
        return {"data": [{"assetCode": "BTC", "tags": []}], "success": True}
    if url.endswith("getNetworkCoinAll"):
        return {"data": [{"coin": "BTC"}], "success": True}
    if "tickers?page=" in url:
        return {"tickers": [_TICKER]}
    if "eur-stablecoin" in url:
        return [{"id": "eurite"}]
    if url.endswith("symbol/list"):
        return {"data": [{"cmcUniqueId": 1, "symbol": "BTCUSDT"}]}
    if "ids=" in url:
        return [{"id": "bitcoin", "last_updated": "2026-10-08T16:00:00Z", "market_cap": 2}]
    raise AssertionError(url)


def _bundle(root: Path, blocked: set[str] | None = None) -> list[str]:
    seen: list[str] = []
    blocked = set() if blocked is None else blocked

    def get(url: str) -> object:
        seen.append(url)
        if any(part in url for part in blocked):
            raise RecorderError("status 429")
        return _payload(url)

    ensure_bundle(root, OPEN, NOW, PAIRS, get)
    return seen


def test_a_later_throttle_keeps_the_ticker_catalog_and_publishes_nothing(tmp_path: Path) -> None:
    seen = _bundle(tmp_path, {"eur-stablecoin"})
    assert any("tickers?page=1" in url for url in seen)
    assert (tmp_path / "captures/session=2026-10-08/classification_sources/tickers.json").is_file()
    assert not (tmp_path / "captures/session=2026-10-08/classification").exists()


def test_the_next_invocation_does_not_download_the_finished_ticker_catalog(tmp_path: Path) -> None:
    _bundle(tmp_path, {"eur-stablecoin"})
    seen = _bundle(tmp_path, set())
    assert not any("tickers" in url for url in seen)
    assert any("eur-stablecoin" in url for url in seen)
    names = sorted(
        path.name
        for path in (tmp_path / "captures/session=2026-10-08/classification").glob("*.json")
    )
    assert names == []


def test_invocations_complete_independent_sources_then_publish_once(tmp_path: Path) -> None:
    _bundle(tmp_path, {"eur-stablecoin"})
    _bundle(tmp_path, {"ids="})
    calls: list[str] = []

    def get(url: str) -> object:
        calls.append(url)
        return _payload(url)

    published = ensure_bundle(tmp_path, OPEN, NOW, PAIRS, get)
    assert published is not None
    assert [item.symbol for item in published] == ["BTCUSDT", "ETHUSDT"]
    assert not any("tickers" in url or "eur-stablecoin" in url for url in calls)
    assert any("ids=" in url for url in calls)
    again = ensure_bundle(tmp_path, OPEN, NOW, PAIRS, get)
    assert [item.symbol for item in again] == ["BTCUSDT", "ETHUSDT"] if again else []
    assert calls == [url for url in calls if "ids=" in url]


def test_a_second_complete_read_makes_no_provider_call(tmp_path: Path) -> None:
    assert ensure_bundle(tmp_path, OPEN, NOW, PAIRS, _payload) is not None

    def get(url: str) -> object:
        raise AssertionError(url)

    again = ensure_bundle(tmp_path, OPEN, NOW, PAIRS, get)
    assert again is not None
    assert len(again) == 2


def test_a_conflicting_source_fails_closed_and_the_first_body_remains(tmp_path: Path) -> None:
    store_component(tmp_path, OPEN, "eur", [{"id": "eurite"}], NOW)
    path = tmp_path / "captures/session=2026-10-08/classification_sources/eur.json"
    before = path.read_bytes()
    with pytest.raises(EvaluationError, match="conflicting observation"):
        store_component(tmp_path, OPEN, "eur", [{"id": "other"}], NOW)
    assert path.read_bytes() == before
    store_component(tmp_path, OPEN, "eur", [{"id": "eurite"}], NOW)


def test_a_source_outside_the_session_is_rejected(tmp_path: Path) -> None:
    after = datetime(2026, 10, 9, 0, 0, tzinfo=UTC)
    before = datetime(2026, 10, 7, 23, tzinfo=UTC)
    with pytest.raises(EvaluationError, match="outside the session"):
        store_component(tmp_path, OPEN, "eur", [{"id": "eurite"}], after)
    with pytest.raises(EvaluationError, match="outside the session"):
        store_component(tmp_path, OPEN, "eur", [{"id": "eurite"}], before)
    store_component(tmp_path, OPEN, "eur", [{"id": "eurite"}], NOW)
    document = json.loads(
        (tmp_path / "captures/session=2026-10-08/classification_sources/eur.json").read_text()
    )
    document["captured_at"] = "2026-10-09T00:00:00Z"
    path = tmp_path / "captures/session=2026-10-08/classification_sources/eur.json"
    path.write_text(json.dumps(document))
    with pytest.raises(EvaluationError, match="outside the session"):
        load_component(tmp_path, OPEN, "eur")


def test_a_finalized_or_sealed_session_cannot_gain_a_source(tmp_path: Path) -> None:
    manifest = tmp_path / "sessions/date=2026-10-08/manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}")
    with pytest.raises(EvaluationError, match="finalized session is sealed"):
        store_component(tmp_path, OPEN, "eur", [{"id": "eurite"}], NOW)
    with pytest.raises(EvaluationError, match="sealed session stays sealed"):
        store_component(
            tmp_path, date(2026, 10, 6), "eur", [], datetime(2026, 10, 6, 1, tzinfo=UTC)
        )


def test_a_damaged_source_file_fails_closed(tmp_path: Path) -> None:
    store_component(tmp_path, OPEN, "eur", [{"id": "eurite"}], NOW)
    path = tmp_path / "captures/session=2026-10-08/classification_sources/eur.json"
    document = json.loads(path.read_text())
    document["content_sha256"] = "0" * 64
    path.write_text(json.dumps(document))
    with pytest.raises(EvaluationError, match="unusable"):
        load_component(tmp_path, OPEN, "eur")
    path.write_text(json.dumps(["nope"]))
    with pytest.raises(EvaluationError, match="unusable"):
        load_component(tmp_path, OPEN, "eur")
    path.write_text(
        json.dumps(
            {
                "captured_at": "yesterday",
                "complete": True,
                "component": "eur",
                "content_sha256": "x",
                "payload": [],
                "session": OPEN.isoformat(),
            }
        )
    )
    with pytest.raises(EvaluationError, match="unusable"):
        load_component(tmp_path, OPEN, "eur")
    document = {
        "captured_at": "2026-10-08T16:24:00+00:00",
        "complete": False,
        "component": "eur",
        "content_sha256": "abc",
        "payload": [],
        "session": "2026-10-07",
    }
    path.write_text(json.dumps(document))
    with pytest.raises(EvaluationError, match="outside the session"):
        load_component(tmp_path, OPEN, "eur")


def test_an_unknown_source_and_a_short_market_catalog_fail(tmp_path: Path) -> None:
    with pytest.raises(EvaluationError, match="unknown"):
        fetch_component("funding", _payload, PAIRS, {})
    with pytest.raises(CatalogIncomplete):
        fetch_component("markets", _payload, PAIRS, {})
    naive = NOW.replace(tzinfo=None)
    with pytest.raises(EvaluationError, match="timezone-aware"):
        store_component(tmp_path, OPEN, "eur", [], naive)
    later = NOW + timedelta(minutes=1)
    store_component(tmp_path, OPEN, "assets", {"data": []}, NOW)
    with pytest.raises(EvaluationError, match="conflicting observation"):
        store_component(tmp_path, OPEN, "assets", {"data": [1]}, later)
    with pytest.raises(EvaluationError, match="unknown"):
        store_component(tmp_path, OPEN, "funding", [], NOW)
    with pytest.raises(EvaluationError, match="unusable"):
        store_component(tmp_path, OPEN, "eur", "raw", NOW)
    stored = store_component(tmp_path, OPEN, "coins", {"data": [Decimal("1.5")]}, NOW)
    assert stored == {"data": ["1.5"]}
    with pytest.raises(TypeError):
        store_component(tmp_path, OPEN, "marketing", {"data": [object()]}, NOW)
    store_component(tmp_path, OPEN, "eur", [{"id": "eurite"}], NOW)

    path = tmp_path / "captures/session=2026-10-08/classification_sources/eur.json"
    document = json.loads(path.read_text())
    document["complete"] = False
    path.write_text(json.dumps(document))
    with pytest.raises(EvaluationError, match="incomplete"):
        load_component(tmp_path, OPEN, "eur")
    document["complete"] = True
    document["captured_at"] = 1
    path.write_text(json.dumps(document))
    with pytest.raises(EvaluationError, match="unusable"):
        load_component(tmp_path, OPEN, "eur")
    document["captured_at"] = "not-a-time"
    path.write_text(json.dumps(document))
    with pytest.raises(EvaluationError, match="unusable"):
        load_component(tmp_path, OPEN, "eur")
    document["captured_at"] = "2026-10-08T16:24:00"
    path.write_text(json.dumps(document))
    with pytest.raises(EvaluationError, match="timezone-aware"):
        load_component(tmp_path, OPEN, "eur")

    def markets(url: str) -> object:
        if "ids=" in url:
            return {"id": "bitcoin"}
        return _payload(url)

    with pytest.raises(CatalogIncomplete):
        fetch_component("markets", markets, PAIRS, {"tickers": [_TICKER]})
    with pytest.raises(EvaluationError, match="unusable"):
        fetch_component("marketing", lambda _url: {"data": {}}, PAIRS, {})
    with pytest.raises(EvaluationError, match="incomplete"):
        classify_stored(PAIRS, {})
    with pytest.raises(EvaluationError, match="incomplete"):
        classify_stored(PAIRS, {"tickers": [], "eur": [], "markets": {}})
    with pytest.raises(EvaluationError, match="unusable"):
        classify_stored(PAIRS, {"tickers": [], "eur": [], "markets": [], "marketing": {}})
