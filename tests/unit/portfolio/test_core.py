import warnings
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from cip.backtest.schedule import allocate
from cip.domain.errors import CoreError
from cip.domain.policy import load_policy
from cip.portfolio.core import CoreWeek, plan_core_week

_POLICY_PATH = Path(__file__).parents[3] / "policies" / "investment-policy.yaml"
POLICY = load_policy(_POLICY_PATH)
_KEYS = {"week_index", "week_count", "btc_usd", "eth_usd", "reserve_deployed_usd"}


def _week(**overrides: object) -> CoreWeek:
    values: dict[str, object] = {"week_index": 1, "week_count": 4, "policy": POLICY}
    values.update(overrides)
    return plan_core_week(**values)  # type: ignore[arg-type]


def test_four_weeks_split_the_core_budget_and_leave_the_reserve_in_usdc() -> None:
    planned = _week()
    assert planned.btc_usd == Decimal("98.00")
    assert planned.eth_usd == Decimal("42.00")
    assert planned.reserve_deployed_usd == Decimal("0.00")
    document = planned.to_document()
    assert set(document) == _KEYS
    assert document["btc_usd"] == "98.00"
    assert document["eth_usd"] == "42.00"
    assert document["reserve_deployed_usd"] == "0.00"
    assert "quantity" not in document
    with pytest.raises(ValidationError):
        CoreWeek.model_validate({**planned.model_dump(), "order_id": "1"})

    portfolio = POLICY.policy.portfolio
    btc = Decimal(0)
    eth = Decimal(0)
    for index in range(1, 5):
        week = _week(week_index=index)
        btc += week.btc_usd
        eth += week.eth_usd
        assert week.reserve_deployed_usd == 0
    assert btc == portfolio.core_btc_monthly_usd == Decimal("392.00")
    assert eth == portfolio.core_eth_monthly_usd == Decimal("168.00")
    assert btc + eth == portfolio.core_monthly_usd
    assert btc != portfolio.starting_value_usd


def test_the_week_uses_the_dca_allocator_on_the_core_budget_only() -> None:
    mix = {
        symbol: Decimal(str(weight)) for symbol, weight in POLICY.policy.portfolio.core_mix.items()
    }
    legs = allocate(Decimal("140.00"), mix)
    planned = _week(week_index=2, week_count=4)
    assert planned.btc_usd == legs["BTCUSDT"]
    assert planned.eth_usd == legs["ETHUSDT"]
    assert planned.btc_usd + planned.eth_usd == Decimal("140.00")
    assert "discovery" not in planned.to_document()


def test_one_week_deploys_the_whole_core_month() -> None:
    planned = _week(week_index=1, week_count=1)
    assert planned.btc_usd == Decimal("392.00")
    assert planned.eth_usd == Decimal("168.00")


def test_the_last_week_keeps_the_residual_cent() -> None:
    first = _week(week_index=1, week_count=3)
    last = _week(week_index=3, week_count=3)
    assert first.btc_usd == Decimal("130.67")
    assert first.eth_usd == Decimal("56.00")
    assert last.btc_usd == Decimal("130.66")
    assert last.eth_usd == Decimal("56.00")
    weeks = [_week(week_index=index, week_count=3) for index in range(1, 4)]
    total_btc = sum((week.btc_usd for week in weeks), Decimal(0))
    total_eth = sum((week.eth_usd for week in weeks), Decimal(0))
    assert total_btc == Decimal("392.00")
    assert total_eth == Decimal("168.00")


def test_a_week_outside_the_month_is_refused() -> None:
    with pytest.raises(CoreError, match="at least one week"):
        _week(week_count=0)
    with pytest.raises(CoreError, match="inside the month"):
        _week(week_index=0)
    with pytest.raises(CoreError, match="inside the month"):
        _week(week_index=5)
    with pytest.raises(CoreError, match="integer"):
        _week(week_index=True)
    with pytest.raises(CoreError, match="integer"):
        _week(week_count="4")


def test_a_share_that_rounds_away_is_refused() -> None:
    with pytest.raises(CoreError, match="positive"):
        _week(week_count=10**30)
    with pytest.raises(CoreError, match="positive"):
        _week(week_count=56001)


def _raw(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "week_index": 1,
        "week_count": 4,
        "btc_usd": Decimal("98.00"),
        "eth_usd": Decimal("42.00"),
        "reserve_deployed_usd": Decimal("0.00"),
        "core_usd": Decimal("140.00"),
    }
    values.update(overrides)
    return values


def test_a_document_that_is_not_a_core_week_is_refused() -> None:
    with pytest.raises(ValidationError):
        CoreWeek.model_validate(_raw(week_index=True))
    with pytest.raises(ValidationError):
        CoreWeek.model_validate(_raw(btc_usd=1))
    with pytest.raises(ValidationError):
        CoreWeek.model_validate(_raw(eth_usd=Decimal("NaN")))
    with pytest.raises(ValidationError):
        CoreWeek.model_validate(_raw(week_count=0))
    with pytest.raises(ValidationError):
        CoreWeek.model_validate(_raw(week_index=0))
    with pytest.raises(ValidationError):
        CoreWeek.model_validate(_raw(week_index=5))
    with pytest.raises(ValidationError):
        CoreWeek.model_validate(_raw(btc_usd=Decimal("0.00"), core_usd=Decimal("42.00")))
    with pytest.raises(ValidationError):
        CoreWeek.model_validate(_raw(eth_usd=Decimal("0.00"), core_usd=Decimal("98.00")))
    with pytest.raises(ValidationError):
        CoreWeek.model_validate(_raw(core_usd=Decimal("0.00")))


def test_a_copied_reserve_deployment_is_refused() -> None:
    planned = _week()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        copied = planned.model_copy(update={"reserve_deployed_usd": Decimal("80.00")})
    with pytest.raises(CoreError, match="invalid"):
        copied.to_document()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        drifted = planned.model_copy(update={"btc_usd": Decimal("99.00")})
    with pytest.raises(CoreError, match="invalid"):
        drifted.to_document()
