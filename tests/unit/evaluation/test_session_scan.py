import json
import socket
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from cip.domain.errors import EvaluationError
from cip.domain.policy import load_policy
from cip.evaluation.decision import Cohort, Disposition
from cip.evaluation.eligibility import CandidateFacts
from cip.evaluation.features import Tokenomics
from cip.evaluation.fundamentals import CmcReading, CoinGeckoReading, UnlockReading
from cip.evaluation.liquidity import MarketSnapshot
from cip.evaluation.populate import (
    BarCapture,
    CaptureClock,
    PacketCapture,
    PopulationResult,
    RegimeCapture,
    SessionCaptures,
    UniverseCapture,
    populate_session,
    replay_session,
)
from cip.evaluation.scan import ScanCandidate
from cip.evaluation.session import SessionReadiness
from cip.evaluation.session_scan import scan_finalized_session
from cip.evaluation.store import FileDecisionWriter
from cip.history.bars import DailyBar
from cip.recorders.observation import Observation

_POLICY = load_policy(Path(__file__).parents[3] / "policies" / "investment-policy.yaml")
_UNIVERSE = _POLICY.policy.hypotheses.universe
SESSION = date(2026, 10, 5)
CLOSE = datetime(2026, 10, 6, tzinfo=UTC)
FREEZE_SHA = "a" * 40
SCAN_SHA = "b" * 40
SOL = "SOLUSDT"
ETH = "ETHUSDT"
BTC = "BTCUSDT"


def _clock(source: str) -> CaptureClock:
    return CaptureClock(source, CLOSE, CLOSE)


def _bar(symbol: str, day: date, close: str, *, high: str | None = None) -> DailyBar:
    price = Decimal(close)
    peak = Decimal(high) if high is not None else price
    return DailyBar(
        symbol=symbol,
        open_date=day,
        open=price,
        high=peak,
        low=min(price, peak),
        close=price,
        volume=Decimal("1"),
        quote_volume=Decimal("1"),
        trade_count=1,
        taker_buy_base_volume=Decimal("1"),
        taker_buy_quote_volume=Decimal("1"),
    )


def _flat(symbol: str, days: int, *, end: date = SESSION) -> tuple[DailyBar, ...]:
    return tuple(
        _bar(symbol, end - timedelta(days=offset), "100") for offset in range(days - 1, -1, -1)
    )


def _crashed_btc() -> tuple[DailyBar, ...]:
    bars = list(_flat(BTC, 200))
    last = bars[-1]
    bars[-1] = DailyBar(
        symbol=BTC,
        open_date=last.open_date,
        open=Decimal("100"),
        high=Decimal("100"),
        low=Decimal("70"),
        close=Decimal("70"),
        volume=Decimal("1"),
        quote_volume=Decimal("1"),
        trade_count=1,
        taker_buy_base_volume=Decimal("1"),
        taker_buy_quote_volume=Decimal("1"),
    )
    return tuple(bars)


def _money(value: float) -> Decimal:
    return Decimal(str(value))


def _eligible(symbol: str, base: str, gecko_id: str) -> ScanCandidate:
    lane = _UNIVERSE.normal
    rules = _UNIVERSE.manipulation
    share = (
        _money(rules.binance_volume_share_below) + _money(rules.binance_volume_share_above)
    ) / 2
    return ScanCandidate(
        facts=CandidateFacts(
            symbol=symbol,
            base_asset=base,
            quote_asset="USDT",
            status="TRADING",
            eur_stable=False,
            fan_token=False,
            monitoring_tag=False,
            delisting=False,
            deposits_suspended=False,
            withdrawals_suspended=False,
            pending_migration=False,
            market_cap_usd=_money(lane.minimum_market_cap_usd),
            market_cap_rank=lane.market_cap_rank_ceiling,
            circulating_ratio=_money(lane.minimum_circulating_ratio),
            fdv_to_market_cap=_money(lane.maximum_fdv_to_market_cap),
            history_days=lane.minimum_history_days,
            unlock_schedule_known=None,
        ),
        market=MarketSnapshot(
            as_of=CLOSE,
            median_quote_volume_30d_usd=_money(lane.median_quote_volume_30d_usd),
            day_quote_volume_usd=_money(lane.minimum_day_quote_volume_usd),
            median_spread_bps=_money(lane.maximum_median_spread_bps),
            spread_snapshots=lane.minimum_spread_snapshots,
            depth_usd_per_side=_money(lane.minimum_depth_usd_per_side),
            turnover=_money(lane.turnover_min),
            volume_zscore=Decimal("0"),
            price_move=Decimal("0"),
            spike_candle_count=rules.spike_candle_ceiling,
            trade_size_stdev=Decimal("0"),
            taker_buy_ratio=Decimal("0"),
            binance_volume_share=share,
            stablecoin_peg_deviation=Decimal("0"),
            peg_deviation_hours=0,
        ),
        coingecko=CoinGeckoReading(
            coingecko_id=gecko_id,
            market_cap_usd=Decimal("100"),
            circulating_supply=Decimal("50"),
            total_supply=Decimal("100"),
            fully_diluted_valuation_usd=Decimal("200"),
            source_timestamp=CLOSE,
        ),
        cmc=CmcReading(market_cap_usd=Decimal("100"), source_timestamp=CLOSE),
        unlocks=UnlockReading(
            schedule_known=True, pct_circ_14d=Decimal("0"), pct_circ_90d=Decimal("0")
        ),
        tokenomics=Tokenomics(
            circulating_ratio=Decimal("1"),
            fdv_to_market_cap=Decimal("2"),
            unlock_pct_14d=Decimal("0"),
            unlock_pct_90d=Decimal("0"),
        ),
    )


