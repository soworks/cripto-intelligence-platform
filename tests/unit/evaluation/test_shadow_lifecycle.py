import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from cip.domain.errors import DuplicateEventError, EvaluationError
from cip.domain.policy import load_policy
from cip.evaluation.decision import (
    Cohort,
    DecisionRecord,
    Disposition,
    SourceStamp,
    decision_id,
)
from cip.evaluation.outcomes import record_forward
from cip.evaluation.scorecard import report_scorecard
from cip.evaluation.store import append_decision, append_outcome
from cip.evaluation.weekly import write_weekly_scorecard
from cip.history.bars import DailyBar
from cip.persistence.ledger import LedgerRepository
from cip.portfolio.exits import name_exit
from cip.portfolio.fills import shadow_fill
from cip.portfolio.limits import admit_entry
from cip.portfolio.position import PositionState, advance, propose, transition_event
from cip.portfolio.sizing import size_position
from cip.portfolio.symbol_filter import screen_symbol

POLICY = load_policy(Path(__file__).parents[3] / "policies" / "investment-policy.yaml")
CLOSE = datetime(2026, 10, 4, tzinfo=UTC)
SHA = "a" * 40
ENTRY = date(2026, 10, 3)
END = date(2026, 10, 10)


def _filters() -> list[dict[str, object]]:
    return [
        {
            "filterType": "PRICE_FILTER",
            "minPrice": "0.01",
            "maxPrice": "100000",
            "tickSize": "0.01",
        },
        {"filterType": "LOT_SIZE", "minQty": "0.01", "maxQty": "100000", "stepSize": "0.01"},
        {"filterType": "MARKET_LOT_SIZE", "minQty": "0.00", "maxQty": "100000", "stepSize": "0"},
        {"filterType": "NOTIONAL", "minNotional": "5", "maxNotional": "1000000"},
        {
            "filterType": "PERCENT_PRICE_BY_SIDE",
            "bidMultiplierUp": "1.2",
            "bidMultiplierDown": "0.5",
            "askMultiplierUp": "2",
            "askMultiplierDown": "0.8",
        },
        {"filterType": "MAX_NUM_ALGO_ORDERS", "maxNumAlgoOrders": 5},
    ]


def _bar(symbol: str, day: date, close: str) -> DailyBar:
    price = Decimal(close)
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


def _span(symbol: str, close: str, end_close: str) -> list[DailyBar]:
    bars: list[DailyBar] = []
    day = ENTRY
    while day <= END:
        price = end_close if day == END else close
        bars.append(_bar(symbol, day, price))
        day += timedelta(days=1)
    return bars


def _record() -> DecisionRecord:
    return DecisionRecord(
        cohort=Cohort.SHADOW,
        symbol="SOLUSDT",
        evaluated_at=CLOSE,
        policy_version=POLICY.version,
        git_sha=SHA,
        disposition=Disposition.BUY,
        reason_codes=("shadow_lifecycle",),
        features={"circulating_ratio": Decimal("1")},
        score=Decimal("80"),
        score_components={"tokenomics": Decimal("80")},
        rank=1,
        regime="RISK_ON",
        raw_regime="RISK_ON",
        sources=(
            SourceStamp(name="daily_bars", observed_at=CLOSE, provenance="stored_daily_bars"),
        ),
    )


def _once(ledger: LedgerRepository, event: object) -> None:
    from cip.domain.events import LedgerEvent

    assert isinstance(event, LedgerEvent)
    ledger.append(event)
    with pytest.raises(DuplicateEventError):
        ledger.append(event)


