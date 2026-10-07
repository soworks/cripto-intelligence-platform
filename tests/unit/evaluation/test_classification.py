"""Point-in-time classification and market cap. Unknown stays unknown."""

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from cip.domain.errors import EvaluationError
from cip.domain.policy import load_policy
from cip.evaluation.classification import (
    CapReading,
    classify_universe,
    map_binance_tickers,
    map_cmc_ids,
    parse_asset_catalog,
    parse_cmc_quotes,
    parse_coin_config,
    parse_coingecko_markets,
    store_classification,
    to_facts,
)
from cip.evaluation.eligibility import assess

_POLICY = Path(__file__).parents[3] / "policies" / "investment-policy.yaml"
UNIVERSE = load_policy(_POLICY).policy.hypotheses.universe
SESSION = date(2026, 10, 7)
WHEN = datetime(2026, 10, 7, 13, 0, tzinfo=UTC)
STAMP = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def _asset(code: str, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "assetCode": code,
        "tags": ["Payments"],
        "delisted": False,
        "preDelist": False,
        "swapTag": "no",
        "oldAssetCode": "",
        "newAssetCode": "",
    }
    body.update(overrides)
    return body


def _catalog(*rows: dict[str, object]) -> dict[str, object]:
    return {"success": True, "data": list(rows)}


def _venue(
    base: str, target: str, coin_id: str | None = None, *, venue: str = "binance"
) -> dict[str, object]:
    body: dict[str, object] = {"base": base, "target": target, "market": {"identifier": venue}}
    if coin_id is not None:
        body["coin_id"] = coin_id
    return body


def _coin(coin: str, *, deposits: bool = True, withdrawals: bool = True) -> dict[str, object]:
    return {
        "coin": coin,
        "depositAllEnable": deposits,
        "withdrawAllEnable": withdrawals,
        "free": "999",
        "locked": "1",
    }


def test_a_tag_list_without_the_tag_is_false_and_a_missing_list_stays_missing() -> None:
    assets = parse_asset_catalog(
        _catalog(
            _asset("BTC"),
            _asset("JASMY", tags=["Monitoring", "Seed"]),
            _asset("CHZ", tags=["fan_token"]),
            _asset("GONE", tags=None, delisted=True, preDelist=False, swapTag="no"),
        )
    )
    btc = assets["BTC"]
    assert btc.fan_token is False
    assert btc.monitoring_tag is False
    assert btc.delisting is False
    assert btc.pending_migration is False
    assert assets["JASMY"].monitoring_tag is True
    assert assets["CHZ"].fan_token is True
    assert assets["GONE"].fan_token is None
    assert assets["GONE"].monitoring_tag is None
    assert assets["GONE"].delisting is True


def test_delisting_and_migration_need_evidence_for_the_negative() -> None:
    assets = parse_asset_catalog(
        _catalog(
            _asset("HALF", delisted=False, preDelist=None),
            _asset("SOON", delisted=False, preDelist=True),
            _asset("WAIT", swapTag="ps", oldAssetCode="WAIT", newAssetCode="NEXT"),
            _asset("OLD", swapTag="sw", oldAssetCode="OLD", newAssetCode="NEW"),
            _asset("NEW", swapTag="sw", oldAssetCode="OLD", newAssetCode="NEW"),
            _asset("ODD", swapTag="sw", oldAssetCode="ODD", newAssetCode="ODD"),
            _asset("WHAT", swapTag="later"),
            _asset("BLANK", swapTag=None),
        )
    )
    assert assets["HALF"].delisting is None
    assert assets["SOON"].delisting is True
    assert assets["WAIT"].pending_migration is True
    assert assets["OLD"].pending_migration is True
    assert assets["NEW"].pending_migration is False
    assert assets["ODD"].pending_migration is None
    assert assets["WHAT"].pending_migration is None
    assert assets["BLANK"].pending_migration is None


