from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from cip.domain.errors import EvaluationError
from cip.domain.policy import RegimeHypotheses, load_policy
from cip.evaluation.regime import PriorSession, RegimeDecision, classify
from cip.history.bars import DailyBar
from cip.recorders.observation import Observation

_POLICY = Path(__file__).parents[3] / "policies" / "investment-policy.yaml"
REGIME = load_policy(_POLICY).policy.hypotheses.regime
AS_OF = date(2026, 10, 4)


def _bar(symbol: str, day: date, close: str, high: str | None = None) -> DailyBar:
    price = Decimal(close)
    peak = Decimal(high) if high is not None else price
    return DailyBar(
        symbol=symbol,
        open_date=day,
        open=price,
        high=peak,
        low=price,
        close=price,
        volume=Decimal("1"),
        quote_volume=Decimal("1"),
        trade_count=1,
        taker_buy_base_volume=Decimal("1"),
        taker_buy_quote_volume=Decimal("1"),
    )


def _series(
    symbol: str,
    days: int,
    close: str = "100",
    *,
    last_close: str | None = None,
    high: str | None = None,
    as_of: date = AS_OF,
) -> tuple[DailyBar, ...]:
    bars: list[DailyBar] = []
    for offset in range(days - 1, -1, -1):
        day = as_of - timedelta(days=offset)
        price = last_close if offset == 0 and last_close is not None else close
        bars.append(_bar(symbol, day, price, high))
    return tuple(bars)


def _observation(series: str, day: date, value: str = "1") -> Observation:
    name = "btc_dominance" if series == "btc_dominance" else "stablecoin_supply_usd"
    unit = "percent" if series == "btc_dominance" else "usd"
    provider = "coingecko" if series == "btc_dominance" else "defillama"
    moment = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return Observation(
        series=series,
        provider=provider,
        source_timestamp=moment,
        observed_at=moment,
        symbol=None,
        values=((name, Decimal(value)),),
        units=((name, unit),),
    )


def _observations(day: date = AS_OF, dominance: str = "1") -> tuple[Observation, ...]:
    return (
        _observation("btc_dominance", day, dominance),
        _observation("stablecoin_supply", day, "100"),
    )


def _universe(breadth: str) -> dict[str, tuple[DailyBar, ...]]:
    if breadth == "wide":
        return {
            "AAAUSDT": _series("AAAUSDT", 60, last_close="101"),
            "BBBUSDT": _series("BBBUSDT", 60, last_close="101"),
        }
    if breadth == "half":
        return {
            "AAAUSDT": _series("AAAUSDT", 60, last_close="101"),
            "BBBUSDT": _series("BBBUSDT", 60),
        }
    if breadth == "narrow":
        return {"AAAUSDT": _series("AAAUSDT", 60)}
    if breadth == "middle":
        return {
            "AAAUSDT": _series("AAAUSDT", 60, last_close="101"),
            "BBBUSDT": _series("BBBUSDT", 60),
            "CCCUSDT": _series("CCCUSDT", 60),
        }
    raise AssertionError(breadth)


def _classify(**overrides: object) -> RegimeDecision:
    values: dict[str, object] = {
        "as_of": AS_OF,
        "btc_bars": _series("BTCUSDT", 200, last_close="101"),
        "universe_bars": _universe("wide"),
        "observations": _observations(),
        "prior": (),
        "hypotheses": REGIME,
    }
    values.update(overrides)
    return classify(**values)  # type: ignore[arg-type]


def _priors(raw: str, published: str, days: int) -> tuple[PriorSession, ...]:
    return tuple(
        PriorSession(session=AS_OF - timedelta(days=offset), raw=raw, published=published)
        for offset in range(1, days + 1)
    )


def test_risk_on_uses_the_discovery_policy_and_ignores_observation_magnitude() -> None:
    held = _priors("RISK_ON", "RISK_ON", 1)
    first = _classify(prior=held)
    other = _classify(prior=held, observations=_observations(dominance="90"))
    assert first.regime == "RISK_ON"
    assert first.reason_codes == ("risk_on",)
    assert first.new_entries is True
    assert first.min_score == REGIME.risk_on.min_score
    assert first.size_mult == Decimal(str(REGIME.risk_on.size_mult))
    assert first.require_rs_vs_btc_30d_positive is None
    assert first.trailing_atr_mult is None
    assert other.regime == first.regime
    assert other.reason_codes == first.reason_codes