def _blank(symbol: str) -> ScanCandidate:
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
            as_of=CLOSE,
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
            coingecko_id=None,
            market_cap_usd=None,
            circulating_supply=None,
            total_supply=None,
            fully_diluted_valuation_usd=None,
            source_timestamp=None,
        ),
        cmc=CmcReading(market_cap_usd=None, source_timestamp=None),
        unlocks=UnlockReading(schedule_known=None, pct_circ_14d=None, pct_circ_90d=None),
        tokenomics=None,
    )


def _observation(series: str) -> Observation:
    moment = datetime(SESSION.year, SESSION.month, SESSION.day, 12, tzinfo=UTC)
    name = "btc_dominance" if series == "btc_dominance" else "stablecoin_supply_usd"
    unit = "percent" if series == "btc_dominance" else "usd"
    provider = "coingecko" if series == "btc_dominance" else "defillama"
    return Observation(
        series=series,
        provider=provider,
        source_timestamp=moment,
        observed_at=moment,
        symbol=None,
        values=((name, Decimal("1")),),
        units=((name, unit),),
    )


def _freeze(tmp_path: Path) -> None:
    captures = SessionCaptures(
        universe=UniverseCapture((SOL, ETH), CLOSE, _clock("binance/exchangeInfo")),
        packets=(
            PacketCapture(SOL, _eligible(SOL, "SOL", "solana"), _clock("binance/ticker")),
            PacketCapture(ETH, _blank(ETH), _clock("binance/ticker")),
        ),
        candidate_absences=(),
        bars=(
            BarCapture(BTC, _crashed_btc(), _clock("binance/klines")),
            BarCapture(SOL, _flat(SOL, 60), _clock("binance/klines")),
            BarCapture(
                ETH,
                _flat(ETH, 60, end=SESSION - timedelta(days=1)),
                _clock("binance/klines"),
            ),
        ),
        bar_absences=(),
        regime=(
            RegimeCapture(_observation("btc_dominance"), CLOSE),
            RegimeCapture(_observation("stablecoin_supply"), CLOSE),
        ),
        regime_failures=(),
    )
    result = populate_session(
        tmp_path,
        SESSION,
        CLOSE,
        captures,
        git_sha=FREEZE_SHA,
        weights_present=False,
    )
    assert result.readiness.ready is True
    assert result.manifest is not None


def _files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and not path.name.startswith(".")
    }


def _scan(tmp_path: Path, **overrides: object) -> object:
    values: dict[str, object] = {
        "root": tmp_path,
        "session": SESSION,
        "cohort": Cohort.SHADOW,
        "policy": _POLICY,
        "git_sha": SCAN_SHA,
        "correlation_id": "closed-session",
        "writer": FileDecisionWriter(tmp_path),
        "verified_ids": {"SOL": "solana"},
    }
    values.update(overrides)
    return scan_finalized_session(**values)  # type: ignore[arg-type]


def test_a_finalized_session_records_one_decision_per_frozen_symbol(tmp_path: Path) -> None:
    _freeze(tmp_path)
    result = _scan(tmp_path)
    assert [record.symbol for record in result.records] == [SOL, ETH]
    sol, eth = result.records
    assert sol.disposition is Disposition.INELIGIBLE
    assert sol.reason_codes == ("score_weights_not_frozen",)
    assert sol.score is None
    assert sol.rank is None
    assert sol.policy_version == _POLICY.version
    assert sol.git_sha == SCAN_SHA
    freeze = next(source for source in sol.sources if source.name == "session_freeze")
    manifest = replay_session(tmp_path, SESSION).manifest
    assert manifest is not None
    assert freeze.observed_at == manifest.finalized_at
    assert manifest.input_manifest_version == 1
    assert f"manifest={manifest.input_manifest_version}" in freeze.provenance
    assert f"finalized_at={manifest.finalized_at.isoformat()}" in freeze.provenance
    assert f"git_sha={FREEZE_SHA}" in freeze.provenance
    assert f"universe={manifest.universe_snapshot_sha256}" in freeze.provenance
    assert f"bars={manifest.bars_manifest_sha256}" in freeze.provenance
    assert f"candidates={manifest.candidate_manifest_sha256}" in freeze.provenance
    assert f"regime={manifest.regime_inputs_sha256}" in freeze.provenance
    assert "score_weights=absent" in freeze.provenance
    assert eth.disposition is Disposition.INELIGIBLE
    assert eth.features == {}
    assert eth.score is None
    assert eth.rank is None
    assert all(record.disposition is not Disposition.BUY for record in result.records)
    for record in result.records:
        document = record.to_document()
        assert "order_id" not in document
        assert "quantity" not in document
        assert "fill" not in document
    eth_bars = json.loads(
        (tmp_path / "sessions" / "date=2026-10-05" / "bars" / "symbol=ETHUSDT.json").read_text()
    )
    assert eth_bars["bars"][-1]["open_date"] == "2026-10-04"
    assert all(created.created for created in result.stored)


