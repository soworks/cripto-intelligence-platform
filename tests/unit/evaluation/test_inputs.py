import json
import os
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from cip.domain.errors import EvaluationError
from cip.evaluation.eligibility import CandidateFacts
from cip.evaluation.fundamentals import CmcReading, CoinGeckoReading, UnlockReading
from cip.evaluation.inputs import (
    AbsentInput,
    RecordedInputs,
    load_session,
    write_absence,
    write_candidate,
    write_universe,
)
from cip.evaluation.liquidity import MarketSnapshot
from cip.evaluation.scan import ScanCandidate, UniverseSnapshot
from cip.evaluation.session import assess_session
from cip.history.bars import DailyBar
from cip.recorders.observation import Observation

SESSION = date(2026, 10, 5)
CLOSE = datetime(2026, 10, 6, tzinfo=UTC)
SYMBOL = "SOLUSDT"


def _snapshot(*symbols: str, observed_at: datetime = CLOSE) -> UniverseSnapshot:
    return UniverseSnapshot(
        session=SESSION,
        symbols=symbols or (SYMBOL,),
        observed_at=observed_at,
        provenance="stored-exchange-info",
    )


def _candidate(
    *,
    symbol: str = SYMBOL,
    as_of: datetime = CLOSE,
    gecko_cap: Decimal | None = Decimal("1"),
    gecko_stamp: datetime | None = CLOSE,
    cmc_cap: Decimal | None = Decimal("1"),
    cmc_stamp: datetime | None = CLOSE,
) -> ScanCandidate:
    blanks: dict[str, object] = {
        "status": None,
        "eur_stable": None,
        "fan_token": None,
        "monitoring_tag": None,
        "delisting": None,
        "deposits_suspended": None,
        "withdrawals_suspended": None,
        "pending_migration": None,
        "market_cap_usd": None,
        "market_cap_rank": None,
        "circulating_ratio": None,
        "fdv_to_market_cap": None,
        "history_days": None,
        "unlock_schedule_known": None,
    }
    return ScanCandidate(
        facts=CandidateFacts(
            symbol=symbol,
            base_asset=symbol.removesuffix("USDT"),
            quote_asset="USDT",
            **blanks,  # type: ignore[arg-type]
        ),
        market=MarketSnapshot(
            as_of=as_of,
            median_quote_volume_30d_usd=None,
            day_quote_volume_usd=None,
            median_spread_bps=None,
            spread_snapshots=None,
            depth_usd_per_side=None,
            turnover=None,
            volume_zscore=None,
            price_move=None,
            spike_candle_count=None,
            trade_size_stdev=None,
            taker_buy_ratio=None,
            binance_volume_share=None,
            stablecoin_peg_deviation=None,
            peg_deviation_hours=None,
        ),
        coingecko=CoinGeckoReading(
            coingecko_id="solana",
            market_cap_usd=gecko_cap,
            circulating_supply=None,
            total_supply=None,
            fully_diluted_valuation_usd=None,
            source_timestamp=gecko_stamp,
        ),
        cmc=CmcReading(market_cap_usd=cmc_cap, source_timestamp=cmc_stamp),
        unlocks=UnlockReading(schedule_known=None, pct_circ_14d=None, pct_circ_90d=None),
        tokenomics=None,
    )


def _absence(
    kind: str = "candidate",
    symbol: str = SYMBOL,
    produced_at: datetime | None = None,
) -> AbsentInput:
    return AbsentInput(
        kind="absent_input",
        session=SESSION,
        input=kind,  # type: ignore[arg-type]
        symbol=symbol,
        produced_at=CLOSE + timedelta(minutes=5) if produced_at is None else produced_at,
    )


def _universe_path(root: Path) -> Path:
    return root / "sessions" / "date=2026-10-05" / "universe.json"


def _candidate_path(root: Path, symbol: str = SYMBOL) -> Path:
    return root / "sessions" / "date=2026-10-05" / "candidates" / f"symbol={symbol}.json"


def _absence_path(root: Path, kind: str, symbol: str = SYMBOL) -> Path:
    return (
        root
        / "sessions"
        / "date=2026-10-05"
        / "absences"
        / f"input={kind}"
        / f"symbol={symbol}.json"
    )


