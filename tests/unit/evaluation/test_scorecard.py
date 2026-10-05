import warnings
from decimal import Decimal

import pytest
from pydantic import ValidationError

from cip.domain.errors import ScorecardError
from cip.evaluation.scorecard import Scorecard, report_scorecard

_DECISION = "a" * 64
_OTHER = "b" * 64
_TOP = {
    "decision_integrity",
    "signal_quality",
    "trade_quality",
    "portfolio_quality",
}


def _signal(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "decision_id": _DECISION,
        "horizon_days": 7,
        "absolute_return": Decimal("-0.08"),
        "btc_return": Decimal("-0.18"),
        "excess_return": Decimal("0.10"),
        "universe_relative_return": None,
        "rank": None,
        "coefficient": None,
    }
    values.update(overrides)
    return values


def _trade(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "decision_id": _DECISION,
        "realized_r": Decimal("1.5"),
        "mae": Decimal("-0.04"),
        "mfe": Decimal("0.20"),
        "fees_usd": Decimal("0.08"),
        "slippage": Decimal("0.001"),
    }
    values.update(overrides)
    return values


def _portfolio(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
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
    values.update(overrides)
    return values


def _counts(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "decisions": 2,
        "reproducible": 2,
        "policy_violations": 0,
        "stale_or_missing": 0,
        "replay_disagreements": 0,
        "invalid_decisions": 0,
        "signals": (),
        "trades": (),
        "portfolio": _portfolio(),
    }
    values.update(overrides)
    return values


def test_the_same_loss_is_two_results_and_not_a_grade() -> None:
    behind = _signal(
        decision_id=_OTHER,
        absolute_return=Decimal("-0.08"),
        btc_return=Decimal("0.10"),
        excess_return=Decimal("-0.18"),
        universe_relative_return=Decimal("0.01"),
        rank=1,
        coefficient=Decimal("0.25"),
    )
    card = report_scorecard(**_counts(signals=(_signal(), behind)))  # type: ignore[arg-type]
    document = card.to_document()
    assert set(document) == _TOP
    assert "grade" not in document
    assert "score_weights" not in document
    first, second = document["signal_quality"]
    assert first["absolute_return"] == second["absolute_return"] == "-0.08"
    assert first["excess_return"] == "0.10"
    assert second["excess_return"] == "-0.18"
    assert second["coefficient"] == "0.25"
    assert second["rank"] == 1
    assert first["coefficient"] is None
    assert document["trade_quality"] == {"status": "no_trades", "trades": []}
    assert document["portfolio_quality"]["btc_dca_excess"] is None
    assert document["decision_integrity"]["decisions"] == 2
    with pytest.raises(ValidationError):
        Scorecard.model_validate({**card.model_dump(), "grade": "fail"})


def test_a_closed_trade_is_listed_and_a_missing_comparison_stays_missing() -> None:
    card = report_scorecard(
        **_counts(  # type: ignore[arg-type]
            trades=(_trade(),),
            portfolio=_portfolio(btc_dca_excess=Decimal("0.02"), sharpe=Decimal("1.1")),
        )
    )
    document = card.to_document()
    assert document["trade_quality"]["status"] == "reported"
    assert document["trade_quality"]["trades"][0]["realized_r"] == "1.5"
    assert "order_id" not in document["trade_quality"]["trades"][0]
    assert document["portfolio_quality"]["btc_dca_excess"] == "0.02"
    assert document["portfolio_quality"]["sharpe"] == "1.1"
    assert document["portfolio_quality"]["sortino"] is None


def test_a_result_that_collapses_the_record_is_refused() -> None:
    with pytest.raises(ScorecardError, match="invalid"):
        report_scorecard(**_counts(signals=(_signal(excess_return=Decimal("9")),)))  # type: ignore[arg-type]
    with pytest.raises(ScorecardError, match="invalid"):
        report_scorecard(**_counts(signals=(_signal(coefficient=Decimal("70")),)))  # type: ignore[arg-type]
    with pytest.raises(ScorecardError, match="invalid"):
        report_scorecard(**_counts(signals=(_signal(coefficient=Decimal("-1.1")),)))  # type: ignore[arg-type]
    with pytest.raises(ScorecardError, match="invalid"):
        report_scorecard(**_counts(policy_violations=3))  # type: ignore[arg-type]
    with pytest.raises(ScorecardError, match="invalid"):
        report_scorecard(**_counts(decisions=-1))  # type: ignore[arg-type]
    with pytest.raises(ScorecardError, match="invalid"):
        report_scorecard(**_counts(stale_or_missing=-1))  # type: ignore[arg-type]
    with pytest.raises(ScorecardError, match="invalid"):
        report_scorecard(**_counts(decisions=True))  # type: ignore[arg-type]
    with pytest.raises(ScorecardError, match="list"):
        report_scorecard(**_counts(signals="row"))  # type: ignore[arg-type]
    with pytest.raises(ScorecardError, match="list"):
        report_scorecard(**_counts(signals=None))  # type: ignore[arg-type]
    with pytest.raises(ScorecardError, match="list"):
        report_scorecard(**_counts(trades=b"row"))  # type: ignore[arg-type]
    with pytest.raises(ScorecardError, match="stored records"):
        report_scorecard(**_counts(signals=(1,)))  # type: ignore[arg-type]
    with pytest.raises(ScorecardError, match="stored fields"):
        report_scorecard(**_counts(signals=({"decision_id": _DECISION},)))  # type: ignore[arg-type]
    with pytest.raises(ScorecardError, match="record"):
        report_scorecard(**_counts(portfolio=None))  # type: ignore[arg-type]
    with pytest.raises(ScorecardError, match="each comparison"):
        report_scorecard(**_counts(portfolio={"btc_dca_excess": None}))  # type: ignore[arg-type]
    with pytest.raises(ScorecardError, match="invalid"):
        report_scorecard(**_counts(portfolio=_portfolio(sharpe=1)))  # type: ignore[arg-type]


def test_a_copied_excess_or_an_empty_trade_report_is_refused() -> None:
    with pytest.raises(ScorecardError, match="invalid"):
        report_scorecard(**_counts(signals=(_signal(), _signal())))  # type: ignore[arg-type]
    clean = report_scorecard(**_counts(signals=(_signal(),)))  # type: ignore[arg-type]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        copied = clean.model_copy(
            update={
                "signal_quality": (
                    clean.signal_quality[0].model_copy(update={"excess_return": Decimal("1")}),
                )
            }
        )
    with pytest.raises(ScorecardError, match="invalid"):
        copied.to_document()
    with pytest.raises(ValidationError):
        Scorecard.model_validate(
            {
                **clean.model_dump(),
                "trade_status": "reported",
                "trade_quality": (),
            }
        )
    with pytest.raises(ValidationError):
        Scorecard.model_validate(
            {
                **clean.model_dump(),
                "trade_status": "no_trades",
                "trade_quality": (_trade(),),
            }
        )


