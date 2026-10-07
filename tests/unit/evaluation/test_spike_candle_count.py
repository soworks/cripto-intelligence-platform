"""Spike-candle counts from completed daily and hourly quote volume."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from cip.domain.errors import EvaluationError
from cip.domain.policy import load_policy
from cip.evaluation.liquidity_capture import HourQuote, spike_candle_count, store_liquidity
from cip.history.bars import DailyBar

SESSION = date(2026, 10, 7)
EVENT = date(2026, 10, 6)
AS_OF = datetime(2026, 10, 7, 15, tzinfo=UTC)
_POLICY = Path(__file__).parents[3] / "policies" / "investment-policy.yaml"
_RULES = load_policy(_POLICY).policy.hypotheses.universe.manipulation
_HOUR_MS = 3_600_000


def _day(day: date, quote: str | Decimal) -> DailyBar:
    return DailyBar(
        symbol="BTCUSDT",
        open_date=day,
        open=Decimal("10"),
        high=Decimal("11"),
        low=Decimal("9"),
        close=Decimal("10"),
        volume=Decimal("1"),
        quote_volume=quote if isinstance(quote, Decimal) else Decimal(quote),
        trade_count=2,
        taker_buy_base_volume=Decimal("1"),
        taker_buy_quote_volume=Decimal("1"),
    )


def _days(event_quote: str, *, baseline: list[str] | None = None) -> tuple[DailyBar, ...]:
    values = baseline if baseline is not None else ["2400"] * 28 + ["1200", "3600"]
    start = EVENT - timedelta(days=len(values))
    bars = [_day(start + timedelta(days=offset), quote) for offset, quote in enumerate(values)]
    return (*bars, _day(EVENT, event_quote))


def _hours(volumes: list[str] | list[Decimal], *, day: date = EVENT) -> tuple[HourQuote, ...]:
    midnight = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)
    return tuple(
        HourQuote(
            midnight + offset * _HOUR_MS,
            midnight + (offset + 1) * _HOUR_MS - 1,
            volume if isinstance(volume, Decimal) else Decimal(volume),
        )
        for offset, volume in enumerate(volumes)
    )


def _exact(day: Decimal, volumes: list[Decimal]) -> int | None:
    bars = list(_days("2400"))
    bars[-1] = _day(EVENT, day)
    return spike_candle_count(
        tuple(bars),
        _hours(volumes),
        session=SESSION,
        as_of=AS_OF,
        formula=_RULES.spike_count,
        zscore_above=Decimal(str(_RULES.volume_zscore_above)),
    )


def _count(
    quote: str,
    volumes: list[str],
    *,
    as_of: datetime = AS_OF,
    days: tuple[DailyBar, ...] | None = None,
    hours: tuple[HourQuote, ...] | None = None,
) -> int | None:
    return spike_candle_count(
        _days(quote) if days is None else days,
        _hours(volumes) if hours is None else hours,
        session=SESSION,
        as_of=as_of,
        formula=_RULES.spike_count,
        zscore_above=Decimal(str(_RULES.volume_zscore_above)),
    )


def test_worked_examples_follow_the_approved_count() -> None:
    assert _count("2400", ["100"] * 24) == 0
    assert _count("3000", ["3000", *["0"] * 23]) == 0
    assert _count("4800", ["4800", *["0"] * 23]) == 1
    assert _count("4800", ["2400", "2400", *["0"] * 22]) == 2
    assert _count("4800", ["1600"] * 3 + ["0"] * 21) == 3
    assert _count("4800", ["960"] * 5 + ["0"] * 19) == 5
    assert _count("4800", ["200"] * 24) == 24
    assert _count("4800", ["4700", "100", *["0"] * 22]) == 1


def test_the_zscore_line_is_strict_and_a_barely_abnormal_day_keeps_its_shape() -> None:
    baseline = [Decimal("2400")] * 28 + [Decimal("1200"), Decimal("3600")]
    mean = sum(baseline, Decimal(0)) / Decimal(30)
    variance = sum((item - mean) ** 2 for item in baseline) / Decimal(29)
    line = mean + 4 * variance.sqrt()
    assert _exact(line, [Decimal(0)] * 23 + [line]) == 0
    above = line + Decimal("0.01")
    assert _exact(above, [Decimal(0)] * 23 + [above]) == 1
    even_day = Decimal(3672)
    assert even_day > line
    assert _exact(even_day, [Decimal(153)] * 24) == 24


def test_missing_history_zero_variance_and_the_cutoff_stay_missing() -> None:
    assert _count("4800", ["4800", *["0"] * 23], days=_days("4800")[:-1]) is None
    assert _count("4800", ["100"] * 24, days=(_day(SESSION, "4800"),)) is None
    assert _count("4800", ["4800", *["0"] * 23], days=_days("4800")[-10:]) is None
    gapped = list(_days("4800"))
    gapped[3] = _day(date(2020, 1, 1), "2400")
    assert _count("4800", ["4800", *["0"] * 23], days=tuple(gapped)) is None
    assert _count("4800", ["200"] * 23) is None
    flat = ["2400"] * 30
    assert _count("2400", ["100"] * 24, days=_days("2400", baseline=flat)) is None
    before = datetime(2026, 10, 6, 23, 59, tzinfo=UTC)
    assert _count("4800", ["4800", *["0"] * 23], as_of=before) is None
    at_close = datetime(2026, 10, 7, tzinfo=UTC)
    assert _count("4800", ["4800", *["0"] * 23], as_of=at_close) == 1
    session_bar = _day(SESSION, "4800")
    during = _count(
        "2400",
        ["100"] * 24,
        days=(*_days("2400"), session_bar),
        as_of=AS_OF,
    )
    assert during == 0
    session_hours = _hours(["4800", *["0"] * 23], day=SESSION)
    after = datetime(2026, 10, 8, tzinfo=UTC)
    assert (
        spike_candle_count(
            (*_days("2400"), session_bar),
            session_hours,
            session=SESSION,
            as_of=after,
            formula=_RULES.spike_count,
            zscore_above=Decimal(str(_RULES.volume_zscore_above)),
        )
        == 1
    )
    assert (
        spike_candle_count(
            (*_days("2400"), session_bar),
            session_hours,
            session=SESSION,
            as_of=AS_OF,
            formula=_RULES.spike_count,
            zscore_above=Decimal(str(_RULES.volume_zscore_above)),
        )
        is None
    )


def test_a_volume_contradiction_is_an_integrity_failure_not_a_missing_count() -> None:
    with pytest.raises(EvaluationError, match="spike evidence is inconsistent"):
        _count("4800", ["100"] * 24)
    with pytest.raises(EvaluationError, match="spike evidence is inconsistent"):
        _count("2400", ["0"] * 24)
    flat = ["2400"] * 30
    with pytest.raises(EvaluationError, match="spike evidence is inconsistent"):
        _count("2400", ["0"] * 24, days=_days("2400", baseline=flat))


def test_a_formula_whose_sample_does_not_match_its_baseline_is_unusable() -> None:
    shifted = _RULES.spike_count.model_copy(update={"baseline_days": 29})
    with pytest.raises(EvaluationError, match="unusable"):
        spike_candle_count(
            _days("4800"),
            _hours(["4800", *["0"] * 23]),
            session=SESSION,
            as_of=AS_OF,
            formula=shifted,
            zscore_above=Decimal("4"),
        )


def test_malformed_bars_and_a_naive_clock_are_unusable() -> None:
    duplicate = (*_days("4800"), _day(EVENT, "4800"))
    with pytest.raises(EvaluationError, match="unusable"):
        _count("4800", ["4800", *["0"] * 23], days=duplicate)
    negative = list(_days("4800"))
    negative[-1] = _day(EVENT, "-1")
    with pytest.raises(EvaluationError, match="unusable"):
        _count("4800", ["4800", *["0"] * 23], days=tuple(negative))
    broken = list(_hours(["4800", *["0"] * 23]))
    broken[0] = HourQuote(broken[0].open_ms, broken[0].close_ms + 1, Decimal("4800"))
    with pytest.raises(EvaluationError, match="unusable"):
        _count("4800", [], hours=tuple(broken))
    repeated = list(_hours(["4800", *["0"] * 23]))
    repeated.append(repeated[0])
    with pytest.raises(EvaluationError, match="unusable"):
        _count("4800", [], hours=tuple(repeated))
    priced = list(_hours(["4800", *["0"] * 23]))
    priced[0] = HourQuote(priced[0].open_ms, priced[0].close_ms, Decimal("-1"))
    with pytest.raises(EvaluationError, match="unusable"):
        _count("4800", [], hours=tuple(priced))
    infinite = list(_hours(["4800", *["0"] * 23]))
    infinite[1] = HourQuote(infinite[1].open_ms, infinite[1].close_ms, Decimal("Infinity"))
    with pytest.raises(EvaluationError, match="unusable"):
        _count("4800", [], hours=tuple(infinite))
    endless = list(_days("4800"))
    endless[-1] = _day(EVENT, "Infinity")
    with pytest.raises(EvaluationError, match="unusable"):
        _count("4800", ["4800", *["0"] * 23], days=tuple(endless))
    with pytest.raises(EvaluationError, match="timezone-aware UTC"):
        spike_candle_count(
            _days("4800"),
            _hours(["4800", *["0"] * 23]),
            session=SESSION,
            as_of=datetime(2026, 10, 7, 15, 0),  # noqa: DTZ001
            formula=_RULES.spike_count,
            zscore_above=Decimal("4"),
        )


def test_a_stored_count_must_match_the_hour_grid(tmp_path: Path) -> None:
    from cip.evaluation.liquidity_capture import derive_liquidity
    from tests.unit.evaluation.test_liquidity_capture import _capture

    bars = _days("4800")
    derived = derive_liquidity(
        bars,
        SESSION,
        binance_quote_volume=Decimal("50"),
        market_cap=Decimal("200"),
        aggregate_volume=Decimal("80"),
    )
    hours = _hours(["4800", *["0"] * 23])
    item = _capture(
        bars=bars,
        hour_quotes=hours,
        spike_candle_count=1,
        median_quote_volume_30d_usd=derived.median_quote_volume_30d_usd,
        day_quote_volume_usd=derived.day_quote_volume_usd,
        turnover=derived.turnover,
        volume_zscore=derived.volume_zscore,
        price_move=derived.price_move,
        trade_size_stdev=derived.trade_size_stdev,
        binance_volume_share=derived.binance_volume_share,
    )
    store_liquidity(tmp_path, SESSION, item, manipulation=_RULES)
    written = next(tmp_path.rglob("*.json")).read_text()
    assert '"spike_candle_count": 1' in written
    with pytest.raises(EvaluationError, match="does not match"):
        store_liquidity(
            tmp_path / "wrong",
            SESSION,
            _capture(spike_candle_count=2),
            manipulation=_RULES,
        )
    mismatched = _capture(
        bars=bars,
        hour_quotes=hours,
        spike_candle_count=2,
        median_quote_volume_30d_usd=derived.median_quote_volume_30d_usd,
        day_quote_volume_usd=derived.day_quote_volume_usd,
        turnover=derived.turnover,
        volume_zscore=derived.volume_zscore,
        price_move=derived.price_move,
        trade_size_stdev=derived.trade_size_stdev,
        binance_volume_share=derived.binance_volume_share,
    )
    with pytest.raises(EvaluationError, match="does not match"):
        store_liquidity(tmp_path / "off", SESSION, mismatched, manipulation=_RULES)
    contradicted = _capture(
        bars=bars,
        hour_quotes=_hours(["100"] * 24),
        spike_candle_count=None,
        median_quote_volume_30d_usd=derived.median_quote_volume_30d_usd,
        day_quote_volume_usd=derived.day_quote_volume_usd,
        turnover=derived.turnover,
        volume_zscore=derived.volume_zscore,
        price_move=derived.price_move,
        trade_size_stdev=derived.trade_size_stdev,
        binance_volume_share=derived.binance_volume_share,
    )
    with pytest.raises(EvaluationError, match="spike evidence is inconsistent"):
        store_liquidity(tmp_path / "broken", SESSION, contradicted, manipulation=_RULES)
    assert list((tmp_path / "broken").rglob("*.json")) == []
    with pytest.raises(EvaluationError, match="unusable"):
        store_liquidity(tmp_path / "undated", SESSION, item)


def test_a_sealed_session_is_not_given_a_computed_count(tmp_path: Path) -> None:
    from tests.unit.evaluation.test_liquidity_capture import _capture

    hours = _hours(["4800", *["0"] * 23])
    item = _capture(hour_quotes=hours, spike_candle_count=1)
    with pytest.raises(EvaluationError, match="sealed session stays sealed"):
        store_liquidity(tmp_path, date(2026, 10, 6), item, manipulation=_RULES)
    assert list(tmp_path.rglob("*.json")) == []
