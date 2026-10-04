import random
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from cip.backtest.replay import ReplayResult, replay
from cip.domain.errors import BacktestError
from cip.history.bars import DailyBar

_D0 = date(2026, 1, 1)
_D1 = date(2026, 1, 2)
_D2 = date(2026, 1, 3)
_D3 = date(2026, 1, 4)
_D4 = date(2026, 1, 5)
_CASH = Decimal("1000")
_FEE = Decimal("0.001")
_SPREAD = Decimal("0.002")
_SLIP = Decimal("0.001")
_DRAG = Decimal("0.003")
_RUNS = 1000


def _bar(symbol: str, day: date, price: str, *, close: str | None = None) -> DailyBar:
    opened = Decimal(price)
    closed = Decimal(close) if close is not None else opened
    unit = Decimal(1)
    return DailyBar(
        symbol=symbol,
        open_date=day,
        open=opened,
        high=opened,
        low=opened,
        close=closed,
        volume=unit,
        quote_volume=unit,
        trade_count=1,
        taker_buy_base_volume=unit,
        taker_buy_quote_volume=unit,
    )


def _flat(
    symbols: tuple[str, ...],
    days: tuple[date, ...],
    price: str = "100",
    closes: dict[tuple[str, date], str] | None = None,
) -> dict[str, tuple[DailyBar, ...]]:
    overrides = closes or {}
    return {
        symbol: tuple(_bar(symbol, day, price, close=overrides.get((symbol, day))) for day in days)
        for symbol in symbols
    }


class Gate:
    def __init__(self, allowed: set[str]) -> None:
        self.allowed = allowed
        self.calls: list[tuple[str, date]] = []

    def eligible(self, symbol: str, as_of: date) -> bool:
        self.calls.append((symbol, as_of))
        return symbol in self.allowed


class Scores:
    def __init__(self, values: dict[str, Decimal]) -> None:
        self.values = values
        self.calls: list[tuple[str, date]] = []

    def score(self, symbol: str, as_of: date) -> Decimal:
        self.calls.append((symbol, as_of))
        if symbol not in self.values:
            raise AssertionError(symbol)
        return self.values[symbol]


class DatedScores:
    def __init__(self, values: dict[date, dict[str, Decimal]]) -> None:
        self.values = values
        self.calls: list[tuple[str, date]] = []

    def score(self, symbol: str, as_of: date) -> Decimal:
        self.calls.append((symbol, as_of))
        return self.values[as_of][symbol]


class Exits:
    def __init__(self, due: set[tuple[str, date]]) -> None:
        self.due = due
        self.calls: list[tuple[str, date]] = []

    def exit_due(self, symbol: str, as_of: date) -> bool:
        self.calls.append((symbol, as_of))
        return (symbol, as_of) in self.due


def _run_seeds(seed: int) -> tuple[int, ...]:
    generator = random.Random(seed)  # noqa: S311 - seeded baseline, not a secret
    return tuple(generator.randrange(2**63) for _ in range(_RUNS))


def _random_pick(seed: int, names: tuple[str, ...], count: int) -> tuple[str, ...]:
    eligible = tuple(sorted(names))
    if len(eligible) <= count:
        return eligible
    picker = random.Random(_run_seeds(seed)[0])  # noqa: S311 - seeded baseline, not a secret
    return tuple(sorted(picker.sample(list(eligible), count)))


def _replay(**overrides: object) -> ReplayResult:
    arguments: dict[str, object] = {
        "bars": _flat(("AAA", "BBB"), (_D0, _D1)),
        "capital": _CASH,
        "fee_rate": _FEE,
        "spread": _SPREAD,
        "slippage": _SLIP,
        "eligibility": Gate({"AAA", "BBB"}),
        "scorer": Scores({"AAA": Decimal("2"), "BBB": Decimal("1")}),
        "selection_count": 1,
        "seed": 7,
    }
    arguments.update(overrides)
    return replay(**arguments)  # type: ignore[arg-type]


