from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from cip.backtest.metrics import (
    beta_alpha,
    calmar,
    cash_flows,
    daily_returns,
    max_drawdown,
    sharpe,
    solve_xirr,
    sortino,
    time_weighted_return,
)
from cip.backtest.schedule import allocate, contribution_schedule
from cip.domain.errors import BacktestError
from cip.domain.policy import load_policy
from cip.history.bars import DailyBar
from cip.history.store import read_month

_BTC_WEIGHTS: dict[str, Decimal] = {"BTCUSDT": Decimal(1)}


@dataclass(frozen=True)
class BookResult:
    equity: tuple[tuple[date, Decimal], ...]
    fee_drag_usd: Decimal


def load_symbol_bars(root: Path, symbol: str) -> tuple[DailyBar, ...]:
    pattern = f"klines/interval=1d/quote=USDT/symbol={symbol}/year=*/month=*/part.parquet"
    bars: list[DailyBar] = []
    for path in sorted(root.glob(pattern)):
        bars.extend(read_month(path))
    if not bars:
        raise BacktestError(f"{symbol} has no bars")
    return tuple(sorted(bars, key=lambda bar: bar.open_date))


def shared_window(btc: tuple[DailyBar, ...], eth: tuple[DailyBar, ...]) -> tuple[date, ...]:
    btc_dates = _unique_dates("BTCUSDT", btc)
    eth_dates = _unique_dates("ETHUSDT", eth)
    if not btc_dates:
        raise BacktestError("BTCUSDT has no bars")
    if not eth_dates:
        raise BacktestError("ETHUSDT has no bars")
    shared = btc_dates & eth_dates
    if not shared:
        raise BacktestError("BTCUSDT and ETHUSDT share no dates")
    first, last = min(shared), max(shared)
    days: list[date] = []
    cursor = first
    while cursor <= last:
        if cursor not in btc_dates:
            raise BacktestError(f"BTCUSDT is missing {cursor.isoformat()}")
        if cursor not in eth_dates:
            raise BacktestError(f"ETHUSDT is missing {cursor.isoformat()}")
        days.append(cursor)
        cursor += timedelta(days=1)
    return tuple(days)


def require_two_daily_returns(window: Sequence[date]) -> None:
    if len(window) < 3:
        raise BacktestError("backtest needs at least two daily returns")


def simulate(
    bars_by_symbol: Mapping[str, Mapping[date, DailyBar]],
    window: Sequence[date],
    schedule: Sequence[tuple[date, Decimal]],
    weights: Mapping[str, Decimal],
    fee_rate: Decimal,
) -> BookResult:
    for day, _amount in schedule:
        for symbol in weights:
            if day not in bars_by_symbol[symbol]:
                raise BacktestError(f"{symbol} has no bar on {day.isoformat()}")
    positions: dict[str, Decimal] = {}
    fee_drag = Decimal(0)
    equity: list[tuple[date, Decimal]] = []
    scheduled = dict(schedule)
    for day in window:
        for symbol, by_date in bars_by_symbol.items():
            bar = by_date[day]
            if bar.open <= 0 or bar.close <= 0:
                raise BacktestError(f"{symbol} price on {day.isoformat()} must be positive")
        contribution = scheduled.get(day)
        if contribution is not None:
            for symbol, notional in allocate(contribution, weights).items():
                opened = bars_by_symbol[symbol][day].open
                positions[symbol] = (
                    positions.get(symbol, Decimal(0)) + notional * (1 - fee_rate) / opened
                )
            fee_drag += contribution * fee_rate
        marked = sum(
            (
                quantity * bars_by_symbol[symbol][day].close
                for symbol, quantity in positions.items()
            ),
            start=Decimal(0),
        )
        equity.append((day, marked))
    return BookResult(equity=tuple(equity), fee_drag_usd=fee_drag)


def run_benchmarks(klines: Path, policy_path: Path) -> dict[str, Any]:
    loaded = load_policy(policy_path)
    policy = loaded.policy
    btc = load_symbol_bars(klines, "BTCUSDT")
    eth = load_symbol_bars(klines, "ETHUSDT")
    window = shared_window(btc, eth)
    require_two_daily_returns(window)
    starting = Decimal(str(policy.portfolio.starting_value_usd))
    monthly = Decimal(str(policy.portfolio.monthly_contribution_usd))
    schedule = contribution_schedule(window[0], window[-1], starting, monthly)
    fee = Decimal(str(policy.venue.taker_fee_rate))
    indexed = {"BTCUSDT": _index(btc), "ETHUSDT": _index(eth)}
    mix = {symbol: Decimal(str(weight)) for symbol, weight in policy.portfolio.core_mix.items()}
    btc_book = simulate(indexed, window, schedule, _BTC_WEIGHTS, fee)
    mixed_book = simulate(indexed, window, schedule, mix, fee)
    return build_report(loaded.version, window, schedule, btc_book, mixed_book)


def build_report(
    policy_sha256: str,
    window: Sequence[date],
    schedule: Sequence[tuple[date, Decimal]],
    btc: BookResult,
    mixed: BookResult,
) -> dict[str, Any]:
    contributions = dict(schedule)
    btc_returns = daily_returns(btc.equity, contributions)
    mixed_returns = daily_returns(mixed.equity, contributions)
    span_days = (window[-1] - window[0]).days
    btc_twr = time_weighted_return(btc_returns)
    mixed_twr = time_weighted_return(mixed_returns)
    beta, alpha = beta_alpha(mixed_returns, btc_returns)
    contributed = sum((amount for _day, amount in schedule), start=Decimal(0))
    return {
        "policy_sha256": policy_sha256,
        "start": window[0].isoformat(),
        "end": window[-1].isoformat(),
        "contributed_usd": format(contributed, "f"),
        "books": {
            "btc": _book_json(btc, btc_returns, schedule, span_days, btc_twr),
            "btc_eth": {
                **_book_json(mixed, mixed_returns, schedule, span_days, mixed_twr),
                "excess_twr": format(mixed_twr - btc_twr, "f"),
                "beta": _num(beta),
                "alpha": _num(alpha),
            },
        },
    }


def _book_json(
    book: BookResult,
    returns: tuple[Decimal, ...],
    schedule: Sequence[tuple[date, Decimal]],
    span_days: int,
    twr: Decimal,
) -> dict[str, Any]:
    drawdown = max_drawdown(returns)
    final_day, final_equity = book.equity[-1]
    return {
        "equity": [
            {"date": day.isoformat(), "equity_usd": format(value, "f")}
            for day, value in book.equity
        ],
        "twr": format(twr, "f"),
        "irr": format(solve_xirr(cash_flows(schedule, final_day, final_equity)), "f"),
        "sharpe": _num(sharpe(returns)),
        "sortino": _num(sortino(returns)),
        "max_drawdown": format(drawdown, "f"),
        "calmar": _num(calmar(twr, drawdown, span_days)),
        "fee_drag_usd": format(book.fee_drag_usd, "f"),
    }


def _index(bars: Sequence[DailyBar]) -> dict[date, DailyBar]:
    return {bar.open_date: bar for bar in bars}


def _unique_dates(symbol: str, bars: Sequence[DailyBar]) -> set[date]:
    found = [bar.open_date for bar in bars]
    if len(found) != len(set(found)):
        raise BacktestError(f"{symbol} repeats an open date")
    return set(found)


def _num(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")