def test_a_swap_with_one_missing_code_stays_unknown() -> None:
    assets = parse_asset_catalog(
        _catalog(
            _asset("NEXT", swapTag="sw", oldAssetCode=None, newAssetCode="NEXT"),
            _asset("NEXT2", swapTag="sw", oldAssetCode="", newAssetCode="NEXT2"),
            _asset("OLD", swapTag="sw", oldAssetCode="OLD", newAssetCode=""),
            _asset("OLD2", swapTag="sw", oldAssetCode="OLD2", newAssetCode=None),
            _asset("SIDE", swapTag="sw", oldAssetCode="OLD", newAssetCode="NEXT"),
        )
    )
    assert assets["NEXT"].pending_migration is None
    assert assets["NEXT2"].pending_migration is None
    assert assets["OLD"].pending_migration is None
    assert assets["OLD2"].pending_migration is None
    assert assets["SIDE"].pending_migration is None


def test_deposit_suspension_follows_the_coin_boolean_and_ignores_balances() -> None:
    coins = parse_coin_config(
        {"success": True, "data": [_coin("BTC"), _coin("HALT", deposits=False, withdrawals=False)]}
    )
    assert coins["BTC"].deposits_suspended is False
    assert coins["BTC"].withdrawals_suspended is False
    assert coins["HALT"].deposits_suspended is True
    assert coins["HALT"].withdrawals_suspended is True
    missing = parse_coin_config(
        {"success": True, "data": [{"coin": "BARE", "depositAllEnable": "yes"}]}
    )
    assert missing["BARE"].deposits_suspended is None
    assert missing["BARE"].withdrawals_suspended is None


def test_repeated_catalog_keys_are_unusable() -> None:
    with pytest.raises(EvaluationError, match="repeats an asset"):
        parse_asset_catalog(_catalog(_asset("BTC"), _asset("BTC")))
    with pytest.raises(EvaluationError, match="repeats a coin"):
        parse_coin_config({"success": True, "data": [_coin("BTC"), _coin("BTC")]})
    with pytest.raises(EvaluationError, match="unusable"):
        parse_asset_catalog({"success": False, "data": []})
    with pytest.raises(EvaluationError, match="unusable"):
        parse_coin_config(["BTC"])


def test_two_coin_ids_for_one_pair_are_not_a_mapping() -> None:
    mapped = map_binance_tickers(
        [
            _venue("BTC", "USDT", "bitcoin"),
            _venue("BTC", "USDT", "bitcoin"),
            _venue("SOL", "USDT", "solana"),
            _venue("SOL", "USDT", "solana-wormhole"),
            _venue("EUR", "USDT"),
            _venue("ETH", "BTC", "ethereum", venue="other"),
            _venue("bad pair", "USDT", "nope"),
            _venue("BTC", "usdt", "lowercase"),
            _venue("XRP", "USDT", ""),
        ]
    )
    assert mapped.ids == {"BTCUSDT": "bitcoin"}
    assert mapped.ambiguous == frozenset({"SOLUSDT"})
    assert "EURUSDT" not in mapped.ids
    assert "ETHBTC" not in mapped.ids