def test_a_snapshot_is_stored_once_and_reads_back(tmp_path: Path) -> None:
    snapshot = _snapshot()
    assert write_universe(tmp_path, snapshot) is True
    assert write_universe(tmp_path, snapshot) is False
    loaded = load_session(tmp_path, SESSION)
    assert loaded.snapshot == snapshot
    assert json.loads(_universe_path(tmp_path).read_text())["observed_at"] == "2026-10-06T00:00:00Z"


def test_a_different_snapshot_does_not_replace_the_stored_one(tmp_path: Path) -> None:
    write_universe(tmp_path, _snapshot())
    original = _universe_path(tmp_path).read_bytes()
    changed = _snapshot().model_copy(update={"provenance": "later-exchange-info"})
    with pytest.raises(EvaluationError, match="already exists with a different payload"):
        write_universe(tmp_path, changed)
    assert _universe_path(tmp_path).read_bytes() == original


def test_a_snapshot_after_the_close_is_not_stored(tmp_path: Path) -> None:
    later = _snapshot(observed_at=CLOSE + timedelta(seconds=1))
    with pytest.raises(EvaluationError, match="snapshot is after the close"):
        write_universe(tmp_path, later)
    assert list(tmp_path.rglob("*.json")) == []


def test_a_naive_snapshot_clock_is_not_stored(tmp_path: Path) -> None:
    naive = _snapshot(observed_at=datetime(2026, 10, 6))  # noqa: DTZ001
    with pytest.raises(EvaluationError, match="observed_at must be timezone-aware UTC"):
        write_universe(tmp_path, naive)
    assert list(tmp_path.rglob("*.json")) == []


def test_a_non_utc_snapshot_clock_is_not_stored(tmp_path: Path) -> None:
    shifted = _snapshot(observed_at=datetime(2026, 10, 6, tzinfo=timezone(timedelta(hours=1))))
    with pytest.raises(EvaluationError, match="observed_at must be timezone-aware UTC"):
        write_universe(tmp_path, shifted)
    assert list(tmp_path.rglob("*.json")) == []


def test_a_repeated_snapshot_symbol_is_not_stored(tmp_path: Path) -> None:
    with pytest.raises(EvaluationError, match="universe snapshot repeats a symbol"):
        write_universe(tmp_path, _snapshot(SYMBOL, SYMBOL))
    assert list(tmp_path.rglob("*.json")) == []


def test_a_boolean_session_is_not_a_snapshot_date(tmp_path: Path) -> None:
    snapshot = UniverseSnapshot.model_construct(
        session=True,
        symbols=(SYMBOL,),
        observed_at=CLOSE,
        provenance="stored-exchange-info",
    )
    with pytest.raises(EvaluationError, match="session is a date"):
        write_universe(tmp_path, snapshot)
    assert list(tmp_path.rglob("*.json")) == []


def test_a_candidate_is_stored_once_and_reads_back(tmp_path: Path) -> None:
    packet = _candidate()
    assert write_candidate(tmp_path, SESSION, packet) is True
    assert write_candidate(tmp_path, SESSION, packet) is False
    loaded = load_session(tmp_path, SESSION)
    assert loaded.snapshot is None
    assert loaded.candidates == {SYMBOL: packet}
    assert loaded.candidate_files_present == {SYMBOL: True}
    assert loaded.bar_absences == {}


def test_a_different_candidate_does_not_replace_the_stored_one(tmp_path: Path) -> None:
    write_candidate(tmp_path, SESSION, _candidate())
    original = _candidate_path(tmp_path).read_bytes()
    with pytest.raises(EvaluationError, match="already exists with a different payload"):
        write_candidate(tmp_path, SESSION, _candidate(gecko_cap=Decimal("2")))
    assert _candidate_path(tmp_path).read_bytes() == original


def test_a_candidate_clock_after_the_close_is_not_stored(tmp_path: Path) -> None:
    later = _candidate(as_of=CLOSE + timedelta(seconds=1), gecko_cap=None, gecko_stamp=None)
    with pytest.raises(EvaluationError, match="candidate is after the close"):
        write_candidate(tmp_path, SESSION, later)
    assert list(tmp_path.rglob("*.json")) == []