def test_missing_or_stale_regime_observations_reject() -> None:
    dominance = _observation("btc_dominance", AS_OF)
    supply = _observation("stablecoin_supply", AS_OF)
    assert "missing_btc_dominance" in _classify(observations=(supply,)).reason_codes
    assert "missing_stablecoin_supply" in _classify(observations=(dominance,)).reason_codes
    yesterday = _observations(AS_OF - timedelta(days=1))
    stale = _classify(observations=yesterday)
    assert "stale_btc_dominance" in stale.reason_codes
    assert "stale_stablecoin_supply" in stale.reason_codes
    future = _observations(AS_OF + timedelta(days=1))
    assert "missing_btc_dominance" in _classify(observations=future).reason_codes
    assert _classify(observations=()).new_entries is False


def test_btc_history_must_cover_the_session_without_a_gap() -> None:
    short = _classify(btc_bars=_series("BTCUSDT", 10))
    assert "missing_btc_sma" in short.reason_codes
    assert "missing_btc_drawdown" in short.reason_codes
    hole = AS_OF - timedelta(days=100)
    gapped = tuple(
        bar for bar in _series("BTCUSDT", 201, last_close="101") if bar.open_date != hole
    )
    gap = _classify(btc_bars=gapped)
    ended = _series("BTCUSDT", 200, as_of=AS_OF - timedelta(days=1))
    assert "missing_btc_sma" in _classify(btc_bars=ended).reason_codes
    assert "missing_btc_sma" in gap.reason_codes
    assert "missing_btc_drawdown" not in gap.reason_codes
    late = _series("BTCUSDT", 200, last_close="101", as_of=AS_OF + timedelta(days=1))
    assert "missing_btc_sma" in _classify(btc_bars=late).reason_codes


def test_drawdown_above_the_policy_forces_risk_off_and_equality_does_not() -> None:
    above = _series("BTCUSDT", 200, close="1", last_close="80", high="100")
    at_limit = _classify(btc_bars=above, prior=_priors("RISK_ON", "RISK_ON", 1))
    assert at_limit.regime == "RISK_ON"
    forced = _series("BTCUSDT", 200, close="1", last_close="79", high="100")
    risk_off = _classify(btc_bars=forced)
    assert risk_off.regime == "RISK_OFF"
    assert risk_off.new_entries is False
    assert risk_off.reason_codes == ("risk_off",)
    assert risk_off.trailing_atr_mult == Decimal(str(REGIME.risk_off.trailing_atr_mult))
    assert risk_off.size_mult is None
    assert risk_off.min_score is None


def test_neutral_is_exactly_one_risk_on_clause() -> None:
    below = _series("BTCUSDT", 200, last_close="99")
    held = _priors("NEUTRAL", "NEUTRAL", 1)
    neutral = _classify(btc_bars=below, universe_bars=_universe("wide"), prior=held)
    assert neutral.regime == "NEUTRAL"
    assert neutral.new_entries is True
    assert neutral.min_score == REGIME.neutral.min_score
    assert neutral.require_rs_vs_btc_30d_positive is True
    assert neutral.size_mult == Decimal(str(REGIME.neutral.size_mult))
    both_false = _classify(
        btc_bars=below,
        universe_bars=_universe("middle"),
        prior=held,
    )
    assert both_false.regime is None
    assert both_false.reason_codes == ("regime_unclassified",)
    assert both_false.new_entries is False


def test_price_and_breadth_fail_closed() -> None:
    flat = _series("BTCUSDT", 200)
    assert _classify(btc_bars=flat, prior=_priors("RISK_ON", "RISK_ON", 1)).regime == "NEUTRAL"
    flat_narrow = _classify(btc_bars=flat, universe_bars=_universe("narrow"))
    assert flat_narrow.regime is None
    assert flat_narrow.raw is None
    assert flat_narrow.reason_codes == ("regime_unclassified",)
    assert flat_narrow.new_entries is False
    narrow = _classify(
        btc_bars=_series("BTCUSDT", 200, last_close="99"),
        universe_bars=_universe("narrow"),
    )
    assert narrow.regime == "RISK_OFF"
    zero = list(_series("BTCUSDT", 200, last_close="101"))
    zero[-1] = _bar("BTCUSDT", AS_OF, "0")
    assert "invalid_btc_price" in _classify(btc_bars=tuple(zero)).reason_codes
    empty = _classify(universe_bars={}, prior=_priors("RISK_ON", "RISK_ON", 1))
    assert empty.reason_codes == ("missing_breadth",)
    short_name = _series("AAAUSDT", 10)
    full = _series("BBBUSDT", 60, last_close="101")
    breadth = _classify(
        universe_bars={"AAAUSDT": short_name, "BBBUSDT": full},
        prior=_priors("RISK_ON", "RISK_ON", 1),
    )
    assert breadth.regime == "RISK_ON"
    broken = list(_series("AAAUSDT", 50))
    broken[-1] = _bar("AAAUSDT", AS_OF, "0")
    invalid = _classify(universe_bars={"AAAUSDT": tuple(broken)})
    assert invalid.reason_codes == ("invalid_breadth",)