def test_an_incomplete_euro_category_does_not_become_false() -> None:
    assets = parse_asset_catalog(_catalog(_asset("BTC"), _asset("EURI")))
    coins = parse_coin_config({"success": True, "data": [_coin("BTC"), _coin("EURI")]})
    mapping = map_binance_tickers(
        [
            _venue("BTC", "USDT", "bitcoin"),
            _venue("EURI", "USDT", "eurite"),
            _venue("AMBIG", "USDT", "one"),
            _venue("AMBIG", "USDT", "two"),
        ]
    )
    symbols = (
        ("BTCUSDT", "BTC"),
        ("EURIUSDT", "EURI"),
        ("AMBIGUSDT", "AMBIG"),
        ("GHOSTUSDT", "GHOST"),
        ("SOLUSDT", "SOL"),
    )
    incomplete = classify_universe(
        symbols,
        assets=assets,
        coins=coins,
        mapping=mapping,
        eur_ids=None,
        caps={},
        cmc=map_cmc_ids([]),
        cmc_caps={},
    )
    by_symbol = {item.symbol: item for item in incomplete}
    assert by_symbol["BTCUSDT"].eur_stable is None
    assert by_symbol["EURIUSDT"].eur_stable is None
    complete = classify_universe(
        symbols,
        assets=assets,
        coins=coins,
        mapping=mapping,
        eur_ids=frozenset({"eurite"}),
        caps={
            "bitcoin": CapReading("coingecko", "bitcoin", Decimal("100"), STAMP),
            "eurite": CapReading("coingecko", "eurite", Decimal("50"), STAMP),
            "one": CapReading("coingecko", "one", Decimal("9"), STAMP),
        },
        cmc=map_cmc_ids([{"symbol": "BTCUSDT", "cmcUniqueId": 1}]),
        cmc_caps={"1": CapReading("cmc", "1", Decimal("101"), STAMP)},
    )
    done = {item.symbol: item for item in complete}
    assert done["BTCUSDT"].eur_stable is False
    assert done["BTCUSDT"].coin_id == "bitcoin"
    assert done["BTCUSDT"].mapping == "unambiguous"
    assert done["BTCUSDT"].market_cap_usd == Decimal("100")
    assert done["BTCUSDT"].cmc_id == "1"
    assert done["BTCUSDT"].cmc_market_cap_usd == Decimal("101")
    assert done["EURIUSDT"].eur_stable is True
    assert done["AMBIGUSDT"].eur_stable is None
    assert done["AMBIGUSDT"].mapping == "ambiguous"
    assert done["AMBIGUSDT"].market_cap_usd is None
    assert done["GHOSTUSDT"].fan_token is None
    assert done["GHOSTUSDT"].deposits_suspended is None
    assert done["GHOSTUSDT"].mapping == "unmapped"
    assert done["GHOSTUSDT"].market_cap_usd is None
    assert done["SOLUSDT"].cmc_id is None
    assert done["SOLUSDT"].cmc_market_cap_usd is None


def test_market_cap_parsers_keep_the_id_and_refuse_an_undated_value() -> None:
    caps = parse_coingecko_markets(
        [
            {"id": "bitcoin", "market_cap": 10, "last_updated": "2026-10-07T12:00:00.000Z"},
            {"id": "floated", "market_cap": 1.25, "last_updated": "2026-10-07T12:00:00Z"},
            {"id": "bare", "market_cap": None, "last_updated": None},
            {
                "id": "ranked",
                "market_cap": "12.5",
                "last_updated": "2026-10-07T12:00:00Z",
                "market_cap_rank": 3,
                "circulating_supply": 99,
            },
        ]
    )
    assert caps["bitcoin"] == CapReading("coingecko", "bitcoin", Decimal("10"), STAMP)
    assert "bare" not in caps
    assert caps["ranked"].market_cap_usd == Decimal("12.5")
    assert caps["floated"].market_cap_usd == Decimal("1.25")
    with pytest.raises(EvaluationError, match="undated fundamental"):
        parse_coingecko_markets([{"id": "bitcoin", "market_cap": 1, "last_updated": None}])
    with pytest.raises(EvaluationError, match="repeats an asset"):
        parse_coingecko_markets(
            [
                {"id": "bitcoin", "market_cap": 1, "last_updated": "2026-10-07T12:00:00Z"},
                {"id": "bitcoin", "market_cap": 2, "last_updated": "2026-10-07T12:00:00Z"},
            ]
        )
    with pytest.raises(EvaluationError, match="repeats an asset"):
        parse_coingecko_markets(
            [
                {"id": "bitcoin", "market_cap": None, "last_updated": None},
                {"id": "bitcoin", "market_cap": 1, "last_updated": "2026-10-07T12:00:00Z"},
            ]
        )
    with pytest.raises(EvaluationError, match="repeats an asset"):
        parse_coingecko_markets(
            [
                {"id": "bitcoin", "market_cap": 1, "last_updated": "2026-10-07T12:00:00Z"},
                {"id": "bitcoin", "market_cap": None, "last_updated": None},
            ]
        )
    cmc = parse_cmc_quotes(
        {
            "data": {
                "1": {"quote": {"USD": {"market_cap": 4, "last_updated": "2026-10-07T12:00:00Z"}}},
                "2": {"quote": {"USD": {"market_cap": None, "last_updated": None}}},
            }
        }
    )
    assert cmc["1"].provider == "cmc"
    assert cmc["1"].market_cap_usd == Decimal("4")
    assert "2" not in cmc
    with pytest.raises(EvaluationError, match="undated fundamental"):
        parse_cmc_quotes(
            {"data": {"1": {"quote": {"USD": {"market_cap": 1, "last_updated": None}}}}}
        )