def test_a_fundamental_stamp_after_the_close_is_not_stored(tmp_path: Path) -> None:
    later = _candidate(gecko_stamp=CLOSE + timedelta(seconds=1))
    with pytest.raises(EvaluationError, match="candidate is after the close"):
        write_candidate(tmp_path, SESSION, later)
    cmc = _candidate(cmc_stamp=CLOSE + timedelta(seconds=1))
    with pytest.raises(EvaluationError, match="candidate is after the close"):
        write_candidate(tmp_path, SESSION, cmc)
    assert list(tmp_path.rglob("*.json")) == []


def test_an_undated_fundamental_is_not_stored(tmp_path: Path) -> None:
    gecko = _candidate(gecko_stamp=None)
    with pytest.raises(EvaluationError, match="undated fundamental"):
        write_candidate(tmp_path, SESSION, gecko)
    cmc = _candidate(cmc_stamp=None)
    with pytest.raises(EvaluationError, match="undated fundamental"):
        write_candidate(tmp_path, SESSION, cmc)
    assert list(tmp_path.rglob("*.json")) == []


def test_a_missing_cap_with_no_stamp_is_stored(tmp_path: Path) -> None:
    packet = _candidate(gecko_cap=None, gecko_stamp=None, cmc_cap=None, cmc_stamp=None)
    assert write_candidate(tmp_path, SESSION, packet) is True
    assert load_session(tmp_path, SESSION).candidates[SYMBOL] == packet


def test_an_explicit_absence_matches_the_contract_and_may_follow_the_close(tmp_path: Path) -> None:
    absence = _absence()
    assert write_absence(tmp_path, absence) is True
    assert write_absence(tmp_path, absence) is False
    document = json.loads(_absence_path(tmp_path, "candidate").read_text())
    assert document == {
        "input": "candidate",
        "kind": "absent_input",
        "produced_at": "2026-10-06T00:05:00Z",
        "session": "2026-10-05",
        "symbol": "SOLUSDT",
    }
    loaded = load_session(tmp_path, SESSION)
    assert loaded.candidates == {SYMBOL: None}
    assert loaded.candidate_files_present == {SYMBOL: True}


def test_a_candidate_and_its_absence_cannot_both_be_stored(tmp_path: Path) -> None:
    write_candidate(tmp_path, SESSION, _candidate())
    with pytest.raises(EvaluationError, match="candidate already recorded"):
        write_absence(tmp_path, _absence())
    assert not _absence_path(tmp_path, "candidate").exists()
    write_absence(tmp_path, _absence(kind="daily_bar", symbol="ETHUSDT"))
    assert _absence_path(tmp_path, "daily_bar", "ETHUSDT").is_file()


def test_an_absence_blocks_a_later_candidate(tmp_path: Path) -> None:
    write_absence(tmp_path, _absence())
    with pytest.raises(EvaluationError, match="candidate absence already recorded"):
        write_candidate(tmp_path, SESSION, _candidate())
    assert not _candidate_path(tmp_path).exists()


def test_a_daily_bar_absence_is_recorded_beside_a_candidate(tmp_path: Path) -> None:
    packet = _candidate()
    write_candidate(tmp_path, SESSION, packet)
    assert write_absence(tmp_path, _absence(kind="daily_bar", symbol="ETHUSDT")) is True
    loaded = load_session(tmp_path, SESSION)
    assert loaded.bar_absences == {"ETHUSDT": True}
    assert loaded.candidates == {SYMBOL: packet}


def test_a_boolean_absence_session_is_refused(tmp_path: Path) -> None:
    absence = AbsentInput.model_construct(
        kind="absent_input",
        session=True,
        input="candidate",
        symbol=SYMBOL,
        produced_at=CLOSE,
    )
    with pytest.raises(EvaluationError, match="session is a date"):
        write_absence(tmp_path, absence)
    assert list(tmp_path.rglob("*.json")) == []


def test_a_naive_absence_clock_is_refused(tmp_path: Path) -> None:
    absence = _absence(produced_at=datetime(2026, 10, 6))  # noqa: DTZ001
    with pytest.raises(EvaluationError, match="produced_at must be timezone-aware UTC"):
        write_absence(tmp_path, absence)
    assert list(tmp_path.rglob("*.json")) == []


def test_a_boolean_candidate_session_is_refused(tmp_path: Path) -> None:
    with pytest.raises(EvaluationError, match="session is a date"):
        write_candidate(tmp_path, True, _candidate())  # type: ignore[arg-type]
    assert list(tmp_path.rglob("*.json")) == []