def test_next_bar_fill_prices_the_open_and_leaves_the_signal_day_in_cash() -> None:
    bars = {
        "AAA": (
            _bar("AAA", _D0, "100"),
            _bar("AAA", _D1, "100", close="110"),
        )
    }
    result = _replay(bars=bars, eligibility=Gate({"AAA"}), scorer=Scores({"AAA": Decimal("2")}))

    unit = Decimal("100") * (1 + _DRAG)
    quantity = _CASH / unit
    assert result.strategy.equity[0] == (_D0, _CASH)
    assert result.strategy.equity[1] == (_D1, quantity * Decimal("110"))
    assert result.strategy.fee_drag == _CASH * _DRAG / (1 + _DRAG)
    assert result.strategy.picks == ((_D0, ("AAA",)), (_D1, ("AAA",)))
    assert result.equal_weight.equity == result.strategy.equity


def test_half_spread_is_the_fill_cost() -> None:
    result = _replay(
        bars=_flat(("AAA",), (_D0, _D1)),
        eligibility=Gate({"AAA"}),
        scorer=Scores({"AAA": Decimal("1")}),
        fee_rate=Decimal(0),
        spread=Decimal("0.002"),
        slippage=Decimal(0),
    )

    assert result.strategy.equity[1][1] == _CASH / Decimal("1.001")
    assert result.strategy.fee_drag == _CASH * Decimal("0.001") / Decimal("1.001")


def test_equal_weight_holds_every_eligible_name_and_rank_breaks_ties_by_symbol() -> None:
    closes = {("AAA", _D1): "110"}
    bars = _flat(("BBB", "AAA"), (_D0, _D1), closes=closes)
    ranked = _replay(bars=bars)
    tied = _replay(
        bars=bars,
        scorer=Scores({"AAA": Decimal("1"), "BBB": Decimal("1")}),
    )
    higher = _replay(bars=bars, scorer=Scores({"AAA": Decimal("1"), "BBB": Decimal("3")}))

    aaa = (_CASH / 2) / (Decimal("100") * (1 + _DRAG))
    bbb = (_CASH / 2) / (Decimal("100") * (1 + _DRAG))
    assert ranked.equal_weight.picks[0][1] == ("AAA", "BBB")
    assert ranked.equal_weight.equity[1][1] == aaa * Decimal("110") + bbb * Decimal("100")
    assert ranked.strategy.picks[0][1] == ("AAA",)
    assert tied.strategy.picks[0][1] == ("AAA",)
    assert higher.strategy.picks[0][1] == ("BBB",)


def test_a_short_eligible_set_is_taken_whole_and_an_empty_set_stays_in_cash() -> None:
    short = _replay(selection_count=5)
    scorer = Scores({})
    empty = _replay(eligibility=Gate(set()), scorer=scorer)

    assert short.strategy.picks[0][1] == ("AAA", "BBB")
    assert all(path.picks[0][1] == ("AAA", "BBB") for path in short.random)
    assert empty.strategy.equity[1][1] == _CASH
    assert empty.strategy.fee_drag == Decimal(0)
    assert empty.strategy.picks[0][1] == ()
    assert empty.equal_weight.picks[0][1] == ()
    assert empty.random[0].picks[0][1] == ()
    assert scorer.calls == []


def test_ineligible_names_are_not_scored() -> None:
    scorer = Scores({"AAA": Decimal("2")})
    _replay(eligibility=Gate({"AAA"}), scorer=scorer)

    assert {symbol for symbol, _day in scorer.calls} == {"AAA"}


