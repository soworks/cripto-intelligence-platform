"""Lane fields for the symbols that already cleared the market-cap floor."""

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from cip.domain.errors import EvaluationError
from cip.domain.policy import load_policy
from cip.evaluation.classification import SymbolClassification, to_facts
from cip.evaluation.eligibility import Lane, assess
from cip.evaluation.lane_fields import (
    classify_unlock,
    history_days,
    overlay_lane,
    parse_first_daily_open,
    parse_market_fields,
    store_lane,
    unlock_schedule_known,
)

_POLICY = Path(__file__).parents[3] / "policies" / "investment-policy.yaml"
UNIVERSE = load_policy(_POLICY).policy.hypotheses.universe
SESSION = date(2026, 10, 7)
STAMP = datetime(2026, 10, 7, 14, 30, tzinfo=UTC)
CLOSE = datetime(2026, 10, 8, tzinfo=UTC)
OPEN = 1502928000000  # 2017-08-17T00:00:00Z


def _row(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "id": "bitcoin",
        "circulating_supply": 100,
        "total_supply": 250,
        "fully_diluted_valuation": 500,
        "market_cap": 200,
        "market_cap_rank": 1,
        "last_updated": "2026-10-07T14:30:00Z",
    }
    body.update(overrides)
    return body


def _classification() -> SymbolClassification:
    return SymbolClassification(
        symbol="BTCUSDT",
        base_asset="BTC",
        eur_stable=False,
        fan_token=False,
        monitoring_tag=False,
        delisting=False,
        deposits_suspended=False,
        withdrawals_suspended=False,
        pending_migration=False,
        coin_id="bitcoin",
        mapping="unambiguous",
        market_cap_usd=Decimal("300000000"),
        market_cap_source_timestamp=STAMP,
        cmc_id=None,
        cmc_market_cap_usd=None,
        cmc_source_timestamp=None,
    )


def test_supply_rank_and_fdv_come_from_the_same_dated_row() -> None:
    found = parse_market_fields(
        [
            _row(),
            _row(
                id="bare",
                circulating_supply=None,
                total_supply=None,
                fully_diluted_valuation=None,
                market_cap=None,
                market_cap_rank=None,
            ),
            _row(id="nocap", market_cap=None),
        ]
    )
    bitcoin = found["bitcoin"]
    assert bitcoin.circulating_ratio == Decimal("0.4")
    assert bitcoin.fdv_to_market_cap == Decimal("2.5")
    assert bitcoin.market_cap_rank == 1
    assert bitcoin.source_timestamp == STAMP
    assert found["bare"].circulating_ratio is None
    assert found["bare"].fdv_to_market_cap is None
    assert found["bare"].market_cap_rank is None
    assert found["nocap"].fdv_to_market_cap is None
    assert found["nocap"].circulating_ratio == Decimal("0.4")
    missing_total = parse_market_fields([_row(id="half", total_supply=None, market_cap=0)])
    assert missing_total["half"].circulating_ratio is None
    assert missing_total["half"].fdv_to_market_cap is None
    zero_total = parse_market_fields([_row(id="zero", total_supply=0)])
    assert zero_total["zero"].circulating_ratio is None


def test_a_repeated_or_undated_market_row_is_unusable() -> None:
    with pytest.raises(EvaluationError, match="repeats an asset"):
        parse_market_fields([_row(circulating_supply=None), _row()])
    with pytest.raises(EvaluationError, match="undated fundamental"):
        parse_market_fields([_row(last_updated=None)])
    with pytest.raises(EvaluationError, match="unusable"):
        parse_market_fields([_row(market_cap_rank=True)])
    with pytest.raises(EvaluationError, match="unusable"):
        parse_market_fields([_row(circulating_supply=-1)])


