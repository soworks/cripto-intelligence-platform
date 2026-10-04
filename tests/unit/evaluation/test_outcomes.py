from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from cip.domain.errors import EvaluationError
from cip.evaluation.decision import (
    Cohort,
    DecisionRecord,
    Disposition,
    SourceStamp,
    decision_id,
)
from cip.evaluation.outcomes import measure_outcome, record_forward
from cip.evaluation.store import append_decision, decision_key
from cip.history.bars import DailyBar

CLOSE = datetime(2026, 10, 4, tzinfo=UTC)
ENTRY = date(2026, 10, 3)
END = date(2026, 10, 10)
DUE = datetime(2026, 10, 11, tzinfo=UTC)
SHA = "a" * 40


def _source() -> SourceStamp:
    return SourceStamp(name="daily_bars", observed_at=CLOSE, provenance="stored_daily_bars")


def _record(symbol: str = "SOLUSDT", **overrides: object) -> DecisionRecord:
    values: dict[str, object] = {
        "cohort": Cohort.SHADOW,
        "symbol": symbol,
        "evaluated_at": CLOSE,
        "policy_version": "policy",
        "git_sha": SHA,
        "disposition": Disposition.SCORED,
        "reason_codes": ("below_min_score",),
        "features": {"circulating_ratio": Decimal("1")},
        "score": Decimal("35"),
        "score_components": {"tokenomics": Decimal("35")},
        "rank": 1,
        "regime": "RISK_ON",
        "raw_regime": "RISK_ON",
        "sources": (_source(),),
    }
    values.update(overrides)
    return DecisionRecord(**values)  # type: ignore[arg-type]


def _bar(
    symbol: str,
    day: date,
    close: str,
    *,
    high: str | None = None,
    low: str | None = None,
) -> DailyBar:
    price = Decimal(close)
    peak = Decimal(high) if high is not None else price
    trough = Decimal(low) if low is not None else price
    return DailyBar(
        symbol=symbol,
        open_date=day,
        open=price,
        high=peak,
        low=trough,
        close=price,
        volume=Decimal("1"),
        quote_volume=Decimal("1"),
        trade_count=1,
        taker_buy_base_volume=Decimal("1"),
        taker_buy_quote_volume=Decimal("1"),
    )


def _span(
    symbol: str,
    close: str = "100",
    *,
    end_close: str | None = None,
    spike_high: str | None = None,
    spike_low: str | None = None,
) -> list[DailyBar]:
    bars: list[DailyBar] = []
    day = ENTRY
    while day <= END:
        price = end_close if day == END and end_close is not None else close
        high = spike_high if day == ENTRY + timedelta(days=2) else None
        low = spike_low if day == ENTRY + timedelta(days=3) else None
        bars.append(_bar(symbol, day, price, high=high, low=low))
        day += timedelta(days=1)
    return bars


def _measure(**overrides: object) -> object:
    values: dict[str, object] = {
        "record": _record(),
        "horizon_days": 7,
        "as_of": DUE,
        "bars": _span("SOLUSDT", end_close="120", spike_high="130", spike_low="80"),
        "btc_bars": _span("BTCUSDT", close="200", end_close="220"),
        "peers": (),
    }
    values.update(overrides)
    return measure_outcome(**values)  # type: ignore[arg-type]


def test_a_closed_horizon_is_the_close_to_close_return_after_the_decision() -> None:
    outcome = _measure()
    assert outcome.absolute_return == Decimal("0.2")
    assert outcome.btc_return == Decimal("0.1")
    assert outcome.excess_return == Decimal("0.1")
    assert outcome.mfe == Decimal("0.3")
    assert outcome.mae == Decimal("-0.2")
    assert outcome.universe_relative_return == Decimal("0")
    assert outcome.price_timestamp == DUE
    assert outcome.btc_price_timestamp == DUE
    assert outcome.decision_id == decision_id(_record())
    assert outcome.horizon_days == 7
    document = outcome.to_document()
    assert "disposition" not in document
    assert "score" not in document


