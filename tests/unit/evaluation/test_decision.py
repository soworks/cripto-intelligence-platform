import hashlib
import json
import os
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from cip.domain.errors import EvaluationError
from cip.evaluation.decision import (
    Cohort,
    DecisionRecord,
    Disposition,
    ForwardOutcome,
    SourceStamp,
    decision_id,
)
from cip.evaluation.store import (
    ObjectDecisionWriter,
    _require_inside,
    append_decision,
    append_decision_to,
    append_outcome,
    decision_key,
)

SHA = "a" * 40
NOW = datetime(2026, 10, 4, tzinfo=UTC)


def _source() -> SourceStamp:
    return SourceStamp(name="klines", observed_at=NOW, provenance="klines/SOLUSDT/2026-10-04")


def _buy(**overrides: object) -> DecisionRecord:
    values: dict[str, object] = {
        "cohort": Cohort.SHADOW,
        "symbol": "SOLUSDT",
        "evaluated_at": NOW,
        "policy_version": "policy-sha",
        "git_sha": SHA,
        "disposition": Disposition.BUY,
        "reason_codes": ("score_above_minimum",),
        "features": {"rs_30d": Decimal("0.10")},
        "score": Decimal("80"),
        "score_components": {"trend": Decimal("50"), "rs": Decimal("30")},
        "rank": 1,
        "regime": "RISK_ON",
        "sources": (_source(),),
    }
    values.update(overrides)
    return DecisionRecord(**values)  # type: ignore[arg-type]


def _ineligible(**overrides: object) -> DecisionRecord:
    values: dict[str, object] = {
        "disposition": Disposition.INELIGIBLE,
        "reason_codes": ("missing_market_cap",),
        "features": {},
        "score": None,
        "score_components": None,
        "rank": None,
        "regime": None,
    }
    values.update(overrides)
    return _buy(**values)


def test_a_buy_document_keeps_decimals_and_has_no_order_fields() -> None:
    record = _buy(cohort=Cohort.ALPHA_PILOT_2026_10)
    document = record.to_document()
    assert document["cohort"] == "ALPHA_PILOT_2026_10"
    assert document["score"] == "80"
    assert document["features"]["rs_30d"] == "0.10"
    assert "quantity" not in document
    assert "order_id" not in document
    assert DecisionRecord.from_document(document) == record


def test_an_ineligible_record_cannot_carry_a_score() -> None:
    with pytest.raises(ValidationError):
        _ineligible(score=Decimal("10"))


def test_a_buy_requires_a_risk_on_or_neutral_regime() -> None:
    with pytest.raises(ValidationError):
        _buy(regime="RISK_OFF")
    with pytest.raises(ValidationError):
        _buy(regime=None)


def test_a_buy_requires_rank_features_and_components() -> None:
    with pytest.raises(ValidationError):
        _buy(rank=None)
    with pytest.raises(ValidationError):
        _buy(features={})
    with pytest.raises(ValidationError):
        _buy(score_components={})


def test_reason_codes_and_git_sha_are_explicit() -> None:
    with pytest.raises(ValidationError):
        _buy(reason_codes=())
    with pytest.raises(ValidationError):
        _buy(reason_codes=("Score Above",))
    with pytest.raises(ValidationError):
        _buy(git_sha="abc")


def test_the_same_decision_is_kept_and_a_revision_is_refused(tmp_path: Path) -> None:
    record = _buy()
    assert append_decision(tmp_path, record) is True
    assert append_decision(tmp_path, record) is False
    revised = _buy(
        score=Decimal("81"),
        score_components={"trend": Decimal("51"), "rs": Decimal("30")},
    )
    path = next((tmp_path / "decisions").rglob("*.json"))
    original = path.read_bytes()
    with pytest.raises(EvaluationError, match="different payload"):
        append_decision(tmp_path, revised)
    assert path.read_bytes() == original
    assert decision_id(record) == decision_id(revised)
    stored = json.loads(original)
    assert stored["score"] == "80"