def test_history_is_the_exchange_listing_open_not_a_stored_bar_count() -> None:
    assert parse_first_daily_open([[OPEN, "1", "1", "1", "1", "1"]]) == OPEN
    assert parse_first_daily_open([]) is None
    assert history_days(OPEN, SESSION) == (SESSION - date(2017, 8, 17)).days
    after = int(datetime(2026, 10, 8, tzinfo=UTC).timestamp() * 1000)
    with pytest.raises(EvaluationError, match="history starts after the session"):
        history_days(after, SESSION)
    listed_today = int(datetime(2026, 10, 7, tzinfo=UTC).timestamp() * 1000)
    assert history_days(listed_today, SESSION) == 0
    with pytest.raises(EvaluationError, match="unusable"):
        parse_first_daily_open([[OPEN + 60_000]])
    with pytest.raises(EvaluationError, match="unusable"):
        history_days(-86_400_000, SESSION)


def test_missing_unlock_rows_stay_unavailable() -> None:
    assert classify_unlock(None, tracked=False, as_of=CLOSE) == "unavailable"
    assert classify_unlock(None, tracked=True, as_of=CLOSE) == "unavailable"
    assert classify_unlock((), tracked=False, as_of=CLOSE) == "unavailable"
    assert unlock_schedule_known("unavailable") is None
    assert classify_unlock((), tracked=True, as_of=CLOSE) == "no_applicable_future_unlock"
    past = datetime(2026, 10, 1, tzinfo=UTC)
    settled = classify_unlock((past, CLOSE), tracked=True, as_of=CLOSE)
    assert settled == "no_applicable_future_unlock"
    future = datetime(2026, 10, 8, 0, 0, 1, tzinfo=UTC)
    assert classify_unlock((past, future), tracked=True, as_of=CLOSE) == "future_unlocks"
    assert unlock_schedule_known("future_unlocks") is True
    assert unlock_schedule_known("no_applicable_future_unlock") is True
    with pytest.raises(EvaluationError, match="unusable"):
        unlock_schedule_known("unlock_schedule_unknown")
    with pytest.raises(EvaluationError, match="unusable"):
        classify_unlock((datetime(2026, 10, 9),), tracked=True, as_of=CLOSE)  # noqa: DTZ001


def test_overlay_keeps_each_lane_to_its_own_fields() -> None:
    facts = to_facts(_classification(), quote_asset="USDT", status="TRADING")
    normal = overlay_lane(
        facts,
        lane=Lane.NORMAL,
        circulating_ratio=Decimal("0.5"),
        history_days=400,
        market_cap_rank=10,
        fdv_to_market_cap=Decimal("2"),
        unlock="unavailable",
    )
    assert normal.market_cap_rank == 10
    assert normal.fdv_to_market_cap == Decimal("2")
    assert normal.unlock_schedule_known is None
    assert "missing_market_cap_rank" not in assess(normal, UNIVERSE).reason_codes
    high = overlay_lane(
        facts.model_copy(update={"market_cap_usd": Decimal("80000000")}),
        lane=Lane.HIGH_RISK,
        circulating_ratio=Decimal("0.5"),
        history_days=100,
        market_cap_rank=None,
        fdv_to_market_cap=None,
        unlock="unavailable",
    )
    assert high.market_cap_rank is None
    assert high.fdv_to_market_cap is None
    assert high.unlock_schedule_known is None
    assert "missing_unlock_schedule" in assess(high, UNIVERSE).reason_codes
    known = overlay_lane(
        facts.model_copy(update={"market_cap_usd": Decimal("80000000")}),
        lane=Lane.HIGH_RISK,
        circulating_ratio=Decimal("0.5"),
        history_days=100,
        market_cap_rank=None,
        fdv_to_market_cap=None,
        unlock="no_applicable_future_unlock",
    )
    assert known.unlock_schedule_known is True
    assert "unlock_schedule_unknown" not in assess(known, UNIVERSE).reason_codes
    with pytest.raises(EvaluationError, match="normal lane does not record an unlock"):
        overlay_lane(
            facts,
            lane=Lane.NORMAL,
            circulating_ratio=None,
            history_days=None,
            market_cap_rank=None,
            fdv_to_market_cap=None,
            unlock="future_unlocks",
        )
    with pytest.raises(EvaluationError, match="high-risk lane does not record rank"):
        overlay_lane(
            facts,
            lane=Lane.HIGH_RISK,
            circulating_ratio=None,
            history_days=None,
            market_cap_rank=1,
            fdv_to_market_cap=None,
            unlock="unavailable",
        )


