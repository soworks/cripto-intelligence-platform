from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from cip.domain.errors import LimitError
from cip.domain.policy import load_policy
from cip.portfolio.limits import DiscoveryLine, LimitDecision, admit_entry

_POLICY_PATH = Path(__file__).parents[3] / "policies" / "investment-policy.yaml"
POLICY = load_policy(_POLICY_PATH)


def _line(symbol: str, sector: str, beta: str = "1", value: str = "100") -> DiscoveryLine:
    return DiscoveryLine(symbol=symbol, sector=sector, beta=Decimal(beta), value_usd=Decimal(value))


def _admit(**overrides: object) -> LimitDecision:
    values: dict[str, object] = {
        "portfolio_usd": Decimal("10000"),
        "symbol": "SOLUSDT",
        "sector": "L1",
        "beta": Decimal("1"),
        "size_usd": Decimal("50"),
        "positions": (),
        "drawdown": Decimal("0"),
        "consecutive_losses": 0,
        "days_since_last_loss": None,
        "monthly_realized_loss_pct": Decimal("0"),
        "days_since_symbol_exit": None,
        "policy": POLICY,
    }
    values.update(overrides)
    return admit_entry(**values)  # type: ignore[arg-type]


def test_a_clear_book_admits_the_entry() -> None:
    decision = _admit()
    assert decision.allowed is True
    assert decision.flatten is False
    assert decision.reasons == ()
    assert set(decision.to_document()) == {"allowed", "flatten", "reasons"}
    with pytest.raises(ValidationError):
        LimitDecision.model_validate({**decision.model_dump(), "order_id": "1"})


def test_five_open_positions_block_another() -> None:
    four = tuple(_line(f"A{index}USDT", f"S{index}") for index in range(4))
    assert _admit(positions=four).allowed is True
    decision = _admit(positions=(*four, _line("EUSDT", "S4")))
    assert decision.allowed is False
    assert decision.reasons == ("open_position_cap",)


def test_two_names_in_a_sector_block_a_third() -> None:
    one = (_line("ADAUSDT", "L1"),)
    assert _admit(positions=one, sector="L1").allowed is True
    decision = _admit(positions=(*one, _line("AVAXUSDT", "L1")), sector="L1")
    assert decision.reasons == ("sector_cap",)


def test_beta_weighted_exposure_blocks_only_above_the_cap() -> None:
    held = _admit(positions=(_line("ADAUSDT", "L1", value="1000"),), size_usd=Decimal("200"))
    assert held.allowed is True
    blocked = _admit(positions=(_line("ADAUSDT", "L1", value="1000"),), size_usd=Decimal("250"))
    assert blocked.reasons == ("beta_exposure",)
    reduced = _admit(
        positions=(_line("ADAUSDT", "L1", value="1300"),),
        beta=Decimal("-1"),
        size_usd=Decimal("200"),
    )
    assert reduced.allowed is True


def test_drawdown_halts_and_then_asks_for_a_review() -> None:
    assert _admit(drawdown=Decimal("0.19")).allowed is True
    halted = _admit(drawdown=Decimal("0.20"))
    assert halted.allowed is False
    assert halted.flatten is False
    assert halted.reasons == ("halt_new_entries",)
    flat = _admit(drawdown=Decimal("0.30"))
    assert flat.flatten is True
    assert flat.reasons == ("review_and_flatten", "halt_new_entries")


def test_three_losses_pause_for_fourteen_days() -> None:
    paused = _admit(consecutive_losses=3, days_since_last_loss=13)
    assert paused.reasons == ("loss_streak",)
    assert _admit(consecutive_losses=3, days_since_last_loss=14).allowed is True
    assert _admit(consecutive_losses=3, days_since_last_loss=None).reasons == ("loss_streak",)
    assert _admit(consecutive_losses=2, days_since_last_loss=0).allowed is True


def test_a_three_percent_month_and_a_recent_exit_block_the_name() -> None:
    assert _admit(monthly_realized_loss_pct=Decimal("0.03")).reasons == ("monthly_loss",)
    assert _admit(monthly_realized_loss_pct=Decimal("0.029")).allowed is True
    held = _admit(positions=(_line("SOLUSDT", "L1"),))
    assert held.reasons == ("averaging_down",)
    assert _admit(days_since_symbol_exit=9).reasons == ("reentry_cooldown",)
    assert _admit(days_since_symbol_exit=10).allowed is True
    assert _admit(size_usd=None).reasons == ("no_size",)


def test_several_blocks_stay_in_policy_order() -> None:
    positions = (*tuple(_line(f"A{index}USDT", "L1") for index in range(4)), _line("SOLUSDT", "L1"))
    decision = _admit(
        positions=positions,
        sector="L1",
        drawdown=Decimal("0.30"),
        consecutive_losses=3,
        days_since_last_loss=0,
        monthly_realized_loss_pct=Decimal("0.03"),
        days_since_symbol_exit=1,
        size_usd=None,
    )
    assert decision.reasons == (
        "review_and_flatten",
        "halt_new_entries",
        "loss_streak",
        "monthly_loss",
        "open_position_cap",
        "sector_cap",
        "averaging_down",
        "reentry_cooldown",
        "no_size",
    )


def test_bad_inputs_are_refused() -> None:
    with pytest.raises(LimitError, match="positive"):
        _admit(portfolio_usd=Decimal("0"))
    with pytest.raises(LimitError, match="drawdown"):
        _admit(drawdown=Decimal("-0.01"))
    with pytest.raises(LimitError, match="decimal"):
        _admit(drawdown=0.2)  # type: ignore[arg-type]
    with pytest.raises(LimitError, match="duplicate"):
        _admit(positions=(_line("ADAUSDT", "L1"), _line("ADAUSDT", "L1")))
    with pytest.raises(LimitError, match="days"):
        _admit(consecutive_losses=True)  # type: ignore[arg-type]
    with pytest.raises(LimitError, match="days"):
        _admit(days_since_last_loss=-1)
    with pytest.raises(LimitError, match="monthly"):
        _admit(monthly_realized_loss_pct=Decimal("-0.01"))
    with pytest.raises(LimitError, match="decimal"):
        _admit(beta=Decimal("NaN"))
    with pytest.raises(LimitError, match="positive"):
        _admit(size_usd=Decimal("0"))
    with pytest.raises(LimitError, match="symbol"):
        _admit(symbol="sol")
    with pytest.raises(LimitError, match="symbol"):
        _admit(symbol=1)  # type: ignore[arg-type]
    with pytest.raises(LimitError, match="sector"):
        _admit(sector="")
    with pytest.raises(LimitError, match="sector"):
        _admit(sector=" L1")
    with pytest.raises(LimitError, match="sector"):
        _admit(sector=1)  # type: ignore[arg-type]
    with pytest.raises(LimitError, match="tuple"):
        _admit(positions=[])  # type: ignore[arg-type]
    with pytest.raises(LimitError, match="discovery lines"):
        _admit(positions=("ADAUSDT",))  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        LimitDecision(allowed=True, flatten=False, reasons=("halt_new_entries",))
    with pytest.raises(ValidationError):
        LimitDecision(allowed=False, flatten=True, reasons=("halt_new_entries",))
    with pytest.raises(ValidationError):
        LimitDecision(allowed=False, flatten=False, reasons=())