def test_the_entry_bar_and_a_later_bar_do_not_move_the_excursion() -> None:
    bars = _span("SOLUSDT", end_close="120", spike_high="130", spike_low="80")
    bars[0] = _bar("SOLUSDT", ENTRY, "100", high="500")
    bars.append(_bar("SOLUSDT", END + timedelta(days=1), "100", high="900"))
    stale = _bar("SOLUSDT", ENTRY - timedelta(days=1), "100")
    object.__setattr__(stale, "close", Decimal("0"))
    bars.insert(0, stale)
    outcome = _measure(bars=bars)
    assert outcome.mfe == Decimal("0.3")
    assert outcome.absolute_return == Decimal("0.2")


def test_a_flat_path_has_no_excursion() -> None:
    outcome = _measure(
        bars=_span("SOLUSDT"),
        btc_bars=_span("BTCUSDT"),
    )
    assert outcome.absolute_return == Decimal("0")
    assert outcome.btc_return == Decimal("0")
    assert outcome.excess_return == Decimal("0")
    assert outcome.mfe == Decimal("0")
    assert outcome.mae == Decimal("0")


def test_a_buy_uses_the_same_window_and_not_its_disposition() -> None:
    outcome = _measure(
        record=_record(
            disposition=Disposition.BUY,
            reason_codes=("buy_recommendation",),
            score=Decimal("70"),
            score_components={"tokenomics": Decimal("70")},
        ),
        bars=_span("SOLUSDT"),
        btc_bars=_span("BTCUSDT"),
    )
    assert outcome.universe_relative_return == Decimal("0")
    assert "buy" not in outcome.to_document()


def test_an_ineligible_name_still_has_a_return_and_no_universe_of_one() -> None:
    outcome = _measure(
        record=_record(
            disposition=Disposition.INELIGIBLE,
            reason_codes=("stablecoin",),
            features={},
            score=None,
            score_components=None,
            rank=None,
            regime=None,
        )
    )
    assert outcome.absolute_return == Decimal("0.2")
    assert outcome.universe_relative_return is None


def test_the_eligible_universe_is_the_stored_scored_names() -> None:
    scored = _record("ETHUSDT", rank=2)
    bought = _record(
        "ADAUSDT",
        disposition=Disposition.BUY,
        reason_codes=("buy_recommendation",),
        score=Decimal("70"),
        score_components={"tokenomics": Decimal("70")},
        rank=3,
    )
    outcome = _measure(
        peers=((scored, _span("ETHUSDT")), (bought, _span("ADAUSDT"))),
    )
    assert outcome.universe_relative_return == Decimal("0.2") - (Decimal("0.2") / 3)


def test_a_rejected_or_other_session_peer_is_not_in_the_universe() -> None:
    rejected = _record(
        "ETHUSDT",
        disposition=Disposition.INELIGIBLE,
        reason_codes=("stablecoin",),
        features={},
        score=None,
        score_components=None,
        rank=None,
        regime=None,
    )
    other_day = _record("ADAUSDT", evaluated_at=CLOSE - timedelta(days=1), rank=2)
    other_cohort = _record("XRPUSDT", cohort=Cohort.BACKTEST, rank=3)
    outcome = _measure(peers=((rejected, ()), (other_day, ()), (other_cohort, ())))
    assert outcome.universe_relative_return == Decimal("0")


def test_a_peer_without_the_window_leaves_the_universe_blank() -> None:
    peer = _record("ETHUSDT", rank=2)
    outcome = _measure(peers=((peer, _span("ETHUSDT")[:3]),))
    assert outcome.absolute_return == Decimal("0.2")
    assert outcome.universe_relative_return is None