def test_a_sealed_session_does_not_receive_lane_fields(tmp_path: Path) -> None:
    item = _lane()
    with pytest.raises(EvaluationError, match="sealed session stays sealed"):
        store_lane(tmp_path, date(2026, 10, 6), item)
    manifest = tmp_path / "sessions" / "date=2026-10-07" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}")
    with pytest.raises(EvaluationError, match="finalized session is sealed"):
        store_lane(tmp_path, SESSION, item)


def test_lane_storage_refuses_a_late_or_unsafe_write(tmp_path: Path) -> None:
    store_lane(tmp_path, SESSION, _lane())
    store_lane(tmp_path, SESSION, _lane())
    with pytest.raises(EvaluationError, match="different payload"):
        store_lane(tmp_path, SESSION, _lane(circulating_ratio=Decimal("0.5")))
    with pytest.raises(EvaluationError, match="unusable"):
        store_lane(tmp_path, SESSION, _lane(history_days=1))
    late = _lane(market_captured_at=datetime(2026, 10, 8, 0, 0, 1, tzinfo=UTC))
    with pytest.raises(EvaluationError, match="capture is after the close"):
        store_lane(tmp_path, SESSION, late)
    with pytest.raises(EvaluationError, match="unusable"):
        store_lane(tmp_path, SESSION, _lane(symbol="BTC/USDT"))


def test_malformed_lane_inputs_stay_unusable(tmp_path: Path) -> None:
    omitted = parse_market_fields(
        [
            _row(
                id="empty",
                circulating_supply=None,
                total_supply=None,
                fully_diluted_valuation=None,
                market_cap=None,
                market_cap_rank=None,
                last_updated=None,
            )
        ]
    )
    assert omitted == {}
    floated = parse_market_fields(
        [_row(id="floated", circulating_supply=1.5, total_supply="3", market_cap="3")]
    )
    assert floated["floated"].circulating_ratio == Decimal("0.5")
    refusals: tuple[object, ...] = (
        {"id": "bitcoin"},
        ["row"],
        [_row(id="")],
        [_row(id=1)],
        [_row(circulating_supply=True)],
        [_row(circulating_supply="nope")],
        [_row(total_supply=[])],
        [_row(market_cap_rank=0)],
        [_row(last_updated=1)],
        [_row(last_updated="yesterday")],
        [_row(last_updated="2026-10-07T14:30:00")],
        [_row(last_updated="2026-10-07T14:30:00+01:00")],
        {"open": OPEN},
        [OPEN],
        [[]],
    )
    for payload in refusals[:12]:
        with pytest.raises(EvaluationError, match=r"unusable|undated"):
            parse_market_fields(payload)
    for payload in refusals[12:]:
        with pytest.raises(EvaluationError, match="unusable"):
            parse_first_daily_open(payload)
    with pytest.raises(EvaluationError, match="unusable"):
        history_days(True, SESSION)
    naive = datetime(2026, 10, 9, tzinfo=UTC).replace(tzinfo=None)
    with pytest.raises(EvaluationError, match="unusable"):
        classify_unlock((naive,), tracked=True, as_of=CLOSE)
    store_lane(
        tmp_path,
        SESSION,
        _lane(
            symbol="SOLUSDT",
            base_asset="SOL",
            lane=Lane.HIGH_RISK,
            coin_id="solana",
            market_cap_rank=None,
            fdv_to_market_cap=None,
            market_source_timestamp=None,
            circulating_ratio=None,
            history_days=None,
            history_open=None,
            unlock_state="future_unlocks",
            unlock_provider="manual",
            unlock_provider_id="solana",
            unlock_source_timestamp=STAMP,
        ),
    )
    blocked = (
        _lane(coin_id=""),
        _lane(base_asset="btc"),
        _lane(history_days=1, history_open=None),
        _lane(unlock_state="future_unlocks"),
        _lane(
            lane=Lane.HIGH_RISK,
            market_cap_rank=1,
            fdv_to_market_cap=None,
            unlock_state="unavailable",
        ),
        _lane(unlock_provider="manual"),
        _lane(
            lane=Lane.HIGH_RISK,
            market_cap_rank=None,
            fdv_to_market_cap=None,
            unlock_state="no_applicable_future_unlock",
            unlock_provider="",
            unlock_provider_id="solana",
            unlock_source_timestamp=STAMP,
        ),
        _lane(
            lane=Lane.HIGH_RISK,
            market_cap_rank=None,
            fdv_to_market_cap=None,
            unlock_state="no_applicable_future_unlock",
            unlock_provider="manual",
            unlock_provider_id="",
            unlock_source_timestamp=STAMP,
        ),
        _lane(
            lane=Lane.HIGH_RISK,
            market_cap_rank=None,
            fdv_to_market_cap=None,
            unlock_state="future_unlocks",
            unlock_provider="manual",
            unlock_provider_id="solana",
            unlock_source_timestamp=None,
        ),
        _lane(market_captured_at=datetime(2026, 10, 7, 14, 30)),  # noqa: DTZ001
        _lane(market_source_timestamp=None),
        _lane(
            lane=Lane.HIGH_RISK,
            market_cap_rank=None,
            fdv_to_market_cap=None,
            circulating_ratio=None,
            unlock_state="future_unlocks",
            unlock_provider="manual",
            unlock_provider_id="solana",
            unlock_source_timestamp=datetime(2026, 10, 7, 14, 30),  # noqa: DTZ001
        ),
        _lane(
            lane=Lane.HIGH_RISK,
            market_cap_rank=None,
            fdv_to_market_cap=None,
            circulating_ratio=None,
            unlock_state="future_unlocks",
            unlock_provider="manual",
            unlock_provider_id="solana",
            unlock_source_timestamp=datetime(2026, 10, 8, 0, 0, 1, tzinfo=UTC),
        ),
        _lane(
            lane=Lane.HIGH_RISK,
            market_cap_rank=None,
            fdv_to_market_cap=None,
            circulating_ratio=None,
            unlock_state="unlock_schedule_unknown",
            unlock_provider="manual",
            unlock_provider_id="solana",
            unlock_source_timestamp=STAMP,
        ),
    )
    for item in blocked:
        with pytest.raises(EvaluationError):
            store_lane(tmp_path, SESSION, item)