def test_hysteresis_blocks_a_looser_regime_and_accepts_a_tighter_one() -> None:
    opening = _classify()
    assert opening.regime is None
    assert opening.raw == "RISK_ON"
    assert opening.reason_codes == ("hysteresis",)
    assert opening.new_entries is False
    confirmed = _classify(prior=_priors("RISK_ON", "RISK_OFF", REGIME.hysteresis_days - 1))
    assert confirmed.regime == "RISK_ON"
    tightened = _classify(
        btc_bars=_series("BTCUSDT", 200, last_close="99"),
        universe_bars=_universe("middle"),
        prior=_priors("RISK_ON", "RISK_ON", 1),
    )
    assert tightened.regime is None
    assert tightened.reason_codes == ("regime_unclassified",)
    held = _classify(
        btc_bars=_series("BTCUSDT", 200, last_close="99"),
        prior=_priors("NEUTRAL", "RISK_OFF", 1),
    )
    assert held.regime == "RISK_OFF"
    assert held.raw == "NEUTRAL"
    assert "hysteresis" in held.reason_codes
    assert held.new_entries is False
    released = _classify(
        btc_bars=_series("BTCUSDT", 200, last_close="99"),
        prior=_priors("NEUTRAL", "RISK_OFF", REGIME.hysteresis_days - 1),
    )
    assert released.regime == "NEUTRAL"
    one_day = REGIME.model_copy(update={"hysteresis_days": 1})
    assert _classify(hypotheses=one_day).regime == "RISK_ON"


def test_inputs_cannot_look_ahead_or_contradict_the_symbol() -> None:
    with pytest.raises(EvaluationError):
        _classify(btc_bars=_series("ETHUSDT", 200, last_close="101"))
    with pytest.raises(EvaluationError):
        _classify(prior=(PriorSession(session=AS_OF, raw="RISK_ON", published="RISK_ON"),))
    with pytest.raises(EvaluationError):
        _classify(
            prior=(
                PriorSession(
                    session=AS_OF + timedelta(days=1),
                    raw="RISK_ON",
                    published="RISK_ON",
                ),
            )
        )
    duplicate = PriorSession(session=AS_OF - timedelta(days=1), raw="RISK_ON", published="RISK_ON")
    with pytest.raises(EvaluationError):
        _classify(prior=(duplicate, duplicate))
    with pytest.raises(ValidationError):
        RegimeDecision(
            regime="RISK_OFF",
            raw="RISK_OFF",
            reason_codes=("risk_off",),
            new_entries=True,
            size_mult=None,
            min_score=None,
            require_rs_vs_btc_30d_positive=None,
            trailing_atr_mult=Decimal("1.5"),
        )
    with pytest.raises(ValidationError):
        RegimeDecision(
            regime=None,
            raw=None,
            reason_codes=("Bad",),
            new_entries=False,
            size_mult=None,
            min_score=None,
            require_rs_vs_btc_30d_positive=None,
            trailing_atr_mult=None,
        )
    with pytest.raises(ValidationError):
        RegimeDecision(
            regime=None,
            raw="RISK_ON",
            reason_codes=("hysteresis",),
            new_entries=False,
            size_mult=Decimal("1"),
            min_score=None,
            require_rs_vs_btc_30d_positive=None,
            trailing_atr_mult=None,
        )
    with pytest.raises(ValidationError):
        RegimeDecision(
            regime="RISK_ON",
            raw="RISK_ON",
            reason_codes=("hysteresis",),
            new_entries=True,
            size_mult=Decimal("1"),
            min_score=70,
            require_rs_vs_btc_30d_positive=None,
            trailing_atr_mult=None,
        )
    with pytest.raises(ValidationError):
        RegimeDecision(
            regime="RISK_ON",
            raw=None,
            reason_codes=("risk_on",),
            new_entries=True,
            size_mult=Decimal("1"),
            min_score=70,
            require_rs_vs_btc_30d_positive=None,
            trailing_atr_mult=None,
        )
    duplicate = list(_series("BTCUSDT", 200, last_close="101"))
    with pytest.raises(EvaluationError):
        _classify(btc_bars=(*duplicate, duplicate[-1]))
    mismatched = _series("ETHUSDT", 60, last_close="101")
    with pytest.raises(EvaluationError):
        _classify(universe_bars={"AAAUSDT": mismatched})
    peaked = list(_series("BTCUSDT", 200, last_close="101"))
    peaked[-1] = _bar("BTCUSDT", AS_OF, "101", high="100")
    with pytest.raises(EvaluationError):
        _classify(btc_bars=tuple(peaked))
    zero_high = list(_series("BTCUSDT", 200, last_close="101"))
    zero_high[-1] = _bar("BTCUSDT", AS_OF, "1", high="0")
    assert "invalid_btc_price" in _classify(btc_bars=tuple(zero_high)).reason_codes
    yesterday = _series("AAAUSDT", 60, as_of=AS_OF - timedelta(days=1))
    assert _classify(universe_bars={"AAAUSDT": yesterday}).reason_codes == ("missing_breadth",)
    present = {"AAAUSDT": (), "BBBUSDT": _series("BBBUSDT", 60, last_close="101")}
    assert _classify(universe_bars=present).reason_codes == ("hysteresis",)
    holed = list(_series("AAAUSDT", 70, last_close="101"))
    del holed[5]
    holed_book = {"AAAUSDT": tuple(holed), "BBBUSDT": _series("BBBUSDT", 60)}
    assert _classify(universe_bars=holed_book, prior=_priors("RISK_ON", "RISK_ON", 1)).regime == (
        "RISK_ON"
    )


