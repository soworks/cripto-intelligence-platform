import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from cip.domain.errors import ScorecardError
from cip.evaluation.decision import (
    Cohort,
    DecisionRecord,
    Disposition,
    ForwardOutcome,
    SourceStamp,
    decision_id,
)
from cip.evaluation.evidence import previous_sunday, scorecard_from_evidence

CLOSE = datetime(2026, 10, 4, tzinfo=UTC)
MONDAY = datetime(2026, 10, 12, tzinfo=UTC)


def _source() -> SourceStamp:
    return SourceStamp(name="daily_bars", observed_at=CLOSE, provenance="stored_daily_bars")


def _decision(**overrides: object) -> DecisionRecord:
    values: dict[str, object] = {
        "cohort": Cohort.SHADOW,
        "symbol": "SOLUSDT",
        "evaluated_at": CLOSE,
        "policy_version": "policy",
        "git_sha": "a" * 40,
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


def _file(record: DecisionRecord, *, body: bytes | None = None) -> tuple[str, bytes]:
    identity = decision_id(record)
    payload = json.dumps(record.to_document(), sort_keys=True).encode()
    return f"decisions/cohort=SHADOW/{identity}.json", body if body is not None else payload


def _outcome(record: DecisionRecord) -> bytes:
    outcome = ForwardOutcome(
        decision_id=decision_id(record),
        horizon_days=7,
        absolute_return=Decimal("-0.08"),
        btc_return=Decimal("-0.18"),
        excess_return=Decimal("0.10"),
        universe_relative_return=None,
        mfe=Decimal("0.01"),
        mae=Decimal("-0.02"),
        price_timestamp=CLOSE,
        btc_price_timestamp=CLOSE,
    )
    return json.dumps(outcome.to_document(), sort_keys=True).encode()


def _trade(record: DecisionRecord, **overrides: object) -> bytes:
    document: dict[str, object] = {
        "decision_id": decision_id(record),
        "realized_r": "-0.1",
        "mae": "-0.02",
        "mfe": "0.01",
        "fees_usd": "0.15",
        "slippage": "0.001",
    }
    document.update(overrides)
    return json.dumps(document, sort_keys=True).encode()


def _portfolio() -> dict[str, object]:
    return {
        "btc_dca_excess": None,
        "btc_eth_dca_excess": None,
        "equal_weight_excess": None,
        "random_baseline_excess": None,
        "sharpe": None,
        "sortino": None,
        "exposure": None,
        "turnover": None,
        "fee_drag_usd": None,
    }


def test_a_monday_names_the_sunday_that_just_ended() -> None:
    sunday = datetime(2026, 10, 11, tzinfo=UTC)
    assert previous_sunday(MONDAY) == sunday.date()
    assert previous_sunday(sunday) == sunday.date()
    with pytest.raises(ScorecardError, match="UTC"):
        previous_sunday(datetime(2026, 10, 12))  # noqa: DTZ001
    with pytest.raises(ScorecardError, match="UTC"):
        previous_sunday(datetime(2026, 10, 12, tzinfo=timezone(timedelta(hours=1))))


def test_an_empty_store_is_a_no_trade_week_with_named_missing_benchmarks() -> None:
    card = scorecard_from_evidence(decisions=(), outcomes=(), trades=(), portfolio=None)
    document = card.to_document()
    assert document["decision_integrity"]["decisions"] == 0
    assert document["signal_quality"] == []
    assert document["trade_quality"] == {"status": "no_trades", "trades": []}
    assert document["portfolio_quality"] == _portfolio()
    assert "expectancy" not in json.dumps(document)
    assert "order_id" not in json.dumps(document)


def test_a_replayed_decision_keeps_its_outcome_and_its_trade() -> None:
    record = _decision()
    card = scorecard_from_evidence(
        decisions=(_file(record),),
        outcomes=(_outcome(record),),
        trades=(_trade(record),),
        portfolio=json.dumps({**_portfolio(), "btc_dca_excess": "0.01"}).encode(),
    )
    document = card.to_document()
    assert document["decision_integrity"]["reproducible"] == 1
    assert document["decision_integrity"]["replay_disagreements"] == 0
    signal = document["signal_quality"][0]
    assert signal["decision_id"] == decision_id(record)
    assert signal["excess_return"] == "0.10"
    assert signal["rank"] == 1
    assert signal["coefficient"] is None
    assert document["trade_quality"]["status"] == "reported"
    assert document["trade_quality"]["trades"][0]["decision_id"] == decision_id(record)
    assert document["portfolio_quality"]["btc_dca_excess"] == "0.01"
    assert document["portfolio_quality"]["sharpe"] is None


def test_missing_and_policy_reasons_are_counted_without_hiding_a_replay() -> None:
    stale = _decision(
        symbol="ADAUSDT",
        disposition=Disposition.INELIGIBLE,
        reason_codes=("missing_candidate",),
        features={},
        score=None,
        score_components=None,
        rank=None,
        regime=None,
        raw_regime=None,
    )
    violated = _decision(
        symbol="ETHUSDT",
        disposition=Disposition.INELIGIBLE,
        reason_codes=("policy_violation", "stale_input"),
        features={},
        score=None,
        score_components=None,
        rank=None,
        regime=None,
        raw_regime=None,
    )
    card = scorecard_from_evidence(
        decisions=(_file(stale), _file(violated)),
        outcomes=(),
        trades=(),
        portfolio=None,
    )
    integrity = card.to_document()["decision_integrity"]
    assert integrity["decisions"] == 2
    assert integrity["reproducible"] == 2
    assert integrity["stale_or_missing"] == 2
    assert integrity["policy_violations"] == 1


def test_a_changed_or_misnamed_or_unreadable_decision_is_not_reproducible() -> None:
    record = _decision()
    key, body = _file(record)
    changed = body + b"\n"
    card = scorecard_from_evidence(
        decisions=(
            (key, changed),
            ("decisions/bad.json", b"not-json"),
            ("decisions/list.json", b"[]"),
            ("decisions/partial.json", b'{"schema_version": 2}'),
        ),
        outcomes=(),
        trades=(),
        portfolio=None,
    )
    integrity = card.to_document()["decision_integrity"]
    assert integrity["decisions"] == 4
    assert integrity["reproducible"] == 0
    assert integrity["replay_disagreements"] == 1
    assert integrity["invalid_decisions"] == 3
    misnamed = scorecard_from_evidence(
        decisions=((key.replace(decision_id(record), "b" * 64), body),),
        outcomes=(),
        trades=(),
        portfolio=None,
    )
    assert misnamed.to_document()["decision_integrity"]["replay_disagreements"] == 1


def test_two_files_for_one_decision_are_refused() -> None:
    record = _decision()
    key, body = _file(record)
    with pytest.raises(ScorecardError, match="ambiguous"):
        scorecard_from_evidence(
            decisions=((key, body), (f"decisions/other/{decision_id(record)}.json", body)),
            outcomes=(),
            trades=(),
            portfolio=None,
        )


def test_an_outcome_or_trade_without_its_decision_is_refused() -> None:
    record = _decision()
    with pytest.raises(ScorecardError, match="outcome has no stored decision"):
        scorecard_from_evidence(
            decisions=(), outcomes=(_outcome(record),), trades=(), portfolio=None
        )
    with pytest.raises(ScorecardError, match="trade has no stored decision"):
        scorecard_from_evidence(decisions=(), outcomes=(), trades=(_trade(record),), portfolio=None)


def test_a_broken_outcome_trade_or_benchmark_is_refused() -> None:
    record = _decision()
    stored = (_file(record),)
    with pytest.raises(ScorecardError, match="outcome is unusable"):
        scorecard_from_evidence(decisions=stored, outcomes=(b"\xff",), trades=(), portfolio=None)
    with pytest.raises(ScorecardError, match="outcome is unusable"):
        scorecard_from_evidence(decisions=stored, outcomes=(b"{}",), trades=(), portfolio=None)
    with pytest.raises(ScorecardError, match="trade is unusable"):
        scorecard_from_evidence(decisions=stored, outcomes=(), trades=(b"[]",), portfolio=None)
    with pytest.raises(ScorecardError, match="trade uses the stored fields"):
        scorecard_from_evidence(
            decisions=stored,
            outcomes=(),
            trades=(json.dumps({"decision_id": decision_id(record), "order_id": "1"}).encode(),),
            portfolio=None,
        )
    with pytest.raises(ScorecardError, match="portfolio results name each comparison"):
        scorecard_from_evidence(decisions=(), outcomes=(), trades=(), portfolio=b"{}")
    with pytest.raises(ScorecardError, match="decimal strings"):
        scorecard_from_evidence(
            decisions=stored,
            outcomes=(),
            trades=(_trade(record, fees_usd="-0"),),
            portfolio=None,
        )
    with pytest.raises(ScorecardError, match="decimal strings"):
        scorecard_from_evidence(
            decisions=stored, outcomes=(), trades=(_trade(record, fees_usd="nope"),), portfolio=None
        )
    with pytest.raises(ScorecardError, match="decimal strings"):
        scorecard_from_evidence(
            decisions=stored, outcomes=(), trades=(_trade(record, fees_usd=1),), portfolio=None
        )
    with pytest.raises(ScorecardError, match="decimal strings"):
        scorecard_from_evidence(
            decisions=stored, outcomes=(), trades=(_trade(record, fees_usd="NaN"),), portfolio=None
        )