def test_a_corrupt_session_file_is_unusable(tmp_path: Path) -> None:
    write_universe(tmp_path, _snapshot())
    _universe_path(tmp_path).write_text("{")
    with pytest.raises(EvaluationError, match="session input is unusable"):
        load_session(tmp_path, SESSION)


def test_a_candidate_file_must_match_its_symbol(tmp_path: Path) -> None:
    write_candidate(tmp_path, SESSION, _candidate())
    document = json.loads(_candidate_path(tmp_path).read_text())
    document["facts"]["symbol"] = "ETHUSDT"
    document["facts"]["base_asset"] = "ETH"
    _candidate_path(tmp_path).write_text(json.dumps(document))
    with pytest.raises(EvaluationError, match="candidate file does not match its symbol"):
        load_session(tmp_path, SESSION)


def test_a_universe_file_must_match_its_session(tmp_path: Path) -> None:
    write_universe(tmp_path, _snapshot())
    document = json.loads(_universe_path(tmp_path).read_text())
    document["session"] = "2026-10-04"
    _universe_path(tmp_path).write_text(json.dumps(document))
    with pytest.raises(EvaluationError, match="universe snapshot is for a different session"):
        load_session(tmp_path, SESSION)


def test_a_candidate_packet_and_absence_together_are_unusable(tmp_path: Path) -> None:
    write_candidate(tmp_path, SESSION, _candidate())
    path = _absence_path(tmp_path, "candidate")
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "input": "candidate",
                "kind": "absent_input",
                "produced_at": "2026-10-06T00:05:00Z",
                "session": "2026-10-05",
                "symbol": SYMBOL,
            }
        )
    )
    with pytest.raises(EvaluationError, match="candidate absence conflicts with a packet"):
        load_session(tmp_path, SESSION)


def test_an_unknown_candidate_filename_is_unusable(tmp_path: Path) -> None:
    directory = _candidate_path(tmp_path).parent
    directory.mkdir(parents=True)
    (directory / "symbol=sol.json").write_text("{}")
    with pytest.raises(EvaluationError, match="session input is unusable"):
        load_session(tmp_path, SESSION)


@pytest.mark.parametrize(
    "stamp",
    [1, "yesterday", "2026-10-06T00:00:00", "2026-10-06T00:00:00+01:00"],
)
def test_a_stored_candidate_clock_must_be_utc(tmp_path: Path, stamp: object) -> None:
    write_candidate(tmp_path, SESSION, _candidate())
    document = json.loads(_candidate_path(tmp_path).read_text())
    document["coingecko"]["source_timestamp"] = stamp
    _candidate_path(tmp_path).write_text(json.dumps(document))
    with pytest.raises(EvaluationError, match="session input is unusable"):
        load_session(tmp_path, SESSION)


def test_an_absence_file_must_match_its_name(tmp_path: Path) -> None:
    write_absence(tmp_path, _absence())
    document = json.loads(_absence_path(tmp_path, "candidate").read_text())
    document["symbol"] = "ETHUSDT"
    _absence_path(tmp_path, "candidate").write_text(json.dumps(document))
    with pytest.raises(EvaluationError, match="absence file does not match its name"):
        load_session(tmp_path, SESSION)


def test_a_candidate_symbol_must_be_a_ticker(tmp_path: Path) -> None:
    packet = _candidate()
    bad = packet.model_copy(
        update={
            "facts": packet.facts.model_construct(
                **{**packet.facts.model_dump(), "symbol": "../SOL"}
            )
        }
    )
    with pytest.raises(EvaluationError, match="symbol must be 1 to 20 uppercase letters or digits"):
        write_candidate(tmp_path, SESSION, bad)
    assert list(tmp_path.rglob("*.json")) == []