def test_a_capture_that_appears_during_the_write_is_kept_or_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = _lane(symbol="ETHUSDT", base_asset="ETH", coin_id="ethereum")

    def same(source: str, destination: str) -> None:
        Path(destination).write_bytes(Path(source).read_bytes())
        raise FileExistsError

    monkeypatch.setattr("cip.evaluation.lane_fields.os.link", same)
    store_lane(tmp_path, SESSION, item)
    monkeypatch.undo()

    def different(source: str, destination: str) -> None:
        del source
        Path(destination).write_bytes(b"{}")
        raise FileExistsError

    monkeypatch.setattr("cip.evaluation.lane_fields.os.link", different)
    with pytest.raises(EvaluationError, match="different payload"):
        store_lane(tmp_path / "other", SESSION, item)


def _lane(**overrides: object) -> object:
    from cip.evaluation.lane_fields import LaneFields

    body: dict[str, object] = {
        "symbol": "BTCUSDT",
        "base_asset": "BTC",
        "lane": Lane.NORMAL,
        "coin_id": "bitcoin",
        "circulating_ratio": Decimal("0.4"),
        "history_days": (SESSION - date(2017, 8, 17)).days,
        "history_open": datetime(2017, 8, 17, tzinfo=UTC),
        "market_cap_rank": 1,
        "fdv_to_market_cap": Decimal("1.1"),
        "unlock_state": "unavailable",
        "market_source_timestamp": STAMP,
        "market_captured_at": STAMP,
        "history_captured_at": STAMP,
        "unlock_provider": None,
        "unlock_provider_id": None,
        "unlock_source_timestamp": None,
    }
    body.update(overrides)
    return LaneFields(**body)  # type: ignore[arg-type]