def _remember(day: date, decision: RegimeDecision, stored: list[PriorSession]) -> None:
    if decision.raw is None:
        return
    stored.append(PriorSession(session=day, raw=decision.raw, published=decision.regime))


def _on_day(day: date, prior: tuple[PriorSession, ...], *, tape: str) -> RegimeDecision:
    if tape == "risk_off":
        btc = _series("BTCUSDT", 200, close="1", last_close="79", high="100", as_of=day)
    elif tape == "neutral":
        btc = _series("BTCUSDT", 200, last_close="99", as_of=day)
    else:
        btc = _series("BTCUSDT", 200, last_close="101", as_of=day)
    universe = {
        "AAAUSDT": _series("AAAUSDT", 60, last_close="101", as_of=day),
        "BBBUSDT": _series("BBBUSDT", 60, last_close="101", as_of=day),
    }
    return classify(
        as_of=day,
        btc_bars=btc,
        universe_bars=universe,
        observations=_observations(day),
        prior=prior,
        hypotheses=REGIME,
    )


def test_a_decision_is_a_valid_next_prior() -> None:
    stored: list[PriorSession] = []
    start = AS_OF - timedelta(days=2)
    decisions: list[RegimeDecision] = []
    for offset in range(3):
        day = start + timedelta(days=offset)
        decision = _on_day(day, tuple(stored), tape="risk_on")
        decisions.append(decision)
        _remember(day, decision, stored)
    assert [item.regime for item in decisions] == [None, None, "RISK_ON"]
    assert decisions[0].raw == "RISK_ON"
    assert decisions[2].new_entries is True
    stored.clear()
    shock = _on_day(AS_OF - timedelta(days=3), (), tape="risk_off")
    assert shock.regime == "RISK_OFF"
    _remember(AS_OF - timedelta(days=3), shock, stored)
    loosened: list[RegimeDecision] = []
    for offset in range(1, 4):
        day = AS_OF - timedelta(days=3 - offset)
        decision = _on_day(day, tuple(stored), tape="neutral")
        loosened.append(decision)
        _remember(day, decision, stored)
    assert [item.regime for item in loosened] == ["RISK_OFF", "RISK_OFF", "NEUTRAL"]
    assert [item.raw for item in loosened] == ["NEUTRAL", "NEUTRAL", "NEUTRAL"]
    assert loosened[0].new_entries is False
    assert loosened[2].new_entries is True


def test_hypotheses_are_the_thresholds() -> None:
    assert isinstance(REGIME, RegimeHypotheses)
    assert REGIME.hysteresis_days == 3
    published = _classify(prior=_priors("RISK_ON", "RISK_ON", 1))
    assert published.new_entries is REGIME.risk_on.new_entries
    assert Decimal(1) / Decimal(2) == Decimal(str(REGIME.breadth_risk_on))
    assert Decimal(3) / Decimal(10) == Decimal(str(REGIME.breadth_risk_off))
    half = _classify(universe_bars=_universe("half"), prior=_priors("RISK_ON", "RISK_ON", 1))
    assert half.regime == "RISK_ON"
    at_floor = {
        f"S{index:02d}USDT": _series(
            f"S{index:02d}USDT",
            60,
            last_close="101" if index < 3 else "100",
        )
        for index in range(10)
    }
    touch = _classify(
        btc_bars=_series("BTCUSDT", 200, last_close="99"),
        universe_bars=at_floor,
        prior=_priors("RISK_ON", "RISK_ON", 1),
    )
    assert touch.regime is None
    assert touch.reason_codes == ("regime_unclassified",)