def test_a_trade_or_a_signal_with_a_bad_shape_is_refused() -> None:
    with pytest.raises(ScorecardError, match="invalid"):
        report_scorecard(**_counts(signals=(_signal(horizon_days=5),)))  # type: ignore[arg-type]
    with pytest.raises(ScorecardError, match="invalid"):
        report_scorecard(**_counts(signals=(_signal(decision_id="Z" * 64),)))  # type: ignore[arg-type]
    with pytest.raises(ScorecardError, match="invalid"):
        report_scorecard(**_counts(signals=(_signal(rank=0),)))  # type: ignore[arg-type]
    with pytest.raises(ScorecardError, match="invalid"):
        report_scorecard(**_counts(signals=(_signal(rank=True),)))  # type: ignore[arg-type]
    with pytest.raises(ScorecardError, match="invalid"):
        report_scorecard(
            **_counts(signals=(_signal(absolute_return=Decimal("NaN")),))  # type: ignore[arg-type]
        )
    with pytest.raises(ScorecardError, match="invalid"):
        report_scorecard(**_counts(signals=(_signal(decision_id=1),)))  # type: ignore[arg-type]
    with pytest.raises(ScorecardError, match="invalid"):
        report_scorecard(
            **_counts(signals=(_signal(universe_relative_return=1),))  # type: ignore[arg-type]
        )
    with pytest.raises(ScorecardError, match="invalid"):
        report_scorecard(**_counts(trades=(_trade(mae=Decimal("0.01")),)))  # type: ignore[arg-type]
    with pytest.raises(ScorecardError, match="invalid"):
        report_scorecard(**_counts(trades=(_trade(mfe=Decimal("-0.01")),)))  # type: ignore[arg-type]
    with pytest.raises(ScorecardError, match="invalid"):
        report_scorecard(**_counts(trades=(_trade(fees_usd=Decimal("-0.01")),)))  # type: ignore[arg-type]
    with pytest.raises(ScorecardError, match="invalid"):
        report_scorecard(**_counts(trades=(_trade(slippage=Decimal("-0.01")),)))  # type: ignore[arg-type]
    with pytest.raises(ScorecardError, match="invalid"):
        report_scorecard(**_counts(trades=(_trade(decision_id="nope"),)))  # type: ignore[arg-type]
    reported = report_scorecard(**_counts(trades=(_trade(),)))  # type: ignore[arg-type]
    assert reported.trade_status == "reported"
    edges = report_scorecard(
        **_counts(signals=(_signal(coefficient=Decimal("1"), rank=2),))  # type: ignore[arg-type]
    )
    assert edges.signal_quality[0].coefficient == Decimal("1")
    floor = report_scorecard(
        **_counts(signals=(_signal(coefficient=Decimal("-1")),))  # type: ignore[arg-type]
    )
    assert floor.signal_quality[0].coefficient == Decimal("-1")