def test_one_shadow_trade_traces_from_decision_to_assurance(
    tmp_path: Path, ledger_table: Any
) -> None:
    ledger = LedgerRepository(ledger_table)
    record = _record()
    identity = decision_id(record)
    assert append_decision(tmp_path, record) is True
    assert append_decision(tmp_path, record) is False
    changed = record.model_copy(update={"reason_codes": ("changed_reason",)})
    with pytest.raises(EvaluationError, match="different payload"):
        append_decision(tmp_path, changed)

    proposed = propose(
        decision_id=identity,
        symbol=record.symbol,
        cohort=Cohort.SHADOW,
        policy_version=POLICY.version,
        git_sha=SHA,
        updated_at=CLOSE,
        reason="recorded",
    )
    _once(ledger, transition_event(None, proposed))
    sized = size_position(
        portfolio_usd=Decimal("10000"),
        stop_distance_pct=Decimal("0.05"),
        beta=Decimal("1"),
        regime="RISK_ON",
        symbol_min_notional=Decimal("5"),
        policy=POLICY,
    )
    assert sized.size_usd == Decimal("75.00")
    admitted = admit_entry(
        portfolio_usd=Decimal("10000"),
        symbol="SOLUSDT",
        sector="L1",
        beta=Decimal("1"),
        size_usd=sized.size_usd,
        positions=(),
        drawdown=Decimal("0"),
        consecutive_losses=0,
        days_since_last_loss=None,
        monthly_realized_loss_pct=Decimal("0"),
        days_since_symbol_exit=None,
        policy=POLICY,
    )
    assert admitted.allowed is True
    screen = screen_symbol(
        info={
            "symbol": "SOLUSDT",
            "status": "TRADING",
            "quoteAsset": "USDT",
            "isSpotTradingAllowed": True,
            "orderTypes": ["LIMIT", "LIMIT_MAKER", "MARKET", "STOP_LOSS_LIMIT"],
            "filters": _filters(),
        },
        price=Decimal("10"),
        exit_price=Decimal("9"),
        size_usd=sized.size_usd or Decimal("0"),
        reference_price=Decimal("10"),
        policy=POLICY,
    )
    assert screen.passed is True
    assert screen.quantity is not None

    approved = advance(
        proposed,
        to=PositionState.APPROVED,
        reason="admitted",
        updated_at=CLOSE + timedelta(seconds=1),
    )
    pending = advance(
        approved,
        to=PositionState.ENTRY_PENDING,
        reason="screened",
        updated_at=CLOSE + timedelta(seconds=2),
    )
    entry = shadow_fill(
        side="buy",
        next_open=Decimal("10"),
        quantity=screen.quantity,
        filled_quantity=None,
        spread=Decimal("0.001"),
        slippage=Decimal("0.001"),
        buy_spent_today_usd=Decimal("0"),
        buy_spent_month_usd=Decimal("0"),
        policy=POLICY,
    )
    opened = advance(
        pending,
        to=PositionState.OPEN,
        reason="shadow_entry",
        updated_at=CLOSE + timedelta(seconds=3),
    )
    named = name_exit(
        state=opened.state,
        entry_price=Decimal("10"),
        mark_price=Decimal("10"),
        highest_price=Decimal("10"),
        atr=Decimal("0.01"),
        days_held=56,
        return_vs_btc=Decimal("0"),
        observed_triggers=frozenset(),
        partial_already_taken=False,
        exits=POLICY.policy.hypotheses.exits,
    )
    assert named is not None
    assert named.reason == "max_holding"
    exiting = advance(
        opened,
        to=PositionState.EXIT_PENDING,
        reason=named.reason,
        updated_at=CLOSE + timedelta(seconds=4),
    )
    exit_fill = shadow_fill(
        side="sell",
        next_open=Decimal("10"),
        quantity=screen.quantity,
        filled_quantity=None,
        spread=Decimal("0.001"),
        slippage=Decimal("0.001"),
        buy_spent_today_usd=Decimal("150"),
        buy_spent_month_usd=Decimal("750"),
        policy=POLICY,
    )
    closed = advance(
        exiting,
        to=PositionState.CLOSED,
        reason="shadow_exit",
        updated_at=CLOSE + timedelta(seconds=5),
    )
    for before, after in (
        (proposed, approved),
        (approved, pending),
        (pending, opened),
        (opened, exiting),
        (exiting, closed),
    ):
        _once(ledger, transition_event(before, after))

    outcome = record_forward(
        tmp_path,
        record,
        horizon_days=7,
        as_of=datetime(2026, 10, 11, tzinfo=UTC),
        bars=_span("SOLUSDT", "100", "110"),
        btc_bars=_span("BTCUSDT", "200", "220"),
    )
    assert append_outcome(tmp_path, outcome, as_of=datetime(2026, 10, 11, tzinfo=UTC)) is False
    assert entry.reason == "filled"
    assert exit_fill.reason == "filled"
    assert exit_fill.budget_usd == 0
    assert entry.fill_price is not None
    assert exit_fill.fill_price is not None
    risk = entry.fill_price * Decimal("0.05")
    card = report_scorecard(
        decisions=1,
        reproducible=1,
        policy_violations=0,
        stale_or_missing=0,
        replay_disagreements=0,
        invalid_decisions=0,
        signals=[
            {
                "decision_id": identity,
                "horizon_days": outcome.horizon_days,
                "absolute_return": outcome.absolute_return,
                "btc_return": outcome.btc_return,
                "excess_return": outcome.excess_return,
                "universe_relative_return": outcome.universe_relative_return,
                "rank": 1,
                "coefficient": None,
            }
        ],
        trades=[
            {
                "decision_id": identity,
                "realized_r": (exit_fill.fill_price - entry.fill_price) / risk,
                "mae": outcome.mae,
                "mfe": outcome.mfe,
                "fees_usd": entry.fee_drag_usd,
                "slippage": Decimal("0.001"),
            }
        ],
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
    week = date(2026, 10, 11)
    assert write_weekly_scorecard(tmp_path, week, card) is True
    assert write_weekly_scorecard(tmp_path, week, card) is False

    events = ledger.list_by_correlation(proposed.position_id)
    states = [event.payload["to_state"] for event in events]
    assert states == ["PROPOSED", "APPROVED", "ENTRY_PENDING", "OPEN", "EXIT_PENDING", "CLOSED"]
    assert all(event.payload["decision_id"] == identity for event in events)
    assert all(event.policy_version == POLICY.version for event in events)
    published = card.to_document()
    blob = json.dumps(
        {
            "decision": record.to_document(),
            "entry": entry.to_document(),
            "exit_rule": named.to_document(),
            "exit": exit_fill.to_document(),
            "outcome": outcome.to_document(),
            "scorecard": published,
        }
    )
    assert "order_id" not in blob
    assert published["trade_quality"]["trades"][0]["decision_id"] == identity
    assert published["signal_quality"][0]["decision_id"] == identity
    assert published["signal_quality"][0]["excess_return"] == format(
        outcome.absolute_return - outcome.btc_return, "f"
    )
