import json
import os
from datetime import date
from pathlib import Path

import pytest

from cip.domain.errors import ScorecardError
from cip.evaluation.scorecard import report_scorecard
from cip.evaluation.weekly import weekly_scorecard, write_weekly_scorecard

WEEK = date(2026, 10, 5)


def _card(decisions: int = 1):
    return report_scorecard(
        decisions=decisions,
        reproducible=decisions,
        policy_violations=0,
        stale_or_missing=0,
        replay_disagreements=0,
        invalid_decisions=0,
        signals=(),
        trades=(),
        portfolio={
            "btc_dca_excess": None,
            "btc_eth_dca_excess": None,
            "equal_weight_excess": None,
            "random_baseline_excess": None,
            "sharpe": None,
            "sortino": None,
            "exposure": None,
            "turnover": None,
            "fee_drag_usd": None,
        },
    )


def test_a_week_is_the_stored_scorecard_and_a_retry_keeps_it(tmp_path: Path) -> None:
    card = _card()
    document = weekly_scorecard(week_ending=WEEK, scorecard=card)
    assert document["week_ending"] == "2026-10-05"
    assert set(document["scorecard"]) == {  # type: ignore[arg-type]
        "decision_integrity",
        "signal_quality",
        "trade_quality",
        "portfolio_quality",
    }
    assert "expectancy" not in json.dumps(document)
    assert "order_id" not in json.dumps(document)

    assert write_weekly_scorecard(tmp_path, WEEK, card) is True
    assert write_weekly_scorecard(tmp_path, WEEK, card) is False
    stored = json.loads((tmp_path / "assurance" / "week=2026-10-05" / "scorecard.json").read_text())
    assert stored == document


def test_a_different_week_is_refused(tmp_path: Path) -> None:
    write_weekly_scorecard(tmp_path, WEEK, _card())
    with pytest.raises(ScorecardError, match="different payload"):
        write_weekly_scorecard(tmp_path, WEEK, _card(decisions=2))


def test_a_weekly_report_refuses_a_timestamp_or_a_grade(tmp_path: Path) -> None:
    with pytest.raises(ScorecardError, match="date"):
        weekly_scorecard(week_ending="2026-10-05", scorecard=_card())  # type: ignore[arg-type]
    with pytest.raises(ScorecardError, match="scorecard"):
        weekly_scorecard(week_ending=WEEK, scorecard={"grade": "A"})  # type: ignore[arg-type]


def test_a_lost_create_keeps_the_first_week(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_link = os.link

    def lose_the_race(source: str, destination: str) -> None:
        real_link(source, destination)
        raise FileExistsError

    monkeypatch.setattr(os, "link", lose_the_race)
    assert write_weekly_scorecard(tmp_path, WEEK, _card()) is False
    stored = json.loads((tmp_path / "assurance" / "week=2026-10-05" / "scorecard.json").read_text())
    assert stored["week_ending"] == "2026-10-05"