def test_replay_matches_the_first_decisions_and_leaves_the_freeze(tmp_path: Path) -> None:
    _freeze(tmp_path)
    before = _files(tmp_path / "sessions")
    first = _scan(tmp_path)
    bodies = {item.key: (tmp_path / item.key).read_bytes() for item in first.stored}
    second = _scan(tmp_path)
    assert [record.to_document() for record in second.records] == [
        record.to_document() for record in first.records
    ]
    assert all(item.created is False for item in second.stored)
    assert {item.key: (tmp_path / item.key).read_bytes() for item in second.stored} == bodies
    assert _files(tmp_path / "sessions") == before
    assert len(list((tmp_path / "decisions").rglob("*.json"))) == 2


def test_an_unfinalized_session_is_refused(tmp_path: Path) -> None:
    with pytest.raises(EvaluationError, match="not finalized"):
        _scan(tmp_path)
    assert list(tmp_path.rglob("*.json")) == []


def test_a_freeze_without_a_manifest_identity_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze(tmp_path)
    readiness = SessionReadiness(ready=True, blocks=(), decision_notes=(), score_weights="absent")
    monkeypatch.setattr(
        "cip.evaluation.session_scan.replay_session",
        lambda _root, _session: PopulationResult(readiness, None),
    )
    with pytest.raises(EvaluationError, match="not finalized"):
        _scan(tmp_path)
    assert list((tmp_path / "decisions").rglob("*.json")) == []


def test_a_blocked_freeze_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(tmp_path)
    sealed = replay_session(tmp_path, SESSION)
    blocked = SessionReadiness(
        ready=False,
        blocks=("snapshot_not_produced",),
        decision_notes=(),
        score_weights="absent",
    )
    monkeypatch.setattr(
        "cip.evaluation.session_scan.replay_session",
        lambda _root, _session: PopulationResult(blocked, sealed.manifest),
    )
    with pytest.raises(EvaluationError, match="not ready"):
        _scan(tmp_path)
    assert list((tmp_path / "decisions").rglob("*.json")) == []


def test_a_manifest_hash_mismatch_is_refused(tmp_path: Path) -> None:
    _freeze(tmp_path)
    path = tmp_path / "sessions" / "date=2026-10-05" / "universe.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(EvaluationError, match="does not match its inputs"):
        _scan(tmp_path)
    assert list((tmp_path / "decisions").rglob("*.json")) == []


def test_a_substituted_session_file_does_not_replace_decisions(tmp_path: Path) -> None:
    _freeze(tmp_path)
    first = _scan(tmp_path)
    body = (tmp_path / first.stored[0].key).read_bytes()
    path = tmp_path / "sessions" / "date=2026-10-05" / "bars" / "symbol=ETHUSDT.json"
    path.write_text(path.read_text().replace("2026-10-04", "2026-10-05"))
    with pytest.raises(EvaluationError, match="does not match its inputs"):
        _scan(tmp_path)
    assert (tmp_path / first.stored[0].key).read_bytes() == body
    assert len(list((tmp_path / "decisions").rglob("*.json"))) == 2


def test_present_weights_without_stored_values_are_refused(tmp_path: Path) -> None:
    _freeze(tmp_path)
    path = tmp_path / "sessions" / "date=2026-10-05" / "manifest.json"
    document = json.loads(path.read_text())
    document["score_weights"] = "present"
    path.write_text(json.dumps(document, sort_keys=True))
    with pytest.raises(EvaluationError, match="score weights are not stored"):
        _scan(tmp_path)
    assert list((tmp_path / "decisions").rglob("*.json")) == []


def test_the_scan_does_not_call_a_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(tmp_path)

    def refused(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("provider call")

    monkeypatch.setattr(socket, "create_connection", refused)
    source = Path(scan_finalized_session.__code__.co_filename).read_text()
    assert "cip.adapters" not in source
    assert "httpx" not in source
    assert "urllib" not in source
    assert "boto3" not in source
    result = _scan(tmp_path)
    assert len(result.records) == 2
