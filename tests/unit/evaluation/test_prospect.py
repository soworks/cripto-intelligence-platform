import json
import shutil
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from cip.adapters.market import ExchangeInfo
from cip.domain.errors import EvaluationError
from cip.evaluation.eligibility import CandidateFacts
from cip.evaluation.fundamentals import CmcReading, CoinGeckoReading, UnlockReading
from cip.evaluation.liquidity import MarketSnapshot
from cip.evaluation.populate import (
    AbsenceCapture,
    BarCapture,
    CaptureClock,
    PacketCapture,
    RegimeCapture,
    UniverseCapture,
)
from cip.evaluation.prospect import (
    candidate_absence,
    history_capture,
    load_closed,
    load_pre_close,
    prospective_session,
    session_bar_capture,
    store_absence,
    store_completed_bars,
    store_history,
    store_packet,
    store_regime,
    store_universe,
    trading_usdt_symbols,
    universe_capture,
)
from cip.evaluation.scan import ScanCandidate
from cip.history.bars import DailyBar
from cip.recorders.observation import Observation

SESSION = date(2026, 10, 6)
CLOSE = datetime(2026, 10, 7, tzinfo=UTC)
BEFORE = datetime(2026, 10, 6, 18, tzinfo=UTC)
AFTER = CLOSE + timedelta(minutes=5)