def test_same_membership_does_not_churn_and_a_new_set_does() -> None:
    days = (_D0, _D1, _D2)
    held = _replay(bars=_flat(("AAA", "BBB"), days))
    rotated = _replay(
        bars=_flat(("AAA", "BBB"), days),
        scorer=DatedScores(
            {
                _D0: {"AAA": Decimal("2"), "BBB": Decimal("1")},
                _D1: {"AAA": Decimal("1"), "BBB": Decimal("2")},
                _D2: {"AAA": Decimal("1"), "BBB": Decimal("2")},
            }
        ),
    )

    one_buy = _CASH * _DRAG / (1 + _DRAG)
    unit = Decimal("100") * (1 + _DRAG)
    quantity = _CASH / unit
    assert held.strategy.fee_drag == one_buy
    assert held.strategy.equity[2][1] == quantity * Decimal("100")
    proceeds = quantity * Decimal("100") * (1 - _DRAG)
    assert rotated.strategy.equity[2][1] == proceeds / unit * Decimal("100")
    assert rotated.strategy.picks[1][1] == ("BBB",)


def test_zero_drag_deploys_the_whole_cash_balance_across_three_names() -> None:
    result = _replay(
        bars=_flat(("AAA", "BBB", "CCC"), (_D0, _D1)),
        eligibility=Gate({"AAA", "BBB", "CCC"}),
        scorer=Scores({"AAA": Decimal("1"), "BBB": Decimal("1"), "CCC": Decimal("1")}),
        selection_count=3,
        fee_rate=Decimal(0),
        spread=Decimal(0),
        slippage=Decimal(0),
    )

    assert result.equal_weight.equity[1][1] == _CASH
    assert result.equal_weight.fee_drag == 0
    assert result.strategy.picks[0][1] == ("AAA", "BBB", "CCC")


def test_blocked_entries_hold_cash_while_the_benchmark_still_buys() -> None:
    scorer = Scores({"AAA": Decimal("2"), "BBB": Decimal("1")})
    result = _replay(entries_allowed={}, scorer=scorer)

    assert result.strategy.equity[1][1] == _CASH
    assert result.strategy.fee_drag == 0
    assert result.strategy.picks[0][1] == ()
    assert scorer.calls == []
    half = _CASH / 2
    unit = Decimal("100") * (1 + _DRAG)
    assert result.equal_weight.equity[1][1] == (half / unit) * Decimal("100") * 2


def test_a_missing_regime_date_blocks_entries_and_an_open_regime_allows_them() -> None:
    blocked = _replay(entries_allowed={date(2020, 1, 1): True})
    allowed = _replay(entries_allowed=None)

    assert blocked.strategy.fee_drag == 0
    assert allowed.strategy.picks[0][1] == ("AAA",)
    assert allowed.strategy.fee_drag == _CASH * _DRAG / (1 + _DRAG)


def test_risk_off_holds_the_book_except_for_an_explicit_exit() -> None:
    days = (_D0, _D1, _D2)
    bars = _flat(("AAA", "BBB"), days)
    entries = {_D0: True, _D1: False, _D2: False}
    scorer = DatedScores(
        {
            _D0: {"AAA": Decimal("2"), "BBB": Decimal("1")},
            _D1: {"AAA": Decimal("1"), "BBB": Decimal("9")},
            _D2: {"AAA": Decimal("1"), "BBB": Decimal("9")},
        }
    )
    held = _replay(bars=bars, entries_allowed=entries, scorer=scorer)
    exits = Exits({("AAA", _D1), ("AAA", _D2)})
    sold = _replay(bars=bars, entries_allowed=entries, scorer=scorer, exit_rules=exits)

    assert held.strategy.picks[1][1] == ("AAA",)
    assert {day for _symbol, day in scorer.calls} == {_D0}
    assert sold.strategy.picks[1][1] == ()
    assert ("AAA", _D1) in exits.calls
    quantity = _CASH / (Decimal("100") * (1 + _DRAG))
    assert sold.strategy.equity[2][1] == quantity * Decimal("100") * (1 - _DRAG)


def test_an_exit_on_the_signal_prevents_the_entry() -> None:
    exits = Exits({("AAA", _D0), ("AAA", _D1)})
    result = _replay(exit_rules=exits)

    assert result.strategy.picks[0][1] == ()
    assert result.strategy.fee_drag == 0
    assert result.equal_weight.fee_drag > 0