def test_cmc_ids_are_not_chosen_by_a_second_guess() -> None:
    mapped = map_cmc_ids(
        [
            {"symbol": "BTCUSDT", "cmcUniqueId": 1},
            {"symbol": "ETHUSDT", "cmcUniqueId": None},
            {"symbol": "SOLUSDT", "cmcUniqueId": 5},
            {"symbol": "SOLUSDT", "cmcUniqueId": 9},
            {"symbol": 1, "cmcUniqueId": 4},
        ]
    )
    assert mapped.ids == {"BTCUSDT": "1"}
    assert mapped.ambiguous == frozenset({"SOLUSDT"})
    with pytest.raises(EvaluationError, match="unusable"):
        map_cmc_ids({"symbol": "BTCUSDT"})
    with pytest.raises(EvaluationError, match="unusable"):
        map_cmc_ids(["BTCUSDT"])


def test_facts_keep_later_lane_fields_missing(tmp_path: Path) -> None:
    assets = parse_asset_catalog(_catalog(_asset("SOL")))
    coins = parse_coin_config({"success": True, "data": [_coin("SOL")]})
    mapping = map_binance_tickers([_venue("SOL", "USDT", "solana")])
    item = classify_universe(
        (("SOLUSDT", "SOL"),),
        assets=assets,
        coins=coins,
        mapping=mapping,
        eur_ids=frozenset(),
        caps={"solana": CapReading("coingecko", "solana", Decimal("300000000"), STAMP)},
        cmc=map_cmc_ids([]),
        cmc_caps={},
    )[0]
    facts = to_facts(item, quote_asset="USDT", status="TRADING")
    assert facts.market_cap_usd == Decimal("300000000")
    assert facts.market_cap_rank is None
    assert facts.circulating_ratio is None
    assert facts.fdv_to_market_cap is None
    assert facts.history_days is None
    assert facts.unlock_schedule_known is None
    decision = assess(facts, UNIVERSE)
    assert "missing_market_cap" not in decision.reason_codes
    assert "missing_fan_token_classification" not in decision.reason_codes
    assert "missing_market_cap_rank" in decision.reason_codes
    store_classification(tmp_path, SESSION, item, WHEN)
    stored = tmp_path / "captures" / "session=2026-10-07" / "classification" / "symbol=SOLUSDT.json"
    assert stored.is_file()
    store_classification(tmp_path, SESSION, item, WHEN)
    with pytest.raises(EvaluationError, match="different payload"):
        store_classification(tmp_path, SESSION, item, WHEN + timedelta(seconds=1))


def test_a_sealed_session_is_not_enriched(tmp_path: Path) -> None:
    assets = parse_asset_catalog(_catalog(_asset("SOL")))
    coins = parse_coin_config({"success": True, "data": [_coin("SOL")]})
    mapping = map_binance_tickers([_venue("SOL", "USDT", "solana")])
    item = classify_universe(
        (("SOLUSDT", "SOL"),),
        assets=assets,
        coins=coins,
        mapping=mapping,
        eur_ids=frozenset(),
        caps={},
        cmc=map_cmc_ids([]),
        cmc_caps={},
    )[0]
    with pytest.raises(EvaluationError, match="sealed session stays sealed"):
        store_classification(tmp_path, date(2026, 10, 6), item, WHEN)
    later = date(2026, 10, 8)
    manifest = tmp_path / "sessions" / f"date={later.isoformat()}" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}")
    with pytest.raises(EvaluationError, match="finalized session is sealed"):
        store_classification(tmp_path, later, item, datetime(2026, 10, 8, 1, tzinfo=UTC))
    with pytest.raises(EvaluationError, match="historical session stays blocked"):
        store_classification(tmp_path, date(2026, 10, 5), item, WHEN)
    with pytest.raises(EvaluationError, match="capture is after the close"):
        store_classification(tmp_path, SESSION, item, datetime(2026, 10, 8, 0, 0, 1, tzinfo=UTC))
    with pytest.raises(EvaluationError, match="timezone-aware UTC"):
        store_classification(tmp_path, SESSION, item, datetime(2026, 10, 7, 13, 0))  # noqa: DTZ001
    off = datetime(2026, 10, 7, 13, 0, tzinfo=timezone(timedelta(hours=1)))
    with pytest.raises(EvaluationError, match="timezone-aware UTC"):
        store_classification(tmp_path, SESSION, item, off)