def test_an_early_outcome_is_refused_and_a_due_outcome_leaves_the_decision(
    tmp_path: Path,
) -> None:
    record = _ineligible()
    append_decision(tmp_path, record)
    decision_path = next((tmp_path / "decisions").rglob("*.json"))
    original = decision_path.read_bytes()
    outcome = ForwardOutcome(
        decision_id=decision_id(record),
        horizon_days=7,
        absolute_return=Decimal("-0.08"),
        btc_return=Decimal("-0.18"),
        excess_return=Decimal("0.10"),
        universe_relative_return=None,
        mfe=Decimal("0.02"),
        mae=Decimal("-0.09"),
        price_timestamp=NOW + timedelta(days=7),
        btc_price_timestamp=NOW + timedelta(days=7),
    )
    with pytest.raises(EvaluationError, match="horizon"):
        append_outcome(tmp_path, outcome, as_of=NOW + timedelta(days=6))
    assert list((tmp_path / "decision-outcomes").rglob("*.json")) == []
    assert append_outcome(tmp_path, outcome, as_of=NOW + timedelta(days=7)) is True
    assert append_outcome(tmp_path, outcome, as_of=NOW + timedelta(days=8)) is False
    assert decision_path.read_bytes() == original
    stored = json.loads(next((tmp_path / "decision-outcomes").rglob("*.json")).read_text())
    assert stored["horizon_days"] == 7
    assert "disposition" not in stored
    assert "score" not in stored


def test_malformed_evidence_and_clocks_are_rejected(tmp_path: Path) -> None:
    record = _buy()
    document = record.to_document()
    document["score"] = 80
    with pytest.raises(ValueError, match="strings"):
        DecisionRecord.from_document(document)
    document = record.to_document()
    document["score"] = "nope"
    with pytest.raises(ValueError, match="strings"):
        DecisionRecord.from_document(document)
    document = record.to_document()
    document["score"] = "NaN"
    with pytest.raises(ValueError, match="finite"):
        DecisionRecord.from_document(document)
    document = record.to_document()
    document["features"] = ["nope"]
    with pytest.raises(ValueError, match="objects"):
        DecisionRecord.from_document(document)
    with pytest.raises(ValidationError):
        _buy(symbol="sol")
    naive = datetime(2026, 10, 4)  # noqa: DTZ001
    with pytest.raises(ValidationError):
        _buy(evaluated_at=naive)
    with pytest.raises(ValidationError):
        _buy(evaluated_at=datetime(2026, 10, 4, tzinfo=timezone(timedelta(hours=-5))))
    with pytest.raises(ValidationError):
        _buy(features={"rs_30d": Decimal("NaN")})
    with pytest.raises(ValidationError):
        _buy(score=Decimal("Infinity"))
    with pytest.raises(ValidationError):
        _source_at(datetime(2026, 10, 4, tzinfo=timezone(timedelta(hours=1))))
    ahead = timezone(timedelta(hours=1))
    with pytest.raises(ValidationError):
        ForwardOutcome(
            decision_id="b" * 64,
            horizon_days=7,
            absolute_return=Decimal("0"),
            btc_return=Decimal("0"),
            excess_return=Decimal("0"),
            universe_relative_return=None,
            mfe=Decimal("-0.01"),
            mae=Decimal("0"),
            price_timestamp=NOW,
            btc_price_timestamp=NOW,
        )
    with pytest.raises(ValidationError):
        ForwardOutcome(
            decision_id="c" * 64,
            horizon_days=7,
            absolute_return=Decimal("0"),
            btc_return=Decimal("0"),
            excess_return=Decimal("0"),
            universe_relative_return=None,
            mfe=Decimal("0"),
            mae=Decimal("0.01"),
            price_timestamp=NOW,
            btc_price_timestamp=NOW,
        )
    with pytest.raises(ValidationError):
        ForwardOutcome(
            decision_id="d" * 64,
            horizon_days=7,
            absolute_return=Decimal("NaN"),
            btc_return=Decimal("0"),
            excess_return=Decimal("0"),
            universe_relative_return=None,
            mfe=Decimal("0"),
            mae=Decimal("0"),
            price_timestamp=NOW,
            btc_price_timestamp=NOW,
        )
    append_decision(tmp_path, record)
    outcome = ForwardOutcome(
        decision_id=decision_id(record),
        horizon_days=7,
        absolute_return=Decimal("0"),
        btc_return=Decimal("0"),
        excess_return=Decimal("0"),
        universe_relative_return=None,
        mfe=Decimal("0"),
        mae=Decimal("0"),
        price_timestamp=NOW,
        btc_price_timestamp=NOW,
    )
    naive_as_of = datetime(2026, 10, 11)  # noqa: DTZ001
    with pytest.raises(EvaluationError, match="UTC"):
        append_outcome(tmp_path, outcome, as_of=naive_as_of)
    with pytest.raises(EvaluationError, match="UTC"):
        append_outcome(tmp_path, outcome, as_of=datetime(2026, 10, 11, tzinfo=ahead))