def test_a_last_day_exit_signal_does_not_fill() -> None:
    exits = Exits({("AAA", _D1)})
    result = _replay(
        bars=_flat(("AAA",), (_D0, _D1)),
        eligibility=Gate({"AAA"}),
        scorer=Scores({"AAA": Decimal("1")}),
        exit_rules=exits,
    )

    quantity = _CASH / (Decimal("100") * (1 + _DRAG))
    assert result.strategy.picks[1][1] == ()
    assert result.strategy.equity[1][1] == quantity * Decimal("100")
    assert result.strategy.fee_drag == _CASH * _DRAG / (1 + _DRAG)


def test_without_exit_rules_a_falling_price_is_marked_not_sold() -> None:
    bars = {
        "AAA": (
            _bar("AAA", _D0, "100"),
            _bar("AAA", _D1, "100", close="50"),
            _bar("AAA", _D2, "50"),
        )
    }
    result = _replay(
        bars=bars,
        eligibility=Gate({"AAA"}),
        scorer=Scores({"AAA": Decimal("1")}),
        exit_rules=None,
    )

    quantity = _CASH / (Decimal("100") * (1 + _DRAG))
    assert result.strategy.equity[2][1] == quantity * Decimal("50")
    assert result.strategy.fee_drag == _CASH * _DRAG / (1 + _DRAG)


def test_random_baseline_persists_the_seed_and_restarts_inside_each_fold() -> None:
    days = (_D0, _D1, _D2, _D3, _D4)
    bars = _flat(("BBB", "AAA"), days, closes={("AAA", _D2): "50", ("AAA", _D3): "80"})
    first = _replay(bars=bars, seed=7, fold_count=2)
    second = _replay(bars=bars, seed=7, fold_count=2)
    other = _replay(bars=bars, seed=8, fold_count=2)

    assert first.seed == 7
    assert first.run_seeds == _run_seeds(7)
    assert len(first.random) == _RUNS
    assert first.random[0].picks[0][1] == _random_pick(7, ("BBB", "AAA"), 1)
    assert other.random[0].picks[0][1] == _random_pick(8, ("BBB", "AAA"), 1)
    assert first.random[0].picks == second.random[0].picks
    assert first.strategy.picks == other.strategy.picks
    assert [point[0] for point in first.folds[0].strategy.equity] == [_D0, _D1, _D2]
    assert [point[0] for point in first.folds[1].strategy.equity] == [_D3, _D4]
    assert first.folds[1].strategy.equity[0][1] == _CASH
    assert first.strategy.equity[3][1] != _CASH
    assert first.folds[0].random[0].picks[0] == first.random[0].picks[0]
    assert first.folds[1].random[0].picks[0][1] == _random_pick(7, ("BBB", "AAA"), 1)
    only = _replay(
        bars=_flat(("AAA",), (_D0, _D1)),
        eligibility=Gate({"AAA"}),
        scorer=Scores({"AAA": Decimal("1")}),
        selection_count=2,
    )
    assert all(path.picks[0][1] == ("AAA",) for path in only.random)


def test_one_fold_is_the_whole_window_and_a_short_fold_is_refused() -> None:
    whole = _replay(fold_count=1)
    assert len(whole.folds) == 1
    assert whole.folds[0].strategy.equity == whole.strategy.equity
    with pytest.raises(BacktestError, match="walk-forward fold"):
        _replay(bars=_flat(("AAA", "BBB"), (_D0, _D1, _D2)), fold_count=2)


def test_a_later_session_date_is_the_next_fill_without_requiring_the_next_calendar_day() -> None:
    gap = date(2026, 1, 3)
    result = _replay(
        bars=_flat(("AAA",), (_D0, gap)),
        eligibility=Gate({"AAA"}),
        scorer=Scores({"AAA": Decimal("1")}),
    )

    assert [point[0] for point in result.strategy.equity] == [_D0, gap]