def test_a_malformed_catalog_is_unusable() -> None:
    broken = {"success": True, "data": ["row"]}
    blank = {"success": True, "data": [{"assetCode": ""}]}
    numbered = {"success": True, "data": [{"assetCode": 1}]}
    for payload in (broken, blank, numbered, {"success": True, "data": None}):
        with pytest.raises(EvaluationError, match="unusable"):
            parse_asset_catalog(payload)
    with pytest.raises(EvaluationError, match="unusable"):
        parse_coin_config({"success": True, "data": ["row"]})
    with pytest.raises(EvaluationError, match="unusable"):
        parse_coin_config({"success": True, "data": [{"coin": ""}]})
    with pytest.raises(EvaluationError, match="unusable"):
        map_binance_tickers(["row"])
    ignored = map_binance_tickers(
        [
            {"base": 1, "target": "USDT", "coin_id": "x", "market": {"identifier": "binance"}},
            {"base": "BTC", "target": 1, "coin_id": "x", "market": {"identifier": "binance"}},
            {"base": "BTC", "target": "USDT", "coin_id": "bitcoin"},
        ]
    )
    assert ignored.ids == {}
    stamp = "2026-10-07T12:00:00Z"
    refusals = (
        {"id": "bitcoin"},
        ["row"],
        [{"id": "", "market_cap": 1, "last_updated": stamp}],
        [{"id": 1, "market_cap": 1, "last_updated": stamp}],
        [{"id": "bitcoin", "market_cap": True, "last_updated": stamp}],
        [{"id": "bitcoin", "market_cap": "nope", "last_updated": stamp}],
        [{"id": "bitcoin", "market_cap": [], "last_updated": stamp}],
        [{"id": "bitcoin", "market_cap": 1, "last_updated": 1}],
        [{"id": "bitcoin", "market_cap": 1, "last_updated": "yesterday"}],
        [{"id": "bitcoin", "market_cap": 1, "last_updated": "2026-10-07T12:00:00"}],
        [{"id": "bitcoin", "market_cap": 1, "last_updated": "2026-10-07T12:00:00+01:00"}],
    )
    for payload in refusals:
        with pytest.raises(EvaluationError, match="unusable"):
            parse_coingecko_markets(payload)
    for payload in ([], {"data": []}, {"data": {"": {}}}, {"data": {1: {}}}, {"data": {"1": []}}):
        with pytest.raises(EvaluationError, match="unusable"):
            parse_cmc_quotes(payload)
    assert parse_cmc_quotes({"data": {"3": {"quote": "no"}}}) == {}


def test_a_capture_that_appears_during_the_write_is_kept_or_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = classify_universe(
        (("SOLUSDT", "SOL"),),
        assets=parse_asset_catalog(_catalog(_asset("SOL"))),
        coins=parse_coin_config({"success": True, "data": [_coin("SOL")]}),
        mapping=map_binance_tickers([_venue("SOL", "USDT", "solana")]),
        eur_ids=frozenset(),
        caps={},
        cmc=map_cmc_ids([]),
        cmc_caps={},
    )[0]

    def same(source: str, destination: str) -> None:
        Path(destination).write_bytes(Path(source).read_bytes())
        raise FileExistsError

    monkeypatch.setattr("cip.evaluation.classification.os.link", same)
    store_classification(tmp_path, SESSION, item, WHEN)
    monkeypatch.undo()

    def different(source: str, destination: str) -> None:
        del source
        Path(destination).write_bytes(b"{}")
        raise FileExistsError

    other = tmp_path / "other"
    monkeypatch.setattr("cip.evaluation.classification.os.link", different)
    with pytest.raises(EvaluationError, match="different payload"):
        store_classification(other, SESSION, item, WHEN)