def _bar(symbol: str, day: date) -> DailyBar:
    price = Decimal("10")
    return DailyBar(
        symbol=symbol,
        open_date=day,
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


def _symbol(symbol: str, status: str, base: str, quote: str) -> dict[str, str]:
    return {"symbol": symbol, "status": status, "baseAsset": base, "quoteAsset": quote}


def _info() -> ExchangeInfo:
    return ExchangeInfo.model_validate(
        {
            "symbols": [
                _symbol("BTCUSDT", "TRADING", "BTC", "USDT"),
                _symbol("ETHUSDT", "BREAK", "ETH", "USDT"),
                _symbol("ETHBTC", "TRADING", "ETH", "BTC"),
                _symbol("bad pair", "TRADING", "BAD", "USDT"),
                _symbol("SOLUSDT", "TRADING", "SOL", "USDT"),
            ]
        }
    )


def test_a_historical_session_stays_blocked() -> None:
    with pytest.raises(EvaluationError, match="historical session stays blocked"):
        prospective_session(date(2026, 10, 5))
    with pytest.raises(EvaluationError, match="historical session stays blocked"):
        history_capture(date(2026, 10, 2), "BTCUSDT", (_bar("BTCUSDT", date(2026, 10, 2)),), AFTER)
    wrong = datetime(2026, 10, 6)  # noqa: DTZ001
    with pytest.raises(EvaluationError, match="session is a date"):
        prospective_session(wrong)  # type: ignore[arg-type]


def test_the_exchange_universe_is_trading_usdt_names() -> None:
    assert trading_usdt_symbols(_info()) == ("BTCUSDT", "SOLUSDT")
    captured = universe_capture(SESSION, _info(), BEFORE, BEFORE)
    assert captured.symbols == ("BTCUSDT", "SOLUSDT")
    assert captured.observed_at == BEFORE
    assert captured.clock.captured_at == BEFORE
    assert captured.clock.source == "binance/exchangeInfo"


def test_a_universe_retrieved_after_the_close_is_rejected() -> None:
    with pytest.raises(EvaluationError, match="capture is after the close"):
        universe_capture(SESSION, _info(), AFTER, BEFORE)
    with pytest.raises(EvaluationError, match="capture is after the close"):
        universe_capture(SESSION, _info(), BEFORE, AFTER)
    with pytest.raises(EvaluationError, match="universe snapshot is empty"):
        universe_capture(
            SESSION,
            ExchangeInfo.model_validate({"symbols": []}),
            BEFORE,
            BEFORE,
        )
    with pytest.raises(EvaluationError, match="observed_at must be timezone-aware UTC"):
        universe_capture(SESSION, _info(), datetime(2026, 10, 6, 18), BEFORE)  # noqa: DTZ001
    shifted = BEFORE.replace(tzinfo=timezone(timedelta(hours=1)))
    with pytest.raises(EvaluationError, match="captured_at must be timezone-aware UTC"):
        universe_capture(SESSION, _info(), BEFORE, shifted)


def test_a_repeated_exchange_symbol_is_rejected() -> None:
    info = ExchangeInfo.model_validate(
        {
            "symbols": [
                _symbol("BTCUSDT", "TRADING", "BTC", "USDT"),
                _symbol("BTCUSDT", "TRADING", "BTC", "USDT"),
            ]
        }
    )
    with pytest.raises(EvaluationError, match="universe snapshot repeats a symbol"):
        trading_usdt_symbols(info)


def test_pre_close_history_excludes_the_session_bar() -> None:
    prior = _bar("BTCUSDT", SESSION - timedelta(days=1))
    captured = history_capture(SESSION, "BTCUSDT", (prior,), BEFORE)
    assert captured.bars == (prior,)
    assert captured.clock.source_timestamp == datetime(2026, 10, 6, tzinfo=UTC)
    assert captured.clock.captured_at == BEFORE
    with pytest.raises(EvaluationError, match="session bar was retrieved before it closed"):
        history_capture(SESSION, "BTCUSDT", (prior, _bar("BTCUSDT", SESSION)), BEFORE)
    with pytest.raises(EvaluationError, match="capture is after the close"):
        history_capture(SESSION, "BTCUSDT", (_bar("BTCUSDT", SESSION + timedelta(days=1)),), BEFORE)
    with pytest.raises(EvaluationError, match="capture is after the close"):
        history_capture(SESSION, "BTCUSDT", (prior,), AFTER)
    with pytest.raises(EvaluationError, match="history is empty"):
        history_capture(SESSION, "BTCUSDT", (), BEFORE)
    with pytest.raises(EvaluationError, match="bar was retrieved before it closed"):
        history_capture(SESSION, "BTCUSDT", (prior,), datetime(2026, 10, 5, 12, tzinfo=UTC))


def test_the_completed_session_bar_is_retrieved_after_the_close() -> None:
    session_bar = _bar("BTCUSDT", SESSION)
    prior = _bar("BTCUSDT", SESSION - timedelta(days=1))
    captured = session_bar_capture(SESSION, "BTCUSDT", (prior, session_bar), AFTER)
    assert captured.clock.captured_at == AFTER
    assert captured.clock.source_timestamp == CLOSE
    with pytest.raises(EvaluationError, match="session bar was retrieved before it closed"):
        session_bar_capture(SESSION, "BTCUSDT", (session_bar,), BEFORE)
    with pytest.raises(EvaluationError, match="capture is after the close"):
        session_bar_capture(
            SESSION, "BTCUSDT", (session_bar, _bar("BTCUSDT", SESSION + timedelta(days=1))), AFTER
        )
    with pytest.raises(EvaluationError, match="session bar is missing"):
        session_bar_capture(SESSION, "BTCUSDT", (prior,), AFTER)
    with pytest.raises(EvaluationError, match="captured_at must be timezone-aware UTC"):
        session_bar_capture(SESSION, "BTCUSDT", (session_bar,), datetime(2026, 10, 7, 0, 5))  # noqa: DTZ001


def test_a_candidate_absence_requires_a_lookup() -> None:
    absence = candidate_absence(SESSION, "SOLUSDT", AFTER, "coingecko", looked=True)
    assert absence.symbol == "SOLUSDT"
    assert absence.produced_at == AFTER
    with pytest.raises(EvaluationError, match="candidate absence requires a lookup"):
        candidate_absence(SESSION, "SOLUSDT", AFTER, "coingecko", looked=False)
    with pytest.raises(EvaluationError, match="source is required"):
        candidate_absence(SESSION, "SOLUSDT", AFTER, "", looked=True)
    naive = datetime(2026, 10, 7, 0, 5)  # noqa: DTZ001
    with pytest.raises(EvaluationError, match="produced_at must be timezone-aware UTC"):
        candidate_absence(SESSION, "SOLUSDT", naive, "coingecko", looked=True)
    with pytest.raises(EvaluationError, match="historical session stays blocked"):
        candidate_absence(date(2026, 10, 4), "SOLUSDT", AFTER, "coingecko", looked=True)


def _universe() -> UniverseCapture:
    return universe_capture(SESSION, _info(), BEFORE, BEFORE)


def _history() -> BarCapture:
    prior = _bar("BTCUSDT", SESSION - timedelta(days=1))
    return history_capture(SESSION, "BTCUSDT", (prior,), BEFORE)


def _completed() -> BarCapture:
    prior = _bar("BTCUSDT", SESSION - timedelta(days=1))
    stale = DailyBar(
        symbol=prior.symbol,
        open_date=prior.open_date,
        open=prior.open,
        high=prior.high,
        low=prior.low,
        close=Decimal("99"),
        volume=prior.volume,
        quote_volume=prior.quote_volume,
        trade_count=prior.trade_count,
        taker_buy_base_volume=prior.taker_buy_base_volume,
        taker_buy_quote_volume=prior.taker_buy_quote_volume,
    )
    return session_bar_capture(SESSION, "BTCUSDT", (stale, _bar("BTCUSDT", SESSION)), AFTER)


def _facts(symbol: str = "BTCUSDT") -> CandidateFacts:
    base = symbol.removesuffix("USDT")
    return CandidateFacts(
        symbol=symbol,
        base_asset=base,
        quote_asset="USDT",
        status="TRADING",
        eur_stable=None,
        fan_token=None,
        monitoring_tag=None,
        delisting=None,
        deposits_suspended=None,
        withdrawals_suspended=None,
        pending_migration=None,
        market_cap_usd=None,
        market_cap_rank=None,
        circulating_ratio=None,
        fdv_to_market_cap=None,
        history_days=None,
        unlock_schedule_known=None,
    )


def _packet(
    symbol: str = "BTCUSDT",
    *,
    cap: Decimal | None = None,
    stamp: datetime | None = None,
) -> PacketCapture:
    candidate = ScanCandidate(
        facts=_facts(symbol),
        market=MarketSnapshot(
            as_of=BEFORE,
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
            market_cap_usd=cap,
            circulating_supply=None,
            total_supply=None,
            fully_diluted_valuation_usd=None,
            source_timestamp=stamp,
        ),
        cmc=CmcReading(market_cap_usd=None, source_timestamp=None),
        unlocks=UnlockReading(schedule_known=None, pct_circ_14d=None, pct_circ_90d=None),
        tokenomics=None,
    )
    return PacketCapture(symbol, candidate, CaptureClock("binance/ticker", BEFORE, BEFORE))


def _regime(series: str = "btc_dominance", observed_at: datetime = BEFORE) -> RegimeCapture:
    name = "btc_dominance" if series == "btc_dominance" else "stablecoin_supply_usd"
    unit = "percent" if series == "btc_dominance" else "usd"
    return RegimeCapture(
        Observation(
            series=series,
            provider="coingecko",
            source_timestamp=observed_at,
            observed_at=observed_at,
            symbol=None,
            values=((name, Decimal("59")),),
            units=((name, unit),),
        ),
        observed_at,
    )


def test_pre_close_captures_round_trip_without_the_session_bar(tmp_path: Path) -> None:
    store_universe(tmp_path, SESSION, _universe())
    store_history(tmp_path, SESSION, _history())
    eth = history_capture(
        SESSION, "ETHUSDT", (_bar("ETHUSDT", SESSION - timedelta(days=1)),), BEFORE
    )
    store_history(tmp_path, SESSION, eth)
    store_packet(tmp_path, SESSION, _packet())
    store_packet(tmp_path, SESSION, _packet("ADAUSDT", cap=Decimal("1"), stamp=BEFORE))
    absence = candidate_absence(SESSION, "SOLUSDT", BEFORE, "coingecko", looked=True)
    store_absence(tmp_path, SESSION, "candidate", absence)
    store_absence(
        tmp_path, SESSION, "daily_bar", AbsenceCapture("ETHUSDT", BEFORE, "binance/klines")
    )
    store_regime(tmp_path, SESSION, _regime())
    store_completed_bars(tmp_path, SESSION, _completed())
    pre_close = load_pre_close(tmp_path, SESSION)
    assert pre_close.universe is not None
    assert pre_close.universe.symbols == ("BTCUSDT", "SOLUSDT")
    assert [series.symbol for series in pre_close.bars] == ["BTCUSDT", "ETHUSDT"]
    assert pre_close.bars[0].bars[0].open_date == SESSION - timedelta(days=1)
    assert [packet.symbol for packet in pre_close.packets] == ["ADAUSDT", "BTCUSDT"]
    assert pre_close.candidate_absences[0].symbol == "SOLUSDT"
    assert pre_close.bar_absences[0].symbol == "ETHUSDT"
    assert pre_close.regime[0].observation.series == "btc_dominance"
    closed = load_closed(tmp_path, SESSION)
    btc = next(series for series in closed.bars if series.symbol == "BTCUSDT")
    eth_bars = next(series for series in closed.bars if series.symbol == "ETHUSDT")
    assert [bar.open_date for bar in btc.bars] == [SESSION - timedelta(days=1), SESSION]
    assert btc.bars[0].close == Decimal("10")
    assert btc.clock.captured_at == AFTER
    assert [bar.open_date for bar in eth_bars.bars] == [SESSION - timedelta(days=1)]
    store_universe(tmp_path, SESSION, _universe())
    assert load_pre_close(tmp_path, SESSION).universe == pre_close.universe


def test_a_historical_capture_is_not_stored(tmp_path: Path) -> None:
    with pytest.raises(EvaluationError, match="historical session stays blocked"):
        store_universe(tmp_path, date(2026, 10, 5), _universe())
    assert list(tmp_path.rglob("*")) == []


def test_a_changed_capture_does_not_replace_the_stored_one(tmp_path: Path) -> None:
    store_universe(tmp_path, SESSION, _universe())
    other = UniverseCapture(
        ("ETHUSDT",), BEFORE, CaptureClock("binance/exchangeInfo", BEFORE, BEFORE)
    )
    with pytest.raises(EvaluationError, match="already exists with a different payload"):
        store_universe(tmp_path, SESSION, other)
    assert load_pre_close(tmp_path, SESSION).universe == _universe()


def test_a_capture_that_appears_during_the_write_keeps_the_same_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def occupy(source: str, destination: str) -> None:
        Path(destination).write_bytes(Path(source).read_bytes())
        raise FileExistsError

    monkeypatch.setattr("cip.evaluation.prospect.os.link", occupy)
    store_universe(tmp_path, SESSION, _universe())
    assert load_pre_close(tmp_path, SESSION).universe == _universe()
    assert list(tmp_path.rglob(".*.tmp")) == []


def test_a_different_capture_that_appears_during_the_write_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def occupy(source: str, destination: str) -> None:
        del source
        Path(destination).write_bytes(b"{}")
        raise FileExistsError

    monkeypatch.setattr("cip.evaluation.prospect.os.link", occupy)
    with pytest.raises(EvaluationError, match="already exists with a different payload"):
        store_universe(tmp_path, SESSION, _universe())


def test_rejected_bar_clocks_store_nothing(tmp_path: Path) -> None:
    early = BarCapture(
        "BTCUSDT", (_bar("BTCUSDT", SESSION),), CaptureClock("binance/klines", CLOSE, BEFORE)
    )
    with pytest.raises(EvaluationError, match="session bar was retrieved before it closed"):
        store_completed_bars(tmp_path, SESSION, early)
    late_history = BarCapture(
        "BTCUSDT",
        (_bar("BTCUSDT", SESSION - timedelta(days=1)),),
        CaptureClock("binance/klines", BEFORE, AFTER),
    )
    with pytest.raises(EvaluationError, match="capture is after the close"):
        store_history(tmp_path, SESSION, late_history)
    mismatched = BarCapture(
        "BTCUSDT",
        _history().bars,
        CaptureClock("other", _history().clock.source_timestamp, BEFORE),
    )
    with pytest.raises(EvaluationError, match="history capture does not match its bars"):
        store_history(tmp_path, SESSION, mismatched)
    mismatched_final = BarCapture(
        "BTCUSDT",
        _completed().bars,
        CaptureClock("other", CLOSE, AFTER),
    )
    with pytest.raises(EvaluationError, match="session bar capture does not match its bars"):
        store_completed_bars(tmp_path, SESSION, mismatched_final)
    assert list(tmp_path.rglob("*.json")) == []


def test_a_packet_with_an_undated_cap_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(EvaluationError, match="undated fundamental"):
        store_packet(tmp_path, SESSION, _packet(cap=Decimal("1")))
    with pytest.raises(EvaluationError, match="capture is after the close"):
        store_packet(tmp_path, SESSION, _packet(stamp=AFTER))
    store_packet(tmp_path, SESSION, _packet(cap=Decimal("1"), stamp=BEFORE))
    path = tmp_path / "captures" / "session=2026-10-06" / "candidates" / "symbol=BTCUSDT.json"
    document = json.loads(path.read_text())
    document["candidate"]["cmc"]["market_cap_usd"] = "1"
    document["candidate"]["cmc"]["source_timestamp"] = "2026-10-07T00:00:01Z"
    path.write_text(json.dumps(document))
    with pytest.raises(EvaluationError, match="capture is after the close"):
        load_pre_close(tmp_path, SESSION)
    path.unlink()
    late = _packet()
    moved = PacketCapture(
        late.symbol, late.candidate, CaptureClock("binance/ticker", AFTER, BEFORE)
    )
    with pytest.raises(EvaluationError, match="capture is after the close"):
        store_packet(tmp_path, SESSION, moved)
    renamed = PacketCapture("ETHUSDT", late.candidate, late.clock)
    with pytest.raises(EvaluationError, match="candidate symbol does not match the packet"):
        store_packet(tmp_path, SESSION, renamed)
    assert list(tmp_path.rglob("*.json")) == []


def test_a_regime_capture_must_be_for_this_session(tmp_path: Path) -> None:
    with pytest.raises(EvaluationError, match="regime observation is for a different session"):
        store_regime(tmp_path, SESSION, _regime(observed_at=datetime(2026, 10, 5, 12, tzinfo=UTC)))
    funding = RegimeCapture(
        Observation(
            series="funding",
            provider="binance",
            source_timestamp=BEFORE,
            observed_at=BEFORE,
            symbol="BTCUSDT",
            values=(("funding_rate", Decimal("0.0001")),),
            units=(("funding_rate", "rate"),),
        ),
        BEFORE,
    )
    with pytest.raises(EvaluationError, match="regime series is not a readiness input"):
        store_regime(tmp_path, SESSION, funding)
    with pytest.raises(EvaluationError, match="capture is after the close"):
        store_regime(tmp_path, SESSION, _regime(observed_at=AFTER))
    blank = UniverseCapture(("BTCUSDT",), BEFORE, CaptureClock("", None, BEFORE))
    with pytest.raises(EvaluationError, match="source is required"):
        store_universe(tmp_path, SESSION, blank)
    empty = UniverseCapture((), BEFORE, CaptureClock("binance/exchangeInfo", None, BEFORE))
    with pytest.raises(EvaluationError, match="universe snapshot is empty"):
        store_universe(tmp_path, SESSION, empty)
    repeated = UniverseCapture(
        ("BTCUSDT", "BTCUSDT"), BEFORE, CaptureClock("binance/exchangeInfo", BEFORE, BEFORE)
    )
    with pytest.raises(EvaluationError, match="universe snapshot repeats a symbol"):
        store_universe(tmp_path, SESSION, repeated)
    with pytest.raises(EvaluationError, match="uppercase letters or digits"):
        store_universe(
            tmp_path,
            SESSION,
            UniverseCapture(
                ("bad pair",), BEFORE, CaptureClock("binance/exchangeInfo", BEFORE, BEFORE)
            ),
        )
    assert list(tmp_path.rglob("*.json")) == []


def test_an_unusable_capture_file_is_refused(tmp_path: Path) -> None:
    store_universe(tmp_path, SESSION, _universe())
    path = tmp_path / "captures" / "session=2026-10-06" / "universe.json"
    path.write_text("{")
    with pytest.raises(EvaluationError, match="capture is unusable"):
        load_pre_close(tmp_path, SESSION)
    path.write_text("[]")
    with pytest.raises(EvaluationError, match="capture is unusable"):
        load_pre_close(tmp_path, SESSION)
    path.unlink()
    store_universe(tmp_path, SESSION, _universe())
    document = json.loads(path.read_text())
    document["session"] = "2026-10-07"
    path.write_text(json.dumps(document))
    with pytest.raises(EvaluationError, match="capture is unusable"):
        load_pre_close(tmp_path, SESSION)


def test_a_stray_capture_file_is_refused(tmp_path: Path) -> None:
    history = tmp_path / "captures" / "session=2026-10-06" / "history"
    history.mkdir(parents=True)
    (history / "notes.json").write_text("{}")
    with pytest.raises(EvaluationError, match="capture is unusable"):
        load_pre_close(tmp_path, SESSION)
    (history / "notes.json").unlink()
    (history / "symbol=BTCUSDT.json").mkdir()
    with pytest.raises(EvaluationError, match="capture is unusable"):
        load_pre_close(tmp_path, SESSION)


def test_a_final_bar_without_history_is_part_of_the_closed_load(tmp_path: Path) -> None:
    eth = session_bar_capture(SESSION, "ETHUSDT", (_bar("ETHUSDT", SESSION),), AFTER)
    store_completed_bars(tmp_path, SESSION, eth)
    assert load_pre_close(tmp_path, SESSION).bars == ()
    assert load_closed(tmp_path, SESSION).bars[0].symbol == "ETHUSDT"


def test_a_packet_needs_a_source(tmp_path: Path) -> None:
    packet = _packet()
    blank = PacketCapture(packet.symbol, packet.candidate, CaptureClock("", BEFORE, BEFORE))
    with pytest.raises(EvaluationError, match="source is required"):
        store_packet(tmp_path, SESSION, blank)
    store_packet(tmp_path, SESSION, packet)
    stored = tmp_path / "captures" / "session=2026-10-06" / "candidates" / "symbol=BTCUSDT.json"
    document = json.loads(stored.read_text())
    document["candidate"]["facts"]["symbol"] = "ETHUSDT"
    stored.write_text(json.dumps(document))
    with pytest.raises(EvaluationError, match="capture is unusable"):
        load_pre_close(tmp_path, SESSION)
    stored.unlink()
    dated = UniverseCapture(
        ("BTCUSDT",), BEFORE, CaptureClock("binance/exchangeInfo", None, BEFORE)
    )
    store_universe(tmp_path, SESSION, dated)
    assert load_pre_close(tmp_path, SESSION).universe == dated


def test_corrupt_capture_files_are_refused(tmp_path: Path) -> None:
    root = tmp_path / "captures" / "session=2026-10-06"
    history = root / "history"
    history.mkdir(parents=True)
    (history / ".keep").write_text("")
    (history / "symbol=BTCUSDT.json").write_text("[]")
    with pytest.raises(EvaluationError, match="capture is unusable"):
        load_pre_close(tmp_path, SESSION)
    (history / "symbol=BTCUSDT.json").write_text(json.dumps({"bars": {}, "symbol": "ETHUSDT"}))
    with pytest.raises(EvaluationError, match="capture is unusable"):
        load_pre_close(tmp_path, SESSION)
    (history / "symbol=BTCUSDT.json").write_text(
        json.dumps(
            {
                "bars": [],
                "captured_at": "2026-10-06T18:00:00Z",
                "symbol": "ETHUSDT",
            }
        )
    )
    with pytest.raises(EvaluationError, match="capture is unusable"):
        load_pre_close(tmp_path, SESSION)
    (history / "symbol=BTCUSDT.json").write_text(
        json.dumps({"bars": ["nope"], "captured_at": "2026-10-06T18:00:00Z", "symbol": "BTCUSDT"})
    )
    with pytest.raises(EvaluationError, match="capture is unusable"):
        load_pre_close(tmp_path, SESSION)
    (history / "symbol=BTCUSDT.json").write_text(
        json.dumps(
            {
                "bars": [{"symbol": "BTCUSDT"}],
                "captured_at": "2026-10-06T18:00:00Z",
                "symbol": "BTCUSDT",
            }
        )
    )
    with pytest.raises(EvaluationError, match="capture is unusable"):
        load_pre_close(tmp_path, SESSION)
    (history / "symbol=BTCUSDT.txt").write_text("{}")
    (history / "symbol=BTCUSDT.json").unlink()
    with pytest.raises(EvaluationError, match="capture is unusable"):
        load_pre_close(tmp_path, SESSION)

    for child in history.iterdir():
        child.unlink()
    history.rmdir()
    history.write_text("not a directory")
    with pytest.raises(EvaluationError, match="capture is unusable"):
        load_pre_close(tmp_path, SESSION)
    history.unlink()

    packets = root / "candidates"
    packets.mkdir()
    (packets / "symbol=ETHUSDT.json").write_text(
        json.dumps(
            {
                "candidate": {"items": ["2026-99-99T00:00:00Z"]},
                "captured_at": "2026-10-06T18:00:00Z",
                "source": "binance/ticker",
                "source_timestamp": None,
            }
        )
    )
    with pytest.raises(EvaluationError, match="capture is unusable"):
        load_pre_close(tmp_path, SESSION)
    (packets / "symbol=ETHUSDT.json").write_text("[]")
    with pytest.raises(EvaluationError, match="capture is unusable"):
        load_pre_close(tmp_path, SESSION)
    shutil.rmtree(packets)

    absences = root / "absences"
    absences.write_text("file")
    with pytest.raises(EvaluationError, match="capture is unusable"):
        load_pre_close(tmp_path, SESSION)
    absences.unlink()
    absences.mkdir()
    (absences / "input=candidate").write_text("file")
    with pytest.raises(EvaluationError, match="capture is unusable"):
        load_pre_close(tmp_path, SESSION)
    (absences / "input=candidate").unlink()
    (absences / "candidate").mkdir()
    with pytest.raises(EvaluationError, match="capture is unusable"):
        load_pre_close(tmp_path, SESSION)
    (absences / "candidate").rmdir()
    kind = absences / "input=candidate"
    kind.mkdir()
    (kind / "symbol=SOLUSDT.json").write_text("[]")
    with pytest.raises(EvaluationError, match="capture is unusable"):
        load_pre_close(tmp_path, SESSION)
    (kind / "symbol=SOLUSDT.json").write_text(
        json.dumps(
            {
                "input": "daily_bar",
                "produced_at": "2026-10-06T18:00:00Z",
                "source": "coingecko",
                "symbol": "SOLUSDT",
            }
        )
    )
    with pytest.raises(EvaluationError, match="capture is unusable"):
        load_pre_close(tmp_path, SESSION)
    (kind / "symbol=SOLUSDT.json").write_text(
        json.dumps(
            {
                "input": "candidate",
                "source": "coingecko",
                "symbol": "SOLUSDT",
            }
        )
    )
    with pytest.raises(EvaluationError, match="capture is unusable"):
        load_pre_close(tmp_path, SESSION)
    shutil.rmtree(absences)

    regime = root / "regime"
    regime.mkdir()
    (regime / "btc_dominance.json").write_text("[]")
    with pytest.raises(EvaluationError, match="capture is unusable"):
        load_pre_close(tmp_path, SESSION)
    (regime / "btc_dominance.json").write_text(
        json.dumps({"series": "btc_dominance", "values": [], "units": {}})
    )
    with pytest.raises(EvaluationError, match="capture is unusable"):
        load_pre_close(tmp_path, SESSION)
    (regime / "funding.json").write_text(
        json.dumps(
            {
                "captured_at": "2026-10-06T18:00:00Z",
                "observed_at": "2026-10-06T18:00:00Z",
                "provider": "binance",
                "series": "funding",
                "source_timestamp": "2026-10-06T18:00:00Z",
                "symbol": "BTCUSDT",
                "units": {"funding_rate": "rate"},
                "values": {"funding_rate": "0.0001"},
            }
        )
    )
    (regime / "btc_dominance.json").unlink()
    with pytest.raises(EvaluationError, match="capture is unusable"):
        load_pre_close(tmp_path, SESSION)

    universe = root / "universe.json"
    universe.write_text(
        json.dumps(
            {
                "captured_at": 1,
                "observed_at": "2026-10-06T18:00:00Z",
                "session": "2026-10-06",
                "source": "binance/exchangeInfo",
                "source_timestamp": "2026-10-06T18:00:00Z",
                "symbols": ["BTCUSDT"],
            }
        )
    )
    with pytest.raises(EvaluationError, match="capture is unusable"):
        load_pre_close(tmp_path, SESSION)
    universe.write_text(
        json.dumps(
            {
                "captured_at": "not-a-time",
                "observed_at": "2026-10-06T18:00:00Z",
                "session": "2026-10-06",
                "source": "binance/exchangeInfo",
                "source_timestamp": "2026-10-06T18:00:00Z",
                "symbols": ["BTCUSDT"],
            }
        )
    )
    with pytest.raises(EvaluationError, match="capture is unusable"):
        load_pre_close(tmp_path, SESSION)
    universe.write_text(
        json.dumps(
            {
                "captured_at": "2026-10-06T18:00:00Z",
                "observed_at": "2026-10-06T18:00:00Z",
                "session": "2026-10-06",
                "source": 1,
                "source_timestamp": "2026-10-06T18:00:00Z",
                "symbols": ["BTCUSDT"],
            }
        )
    )
    with pytest.raises(EvaluationError, match="capture is unusable"):
        load_pre_close(tmp_path, SESSION)
    document = json.loads(universe.read_text())
    document["source"] = "binance/exchangeInfo"
    document["symbols"] = [1]
    universe.write_text(json.dumps(document))
    with pytest.raises(EvaluationError, match="capture is unusable"):
        load_pre_close(tmp_path, SESSION)


def test_pre_close_load_without_a_universe_is_empty(tmp_path: Path) -> None:
    loaded = load_pre_close(tmp_path, SESSION)
    assert loaded.universe is None
    assert loaded.bars == ()
    assert loaded.packets == ()
