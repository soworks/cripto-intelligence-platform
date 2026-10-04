"""Event-driven daily replay.

The simulator calls the eligibility, score, and exit contracts. It does not
recompute those rules. Regime is the caller's published new-entries flag.
Omitting that map leaves entries open. A missing date blocks entries. M4 still
owns exit rules, so a book is sold only when an exit contract says so.
Membership changes trade. An unchanged set holds, and weights then drift.
When entries are allowed, a new set is sold and bought back to equal weight.
When entries are blocked, a partial exit sells only the names that left and
leaves the sale proceeds in cash.

`strategy` on the result is one continuous book. `folds` are the independent
walk-forward replays. Each random run draws again on every session.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from cip.backtest.contracts import Eligibility, ExitRules, Scorer
from cip.domain.errors import BacktestError
from cip.history.bars import DailyBar

_RUNS = 1000
_ONE = Decimal(1)

type Target = Callable[[date, Mapping[str, Decimal]], tuple[tuple[str, ...], bool]]


@dataclass(frozen=True)
class BookPath:
    equity: tuple[tuple[date, Decimal], ...]
    picks: tuple[tuple[date, tuple[str, ...]], ...]
    fee_drag: Decimal


@dataclass(frozen=True)
class FoldReplay:
    start: date
    end: date
    strategy: BookPath
    equal_weight: BookPath
    random: tuple[BookPath, ...]


@dataclass(frozen=True)
class ReplayResult:
    seed: int
    run_seeds: tuple[int, ...]
    strategy: BookPath
    equal_weight: BookPath
    random: tuple[BookPath, ...]
    folds: tuple[FoldReplay, ...]


@dataclass(frozen=True)
class _Plan:
    book: dict[str, dict[date, DailyBar]]
    capital: Decimal
    drag: Decimal
    eligibility: Eligibility
    scorer: Scorer
    selection_count: int
    entries: Mapping[date, bool] | None
    exit_rules: ExitRules | None
    run_seeds: tuple[int, ...]
    listed_on: Mapping[str, date] | None


def replay(
    bars: Mapping[str, Sequence[DailyBar]],
    *,
    capital: Decimal,
    fee_rate: Decimal,
    spread: Decimal,
    slippage: Decimal,
    eligibility: Eligibility,
    scorer: Scorer,
    selection_count: int,
    seed: int,
    entries_allowed: Mapping[date, bool] | None = None,
    exit_rules: ExitRules | None = None,
    fold_count: int = 1,
    n_runs: int = _RUNS,
    listed_on: Mapping[str, date] | None = None,
) -> ReplayResult:
    sessions, book = _sessions(bars, listed_on)
    plan = _Plan(
        book=book,
        capital=_positive(capital, "capital"),
        drag=_drag(fee_rate, spread, slippage),
        eligibility=eligibility,
        scorer=scorer,
        selection_count=_positive_int(selection_count, "selection count"),
        entries=entries_allowed,
        exit_rules=exit_rules,
        run_seeds=_run_seeds(_seed(seed), _runs(n_runs)),
        listed_on=listed_on,
    )
    _check_entries(plan.entries, sessions)
    folds = _split(sessions, fold_count)
    window = _simulate(sessions, plan)
    recorded: tuple[FoldReplay, ...]
    if len(folds) == 1:
        recorded = (_fold(sessions, window),)
    else:
        recorded = tuple(_fold(fold, _simulate(fold, plan)) for fold in folds)
    return ReplayResult(
        seed=seed,
        run_seeds=plan.run_seeds,
        strategy=window[0],
        equal_weight=window[1],
        random=window[2],
        folds=recorded,
    )


def _sessions(
    bars: Mapping[str, Sequence[DailyBar]],
    listed_on: Mapping[str, date] | None,
) -> tuple[tuple[date, ...], dict[str, dict[date, DailyBar]]]:
    sessions: tuple[date, ...] | None = None
    book: dict[str, dict[date, DailyBar]] = {}
    for symbol in sorted(bars):
        if symbol == "":
            raise BacktestError("symbol must be a non-empty string")
        series = bars[symbol]
        if not series:
            raise BacktestError(f"{symbol} has no bars")
        by_date: dict[date, DailyBar] = {}
        previous: date | None = None
        for bar in series:
            if bar.symbol != symbol:
                raise BacktestError("bar symbol does not match")
            _price(bar.open, "open")
            _price(bar.close, "close")
            if previous is not None and bar.open_date <= previous:
                raise BacktestError("dates must be unique and ascending")
            previous = bar.open_date
            by_date[bar.open_date] = bar
        dates = tuple(by_date)
        if listed_on is None:
            if sessions is None:
                sessions = dates
            elif dates != sessions:
                raise BacktestError("symbols must share the session dates")
        else:
            first = listed_on.get(symbol)
            if type(first) is not date:
                raise BacktestError(f"{symbol} is not in the point-in-time universe")
            if any(day < first for day in dates):
                raise BacktestError(f"{symbol} has a bar before it existed")
        book[symbol] = by_date
    if listed_on is not None:
        if set(listed_on) - set(book):
            raise BacktestError("listings name a symbol that has no bars")
        sessions = tuple(sorted({day for dates in book.values() for day in dates}))
        for symbol, by_date in book.items():
            first = listed_on[symbol]
            expected = tuple(day for day in sessions if day >= first)
            if tuple(by_date) != expected:
                raise BacktestError(f"{symbol} is missing a session after it existed")
    if sessions is None:
        raise BacktestError("replay needs symbols")
    if len(sessions) < 2:
        raise BacktestError("replay needs a signal and a fill")
    return sessions, book


def _simulate(
    sessions: tuple[date, ...], plan: _Plan
) -> tuple[BookPath, BookPath, tuple[BookPath, ...]]:
    symbols = tuple(plan.book)

    def strategy(day: date, positions: Mapping[str, Decimal]) -> tuple[tuple[str, ...], bool]:
        return _strategy_target(day, symbols, positions, plan), _entries_open(plan.entries, day)

    def equal(day: date, _positions: Mapping[str, Decimal]) -> tuple[tuple[str, ...], bool]:
        return _eligible_names(day, symbols, plan.eligibility, plan.listed_on), True

    def one_random(run_seed: int) -> BookPath:
        picker = random.Random(run_seed)  # noqa: S311 - seeded baseline, not a secret

        def pick(day: date, _positions: Mapping[str, Decimal]) -> tuple[tuple[str, ...], bool]:
            names = _random_target(
                day, symbols, plan.eligibility, plan.selection_count, picker, plan.listed_on
            )
            return names, True

        return _book(sessions, plan, pick)

    return (
        _book(sessions, plan, strategy),
        _book(sessions, plan, equal),
        tuple(one_random(run_seed) for run_seed in plan.run_seeds),
    )


def _book(sessions: tuple[date, ...], plan: _Plan, target_for: Target) -> BookPath:
    cash = plan.capital
    positions: dict[str, Decimal] = {}
    pending: tuple[tuple[str, ...], bool] | None = None
    equity: list[tuple[date, Decimal]] = []
    picks: list[tuple[date, tuple[str, ...]]] = []
    fee_drag = Decimal(0)
    last = len(sessions) - 1
    for index, day in enumerate(sessions):
        if pending is not None:
            cash, positions, paid = _fill(pending[0], pending[1], day, cash, positions, plan)
            fee_drag += paid
            pending = None
        marked = cash
        for symbol, quantity in positions.items():
            marked += quantity * plan.book[symbol][day].close
        equity.append((day, marked))
        target, redeploy = target_for(day, positions)
        picks.append((day, target))
        if target != tuple(sorted(positions)) and index != last:
            pending = (target, redeploy)
    return BookPath(tuple(equity), tuple(picks), fee_drag)


def _fill(
    target: tuple[str, ...],
    redeploy: bool,
    day: date,
    cash: Decimal,
    positions: Mapping[str, Decimal],
    plan: _Plan,
) -> tuple[Decimal, dict[str, Decimal], Decimal]:
    if not redeploy:
        return _reduce(target, day, cash, positions, plan)
    paid = Decimal(0)
    for symbol, quantity in positions.items():
        gross = quantity * plan.book[symbol][day].open
        cash += gross * (_ONE - plan.drag)
        paid += gross * plan.drag
    if not target:
        return cash, {}, paid
    count = len(target)
    share = cash / Decimal(count)
    bought: dict[str, Decimal] = {}
    for index, symbol in enumerate(target):
        last = index == count - 1
        budget = cash - share * Decimal(count - 1) if last else share
        unit = plan.book[symbol][day].open * (_ONE + plan.drag)
        bought[symbol] = budget / unit
        paid += budget * plan.drag / (_ONE + plan.drag)
    return Decimal(0), bought, paid


def _reduce(
    target: tuple[str, ...],
    day: date,
    cash: Decimal,
    positions: Mapping[str, Decimal],
    plan: _Plan,
) -> tuple[Decimal, dict[str, Decimal], Decimal]:
    wanted = set(target)
    kept: dict[str, Decimal] = {}
    paid = Decimal(0)
    for symbol, quantity in positions.items():
        if symbol in wanted:
            kept[symbol] = quantity
            continue
        gross = quantity * plan.book[symbol][day].open
        cash += gross * (_ONE - plan.drag)
        paid += gross * plan.drag
    return cash, kept, paid


def _strategy_target(
    day: date,
    symbols: tuple[str, ...],
    positions: Mapping[str, Decimal],
    plan: _Plan,
) -> tuple[str, ...]:
    if not _entries_open(plan.entries, day):
        held = tuple(sorted(positions))
        if plan.exit_rules is None:
            return held
        return tuple(symbol for symbol in held if not _must_exit(plan.exit_rules, symbol, day))
    ranked = sorted(
        _eligible_names(day, symbols, plan.eligibility, plan.listed_on),
        key=lambda symbol: (-_score_of(plan.scorer, symbol, day), symbol),
    )
    chosen = tuple(sorted(ranked[: plan.selection_count]))
    if plan.exit_rules is None:
        return chosen
    kept = tuple(symbol for symbol in chosen if not _must_exit(plan.exit_rules, symbol, day))
    return tuple(sorted(kept))


def _eligible_names(
    day: date,
    symbols: tuple[str, ...],
    eligibility: Eligibility,
    listed_on: Mapping[str, date] | None,
) -> tuple[str, ...]:
    return tuple(
        symbol
        for symbol in symbols
        if _listed(symbol, day, listed_on) and _is_eligible(eligibility, symbol, day)
    )


def _listed(symbol: str, day: date, listed_on: Mapping[str, date] | None) -> bool:
    if listed_on is None:
        return True
    return day >= listed_on[symbol]


def _random_target(
    day: date,
    symbols: tuple[str, ...],
    eligibility: Eligibility,
    selection_count: int,
    picker: random.Random,
    listed_on: Mapping[str, date] | None,
) -> tuple[str, ...]:
    eligible = list(_eligible_names(day, symbols, eligibility, listed_on))
    if len(eligible) <= selection_count:
        return tuple(eligible)
    return tuple(sorted(picker.sample(eligible, selection_count)))


def _is_eligible(eligibility: Eligibility, symbol: str, day: date) -> bool:
    allowed = eligibility.eligible(symbol, day)
    if type(allowed) is not bool:
        raise BacktestError("eligibility must return bool")
    return allowed


def _score_of(scorer: Scorer, symbol: str, day: date) -> Decimal:
    value = scorer.score(symbol, day)
    if type(value) is not Decimal or not value.is_finite():
        raise BacktestError("score must be a finite decimal")
    return value


def _must_exit(exit_rules: ExitRules, symbol: str, day: date) -> bool:
    due = exit_rules.exit_due(symbol, day)
    if type(due) is not bool:
        raise BacktestError("exit_due must return bool")
    return due


def _entries_open(entries: Mapping[date, bool] | None, day: date) -> bool:
    if entries is None:
        return True
    allowed = entries.get(day)
    if allowed is None:
        return False
    return allowed


def _check_entries(entries: Mapping[date, bool] | None, sessions: Sequence[date]) -> None:
    if entries is None:
        return
    for day in sessions:
        if day not in entries:
            continue
        if type(entries[day]) is not bool:
            raise BacktestError("entries_allowed values must be bool")


def _split(days: tuple[date, ...], count: int) -> tuple[tuple[date, ...], ...]:
    folds_count = _positive_int(count, "fold count")
    base, extra = divmod(len(days), folds_count)
    folds: list[tuple[date, ...]] = []
    start = 0
    for index in range(folds_count):
        length = base + (1 if index < extra else 0)
        fold = days[start : start + length]
        if len(fold) < 2:
            raise BacktestError("a walk-forward fold needs a signal and a fill")
        folds.append(fold)
        start += length
    return tuple(folds)


def _fold(
    days: tuple[date, ...],
    window: tuple[BookPath, BookPath, tuple[BookPath, ...]],
) -> FoldReplay:
    return FoldReplay(
        start=days[0],
        end=days[-1],
        strategy=window[0],
        equal_weight=window[1],
        random=window[2],
    )


def _run_seeds(seed: int, n_runs: int) -> tuple[int, ...]:
    generator = random.Random(seed)  # noqa: S311 - seeded baseline, not a secret
    return tuple(generator.randrange(2**63) for _ in range(n_runs))


def _runs(n_runs: int) -> int:
    if n_runs != _RUNS:
        raise BacktestError("random baseline runs must be 1000")
    return n_runs


def _seed(value: object) -> int:
    if type(value) is not int:
        raise BacktestError("seed must be an integer")
    return value


def _positive(value: object, name: str) -> Decimal:
    if type(value) is not Decimal or not value.is_finite() or value <= 0:
        raise BacktestError(f"{name} must be a positive decimal")
    return value


def _drag(fee_rate: object, spread: object, slippage: object) -> Decimal:
    drag = _cost(fee_rate) + _cost(spread) / 2 + _cost(slippage)
    if drag >= _ONE:
        raise BacktestError("cost drag must be below 1")
    return drag


def _cost(value: object) -> Decimal:
    if type(value) is not Decimal or not value.is_finite() or value < 0:
        raise BacktestError("cost must be a non-negative decimal")
    return value


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or value < 1:
        raise BacktestError(f"{name} must be a positive integer")
    return value


def _price(value: object, name: str) -> Decimal:
    if type(value) is not Decimal or not value.is_finite() or value <= 0:
        raise BacktestError(f"{name} must be a positive decimal")
    return value
