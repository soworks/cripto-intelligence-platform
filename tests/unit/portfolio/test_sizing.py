from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from cip.domain.errors import SizeError
from cip.domain.policy import load_policy
from cip.portfolio.sizing import PositionSize, size_position

_POLICY_PATH = Path(__file__).parents[3] / "policies" / "investment-policy.yaml"
POLICY = load_policy(_POLICY_PATH)


def _size(**overrides: object) -> PositionSize:
    values: dict[str, object] = {
        "portfolio_usd": Decimal("10000"),
        "stop_distance_pct": Decimal("0.05"),
        "beta": Decimal("1"),
        "regime": "RISK_ON",
        "symbol_min_notional": Decimal("5"),
        "policy": POLICY,
    }
    values.update(overrides)
    return size_position(**values)  # type: ignore[arg-type]


def test_risk_stop_and_the_trade_cap_name_a_size_that_is_not_an_order() -> None:
    sized = _size()
    assert sized.reason == "sized"
    assert sized.size_usd == Decimal("75.00")
    assert sized.minimum_usd == Decimal("25.00")
    assert sized.size_usd != POLICY.policy.portfolio.starting_value_usd
    document = sized.to_document()
    assert set(document) == {"size_usd", "minimum_usd", "reason"}
    assert document["size_usd"] == "75.00"
    with pytest.raises(ValidationError):
        PositionSize.model_validate({**sized.model_dump(), "order_id": "1"})


def test_the_discovery_asset_cap_can_bind_before_the_trade_cap() -> None:
    sized = _size(portfolio_usd=Decimal("4000"))
    assert sized.size_usd == Decimal("40.00")
    assert sized.reason == "sized"


def test_neutral_and_a_high_beta_shrink_the_size() -> None:
    neutral = _size(regime="NEUTRAL")
    assert neutral.size_usd == Decimal("37.50")
    halved = _size(beta=Decimal("2"))
    assert halved.size_usd == Decimal("37.50")
    low_beta = _size(beta=Decimal("0.4"))
    assert low_beta.size_usd == Decimal("75.00")


def test_cents_round_down_and_do_not_lift_a_size_onto_the_floor() -> None:
    capped = _size(portfolio_usd=Decimal("3333.5"))
    assert capped.size_usd == Decimal("33.33")
    assert capped.reason == "sized"
    under = _size(portfolio_usd=Decimal("2499.9"))
    assert under.size_usd is None
    assert under.reason == "below_minimum"


def test_a_size_below_the_minimum_is_absent() -> None:
    small = _size(portfolio_usd=Decimal("4000"), regime="NEUTRAL")
    assert small.size_usd is None
    assert small.reason == "below_minimum"
    assert small.minimum_usd == Decimal("25.00")
    dusty = _size(beta=Decimal("4"))
    assert dusty.size_usd is None
    assert dusty.reason == "below_minimum"
    wider_floor = _size(symbol_min_notional=Decimal("20"))
    assert wider_floor.minimum_usd == Decimal("80.00")
    assert wider_floor.size_usd is None
    assert wider_floor.reason == "below_minimum"


def test_risk_off_and_a_missing_regime_do_not_invent_a_size() -> None:
    off = _size(regime="RISK_OFF")
    assert off.size_usd is None
    assert off.reason == "risk_off"
    missing = _size(regime=None)
    assert missing.size_usd is None
    assert missing.reason == "no_regime"


def test_bad_inputs_are_refused() -> None:
    with pytest.raises(SizeError, match="positive"):
        _size(portfolio_usd=Decimal("0"))
    with pytest.raises(SizeError, match="fraction"):
        _size(stop_distance_pct=Decimal("0"))
    with pytest.raises(SizeError, match="fraction"):
        _size(stop_distance_pct=Decimal("1.1"))
    with pytest.raises(SizeError, match="regime"):
        _size(regime="OPEN")
    with pytest.raises(SizeError, match="regime"):
        _size(regime=1)  # type: ignore[arg-type]
    assert _size(portfolio_usd="10000").size_usd == Decimal("75.00")  # type: ignore[arg-type]
    with pytest.raises(SizeError, match="decimal"):
        _size(portfolio_usd="nope")  # type: ignore[arg-type]
    with pytest.raises(SizeError, match="decimal"):
        _size(portfolio_usd=object())  # type: ignore[arg-type]
    with pytest.raises(SizeError, match="positive"):
        _size(symbol_min_notional=Decimal("-1"))
    with pytest.raises(ValidationError):
        PositionSize.model_validate({"size_usd": "75.00", "minimum_usd": "nope", "reason": "sized"})
    with pytest.raises(ValidationError):
        PositionSize.model_validate({"size_usd": object(), "minimum_usd": "25", "reason": "sized"})
    with pytest.raises(ValidationError):
        PositionSize.model_validate({"size_usd": True, "minimum_usd": "25.00", "reason": "sized"})
    with pytest.raises(ValidationError):
        PositionSize.model_validate({"size_usd": "NaN", "minimum_usd": "25.00", "reason": "sized"})
    assert PositionSize.model_validate(
        {"size_usd": "75.00", "minimum_usd": "25.00", "reason": "sized"}
    ).size_usd == Decimal("75.00")
    with pytest.raises(SizeError, match="decimal"):
        _size(beta=1)  # type: ignore[arg-type]
    with pytest.raises(SizeError, match="finite"):
        _size(beta=Decimal("NaN"))
    with pytest.raises(ValidationError):
        PositionSize(size_usd=Decimal("0"), minimum_usd=Decimal("25"), reason="sized")
    with pytest.raises(ValidationError):
        PositionSize(size_usd=Decimal("25"), minimum_usd=Decimal("25"), reason="below_minimum")