def test_stored_inputs_are_what_a_closed_session_assesses(tmp_path: Path) -> None:
    write_universe(tmp_path, _snapshot(SYMBOL, "ETHUSDT"))
    write_candidate(tmp_path, SESSION, _candidate())
    write_absence(tmp_path, _absence(symbol="ETHUSDT"))
    write_absence(tmp_path, _absence(kind="daily_bar", symbol="ETHUSDT"))
    loaded = load_session(tmp_path, SESSION)
    result = assess_session(
        SESSION,
        CLOSE,
        snapshot=loaded.snapshot,
        bars={"BTCUSDT": (_bar("BTCUSDT"),), SYMBOL: (_bar(SYMBOL),)},
        bar_months_present={"BTCUSDT": True, SYMBOL: True, "ETHUSDT": False},
        candidates=loaded.candidates,
        candidate_files_present=loaded.candidate_files_present,
        observations=(
            _observation("btc_dominance", CLOSE),
            _observation("stablecoin_supply", None),
        ),
        regime_failures=frozenset(),
        weights_present=False,
        bar_absences=loaded.bar_absences,
    )
    assert result.ready is True
    assert result.blocks == ()
    assert result.decision_notes == (
        "daily_bar_absent:ETHUSDT",
        "missing_candidate:ETHUSDT",
        "score_weights_not_frozen",
    )
    assert result.score_weights == "absent"


def _bar(symbol: str) -> DailyBar:
    price = Decimal("10")
    return DailyBar(
        symbol=symbol,
        open_date=SESSION,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal("1"),
        quote_volume=Decimal("1"),
        trade_count=1,
        taker_buy_base_volume=Decimal("1"),
        taker_buy_quote_volume=Decimal("1"),
    )


def _observation(series: str, source: datetime | None) -> Observation:
    moment = datetime(SESSION.year, SESSION.month, SESSION.day, tzinfo=UTC)
    name = "btc_dominance" if series == "btc_dominance" else "stablecoin_supply_usd"
    unit = "percent" if series == "btc_dominance" else "usd"
    provider = "coingecko" if series == "btc_dominance" else "defillama"
    return Observation(
        series=series,
        provider=provider,
        source_timestamp=source,
        observed_at=moment,
        symbol=None,
        values=((name, Decimal("1")),),
        units=((name, unit),),
    )


def test_a_lost_create_keeps_the_first_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = _snapshot()
    real_link = os.link

    def lose_the_race(source: str, destination: str) -> None:
        real_link(source, destination)
        raise FileExistsError

    monkeypatch.setattr(os, "link", lose_the_race)
    assert write_universe(tmp_path, snapshot) is False
    monkeypatch.undo()
    assert load_session(tmp_path, SESSION).snapshot == snapshot


def test_an_invalid_universe_document_is_unusable(tmp_path: Path) -> None:
    write_universe(tmp_path, _snapshot())
    _universe_path(tmp_path).write_text("{}")
    with pytest.raises(EvaluationError, match="session input is unusable"):
        load_session(tmp_path, SESSION)


def test_an_invalid_absence_document_is_unusable(tmp_path: Path) -> None:
    path = _absence_path(tmp_path, "candidate")
    path.parent.mkdir(parents=True)
    path.write_text("{}")
    with pytest.raises(EvaluationError, match="session input is unusable"):
        load_session(tmp_path, SESSION)


def test_a_session_file_must_be_an_object(tmp_path: Path) -> None:
    write_universe(tmp_path, _snapshot())
    _universe_path(tmp_path).write_text("[]")
    with pytest.raises(EvaluationError, match="session input is unusable"):
        load_session(tmp_path, SESSION)


def test_a_candidate_reading_must_be_an_object(tmp_path: Path) -> None:
    write_candidate(tmp_path, SESSION, _candidate())
    document = json.loads(_candidate_path(tmp_path).read_text())
    document["coingecko"] = None
    _candidate_path(tmp_path).write_text(json.dumps(document))
    with pytest.raises(EvaluationError, match="session input is unusable"):
        load_session(tmp_path, SESSION)


def test_a_candidate_clock_field_must_be_present(tmp_path: Path) -> None:
    write_candidate(tmp_path, SESSION, _candidate())
    document = json.loads(_candidate_path(tmp_path).read_text())
    del document["cmc"]["source_timestamp"]
    _candidate_path(tmp_path).write_text(json.dumps(document))
    with pytest.raises(EvaluationError, match="session input is unusable"):
        load_session(tmp_path, SESSION)


def test_an_empty_store_has_no_recorded_inputs(tmp_path: Path) -> None:
    assert load_session(tmp_path, SESSION) == RecordedInputs(None, {}, {}, {})


def test_a_boolean_load_session_is_refused(tmp_path: Path) -> None:
    with pytest.raises(EvaluationError, match="session is a date"):
        load_session(tmp_path, True)  # type: ignore[arg-type]