def test_the_horizon_must_have_elapsed_and_be_a_known_length() -> None:
    with pytest.raises(EvaluationError, match="has not elapsed"):
        _measure(as_of=DUE - timedelta(seconds=1))
    with pytest.raises(EvaluationError, match="horizon"):
        _measure(horizon_days=8)
    with pytest.raises(EvaluationError, match="daily close"):
        _measure(record=_record(evaluated_at=CLOSE + timedelta(hours=1)))
    with pytest.raises(EvaluationError, match="UTC"):
        _measure(as_of=datetime(2026, 10, 11))  # noqa: DTZ001
    with pytest.raises(EvaluationError, match="UTC"):
        _measure(as_of=datetime(2026, 10, 11, tzinfo=timezone(timedelta(hours=1))))


def test_a_gap_a_bad_price_or_a_mismatched_symbol_is_not_an_outcome() -> None:
    gapped = [bar for bar in _span("SOLUSDT") if bar.open_date != ENTRY + timedelta(days=4)]
    with pytest.raises(EvaluationError, match="contiguous"):
        _measure(bars=gapped)
    broken = _span("SOLUSDT")
    broken[4] = _bar("SOLUSDT", broken[4].open_date, "100")
    object.__setattr__(broken[4], "close", 100)
    with pytest.raises(EvaluationError, match="invalid price"):
        _measure(bars=broken)
    mismatched = _span("SOLUSDT")
    mismatched[4] = _bar("ETHUSDT", mismatched[4].open_date, "100")
    with pytest.raises(EvaluationError, match="symbol"):
        _measure(bars=mismatched)
    duplicated = _span("SOLUSDT")
    duplicated.append(_bar("SOLUSDT", ENTRY + timedelta(days=1), "100"))
    with pytest.raises(EvaluationError, match="duplicate"):
        _measure(bars=duplicated)
    zeroed = _span("SOLUSDT")
    object.__setattr__(zeroed[4], "close", Decimal("0"))
    with pytest.raises(EvaluationError, match="invalid price"):
        _measure(bars=zeroed)
    missing_number = _span("SOLUSDT")
    object.__setattr__(missing_number[4], "close", Decimal("NaN"))
    with pytest.raises(EvaluationError, match="invalid price"):
        _measure(bars=missing_number)
    crossed = _span("SOLUSDT")
    crossed[4] = _bar("SOLUSDT", crossed[4].open_date, "100", high="90")
    with pytest.raises(EvaluationError, match="invalid price"):
        _measure(bars=crossed)
    lifted = _span("SOLUSDT")
    lifted[4] = _bar("SOLUSDT", lifted[4].open_date, "100", high="110", low="105")
    with pytest.raises(EvaluationError, match="invalid price"):
        _measure(bars=lifted)


def test_a_repeated_eligible_symbol_is_refused() -> None:
    peer = _record("ETHUSDT", rank=2)
    with pytest.raises(EvaluationError, match="repeats"):
        _measure(peers=((peer, _span("ETHUSDT")), (peer, _span("ETHUSDT"))))


def test_recording_an_outcome_leaves_the_decision_unchanged(tmp_path: Path) -> None:
    record = _record()
    append_decision(tmp_path, record)
    decision = tmp_path / decision_key(record)
    original = decision.read_bytes()
    kwargs = {
        "root": tmp_path,
        "record": record,
        "horizon_days": 7,
        "as_of": DUE,
        "bars": _span("SOLUSDT", end_close="120", spike_high="130", spike_low="80"),
        "btc_bars": _span("BTCUSDT", close="200", end_close="220"),
    }
    first = record_forward(**kwargs)
    second = record_forward(**kwargs)
    assert first == second
    assert decision.read_bytes() == original
    stored = list((tmp_path / "decision-outcomes").rglob("*.json"))
    assert len(stored) == 1
    body = stored[0].read_text()
    assert "disposition" not in body
    assert "0.2" in body


def test_the_evaluator_does_not_import_eligibility_or_scoring() -> None:
    source = Path(measure_outcome.__code__.co_filename).read_text()
    assert "cip.evaluation.eligibility" not in source
    assert "cip.evaluation.score" not in source
    assert "cip.adapters" not in source