def test_replay_does_not_copy_eligibility_scoring_or_exits() -> None:
    source = Path("src/cip/backtest/replay.py").read_text()
    for forbidden in (
        "cip.evaluation",
        "combine(",
        "assess(",
        "classify(",
        "size_usd",
        "load_policy",
        "hit_rate",
        "expectancy",
        "payoff",
    ):
        assert forbidden not in source


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"bars": {}}, "symbols"),
        ({"bars": {"AAA": ()}}, "no bars"),
        ({"capital": Decimal("0")}, "capital"),
        ({"capital": Decimal("-1")}, "capital"),
        ({"capital": Decimal("NaN")}, "capital"),
        ({"capital": 1000}, "capital"),
        ({"fee_rate": 0.001}, "cost"),
        ({"spread": Decimal("-0.001")}, "cost"),
        ({"slippage": Decimal("NaN")}, "cost"),
        ({"selection_count": 0}, "selection count"),
        ({"selection_count": True}, "selection count"),
        ({"seed": True}, "seed"),
        ({"fold_count": 0}, "fold count"),
        ({"fold_count": True}, "fold count"),
        ({"n_runs": 999}, "1000"),
        ({"entries_allowed": {_D0: 1, _D1: True}}, "entries_allowed"),
    ],
)
def test_unusable_inputs_fail_closed(overrides: dict[str, object], match: str) -> None:
    with pytest.raises(BacktestError, match=match):
        _replay(**overrides)


def test_bad_bars_fail_closed() -> None:
    mismatch = {"AAA": (_bar("BBB", _D0, "100"), _bar("BBB", _D1, "100"))}
    unsorted = {"AAA": (_bar("AAA", _D1, "100"), _bar("AAA", _D0, "100"))}
    duplicate = {"AAA": (_bar("AAA", _D0, "100"), _bar("AAA", _D0, "100"))}
    partial = {
        "AAA": (_bar("AAA", _D0, "100"), _bar("AAA", _D1, "100")),
        "BBB": (_bar("BBB", _D0, "100"),),
    }
    zero = _bar("AAA", _D0, "100")
    object.__setattr__(zero, "open", Decimal(0))
    nan = _bar("AAA", _D1, "100")
    object.__setattr__(nan, "close", Decimal("NaN"))
    integer = _bar("AAA", _D0, "100")
    object.__setattr__(integer, "open", 100)
    one_day = {"AAA": (_bar("AAA", _D0, "100"),)}

    cases = (
        (mismatch, "symbol"),
        (unsorted, "ascending"),
        (duplicate, "ascending"),
        (partial, "session"),
        ({"AAA": (zero, _bar("AAA", _D1, "100"))}, "open"),
        ({"AAA": (_bar("AAA", _D0, "100"), nan)}, "close"),
        ({"AAA": (integer, _bar("AAA", _D1, "100"))}, "open"),
        (one_day, "signal"),
        ({"": (_bar("", _D0, "100"), _bar("", _D1, "100"))}, "symbol"),
    )
    for bars, match in cases:
        with pytest.raises(BacktestError, match=match):
            _replay(bars=bars)


def test_protocol_results_must_be_usable() -> None:
    class BadGate:
        def eligible(self, symbol: str, as_of: date) -> bool:
            return 1  # type: ignore[return-value]

    class BadScore:
        def __init__(self, value: object) -> None:
            self.value = value

        def score(self, symbol: str, as_of: date) -> Decimal:
            return self.value  # type: ignore[return-value]

    class BadExit:
        def exit_due(self, symbol: str, as_of: date) -> bool:
            return "yes"  # type: ignore[return-value]

    with pytest.raises(BacktestError, match="eligibility"):
        _replay(eligibility=BadGate())
    with pytest.raises(BacktestError, match="score"):
        _replay(scorer=BadScore(1))
    with pytest.raises(BacktestError, match="score"):
        _replay(scorer=BadScore(Decimal("NaN")))
    with pytest.raises(BacktestError, match="exit"):
        _replay(exit_rules=BadExit())