def _source_at(moment: datetime) -> SourceStamp:
    return SourceStamp(name="klines", observed_at=moment, provenance="klines/SOLUSDT")


def test_an_outcome_cannot_invent_excess_return_or_a_missing_decision(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        ForwardOutcome(
            decision_id="a" * 64,
            horizon_days=14,
            absolute_return=Decimal("-0.08"),
            btc_return=Decimal("-0.18"),
            excess_return=Decimal("0.01"),
            universe_relative_return=None,
            mfe=Decimal("0"),
            mae=Decimal("0"),
            price_timestamp=NOW,
            btc_price_timestamp=NOW,
        )
    record = _buy()
    outcome = ForwardOutcome(
        decision_id=decision_id(record),
        horizon_days=30,
        absolute_return=Decimal("0.01"),
        btc_return=Decimal("0.02"),
        excess_return=Decimal("-0.01"),
        universe_relative_return=Decimal("-0.04"),
        mfe=Decimal("0.05"),
        mae=Decimal("-0.02"),
        price_timestamp=NOW + timedelta(days=30),
        btc_price_timestamp=NOW + timedelta(days=30),
    )
    with pytest.raises(EvaluationError, match="decision"):
        append_outcome(tmp_path, outcome, as_of=NOW + timedelta(days=30))


def test_a_float_cannot_be_frozen_as_a_decision_or_an_outcome() -> None:
    with pytest.raises(ValidationError):
        _buy(score=0.1 + 0.2)
    with pytest.raises(ValidationError):
        _buy(features={"rs_30d": 0.1 + 0.2})
    with pytest.raises(ValidationError):
        _buy(score=True)
    with pytest.raises(ValidationError):
        ForwardOutcome(
            decision_id="e" * 64,
            horizon_days=7,
            absolute_return=0.1 + 0.2,
            btc_return=Decimal("0"),
            excess_return=Decimal("0"),
            universe_relative_return=None,
            mfe=Decimal("0"),
            mae=Decimal("0"),
            price_timestamp=NOW,
            btc_price_timestamp=NOW,
        )
    record = _buy(score="80")
    assert record.score == Decimal("80")


def test_a_rewritten_or_duplicated_decision_cannot_open_an_outcome(tmp_path: Path) -> None:
    record = _buy()
    append_decision(tmp_path, record)
    path = next((tmp_path / "decisions").rglob("*.json"))
    original = path.read_bytes()
    document = json.loads(original)
    document["evaluated_at"] = "2020-01-01T00:00:00+00:00"
    path.write_text(json.dumps(document))
    outcome = ForwardOutcome(
        decision_id=decision_id(record),
        horizon_days=60,
        absolute_return=Decimal("0"),
        btc_return=Decimal("0"),
        excess_return=Decimal("0"),
        universe_relative_return=None,
        mfe=Decimal("0"),
        mae=Decimal("0"),
        price_timestamp=NOW + timedelta(days=60),
        btc_price_timestamp=NOW + timedelta(days=60),
    )
    with pytest.raises(EvaluationError, match="match its id"):
        append_outcome(tmp_path, outcome, as_of=NOW + timedelta(days=1))
    assert list((tmp_path / "decision-outcomes").rglob("*.json")) == []
    assert path.read_bytes() != original

    path.write_bytes(original)
    duplicate = path.parent.parent.parent / "date=2020-01-01" / "symbol=SOLUSDT" / path.name
    duplicate.parent.mkdir(parents=True)
    duplicate.write_bytes(original)
    with pytest.raises(EvaluationError, match="ambiguous"):
        append_outcome(tmp_path, outcome, as_of=NOW + timedelta(days=60))
    assert list((tmp_path / "decision-outcomes").rglob("*.json")) == []

    duplicate.unlink()
    path.write_text("{")
    with pytest.raises(EvaluationError, match="unusable"):
        append_outcome(tmp_path, outcome, as_of=NOW + timedelta(days=60))
    assert list((tmp_path / "decision-outcomes").rglob("*.json")) == []


def test_a_decision_document_with_an_order_field_is_refused() -> None:
    document = _buy().to_document()
    document["order_id"] = "1"
    with pytest.raises(ValueError, match="keys"):
        DecisionRecord.from_document(document)
    document = _buy().to_document()
    document["sources"] = "klines"
    with pytest.raises(ValueError, match="keys"):
        DecisionRecord.from_document(document)
    document = _buy().to_document()
    document["sources"] = [{"name": "klines"}]
    with pytest.raises(ValueError, match="keys"):
        DecisionRecord.from_document(document)
    document = _buy().to_document()
    document["features"] = {1: "0.10"}
    with pytest.raises(ValueError, match="strings"):
        DecisionRecord.from_document(document)


def test_non_decimal_evidence_is_refused() -> None:
    with pytest.raises(ValidationError):
        _buy(score=object())
    with pytest.raises(ValidationError):
        _buy(features=[])
    with pytest.raises(ValidationError):
        _buy(features={1: Decimal("0.10")})


def test_a_decision_path_cannot_leave_the_store(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.json"
    with pytest.raises(EvaluationError, match="escapes"):
        _require_inside(tmp_path / "decisions", outside)


def test_a_lost_create_keeps_the_first_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _buy()
    real_link = os.link

    def lose_the_race(source: str, destination: str) -> None:
        real_link(source, destination)
        raise FileExistsError

    monkeypatch.setattr(os, "link", lose_the_race)
    assert append_decision(tmp_path, record) is False
    stored = next((tmp_path / "decisions").rglob("*.json"))
    assert json.loads(stored.read_text())["score"] == "80"
    assert stored.relative_to(tmp_path).as_posix() == decision_key(record)


class _Memory:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def read(self, key: str) -> bytes | None:
        return self.objects.get(key)

    def create(self, key: str, body: bytes) -> bool:
        if key in self.objects:
            return False
        self.objects[key] = body
        return True


class _Scripted:
    def __init__(self, reads: list[bytes | None], *, created: bool) -> None:
        self._reads = list(reads)
        self._created = created

    def read(self, key: str) -> bytes | None:
        return self._reads.pop(0)

    def create(self, key: str, body: bytes) -> bool:
        return self._created


def test_an_object_store_keeps_the_first_body() -> None:
    store = _Memory()
    record = _buy()
    stored = ObjectDecisionWriter(store).write(record)
    assert stored.created is True
    assert stored.key == decision_key(record)
    assert stored.sha256 == hashlib.sha256(store.objects[stored.key]).hexdigest()
    assert append_decision_to(store, record).created is False
    revised = _buy(
        score=Decimal("81"),
        score_components={"trend": Decimal("51"), "rs": Decimal("30")},
    )
    with pytest.raises(EvaluationError, match="different payload"):
        append_decision_to(store, revised)
    assert json.loads(store.objects[stored.key])["score"] == "80"


def test_a_lost_object_create_keeps_matching_bytes() -> None:
    record = _buy()
    body = json.dumps(record.to_document(), sort_keys=True).encode()
    stored = append_decision_to(_Scripted([None, body], created=False), record)
    assert stored.created is False
    assert stored.sha256 == hashlib.sha256(body).hexdigest()


def test_a_lost_object_create_refuses_a_different_body() -> None:
    with pytest.raises(EvaluationError, match="different payload"):
        append_decision_to(_Scripted([None, b"other"], created=False), _buy())


def test_a_lost_object_create_refuses_a_missing_body() -> None:
    with pytest.raises(EvaluationError, match="disappeared"):
        append_decision_to(_Scripted([None, None], created=False), _buy())
