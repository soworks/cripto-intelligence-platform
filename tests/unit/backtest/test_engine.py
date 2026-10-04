from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from cip.backtest.engine import (
    build_report,
    load_symbol_bars,
    require_two_daily_returns,
    run_benchmarks,
    shared_window,
    simulate,
)
from cip.backtest.metrics import daily_returns, max_drawdown
from cip.domain.errors import BacktestError
from cip.history.bars import DailyBar
from cip.history.store import month_path, write_month

_POLICY = Path("policies/investment-policy.yaml")
_FEE = Decimal("0.00075")


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


def _write(root: Path, bars: tuple[DailyBar, ...]) -> None:
    by_month: dict[tuple[int, int], list[DailyBar]] = {}
    for bar in bars:
        by_month.setdefault((bar.open_date.year, bar.open_date.month), []).append(bar)
    for (year, month), month_bars in by_month.items():
        write_month(
            month_path(root, month_bars[0].symbol, year, month),
            tuple(month_bars),
            source_sha256="abc",
        )


def test_load_reads_only_the_named_symbol_in_date_order(tmp_path: Path) -> None:
    january = _bar("BTCUSDT", date(2020, 1, 31), "1")
    february = _bar("BTCUSDT", date(2020, 2, 1), "2")
    _write(tmp_path, (february, january))
    _write(tmp_path, (_bar("LUNAUSDT", date(2020, 1, 31), "9"),))

    loaded = load_symbol_bars(tmp_path, "BTCUSDT")

    assert [bar.open_date for bar in loaded] == [date(2020, 1, 31), date(2020, 2, 1)]
    assert {bar.symbol for bar in loaded} == {"BTCUSDT"}


def test_load_rejects_a_symbol_with_no_months(tmp_path: Path) -> None:
    with pytest.raises(BacktestError, match="BTCUSDT has no bars"):
        load_symbol_bars(tmp_path, "BTCUSDT")


def test_shared_window_rejects_a_missing_symbol() -> None:
    eth = (_bar("ETHUSDT", date(2020, 1, 1), "1"),)
    with pytest.raises(BacktestError, match="BTCUSDT has no bars"):
        shared_window((), eth)
    with pytest.raises(BacktestError, match="ETHUSDT has no bars"):
        shared_window((_bar("BTCUSDT", date(2020, 1, 1), "1"),), ())


def test_shared_window_rejects_a_repeated_date() -> None:
    day = date(2020, 1, 1)
    btc = (_bar("BTCUSDT", day, "1"), _bar("BTCUSDT", day, "1"))
    eth = (_bar("ETHUSDT", day, "1"),)
    with pytest.raises(BacktestError, match="BTCUSDT repeats"):
        shared_window(btc, eth)
    with pytest.raises(BacktestError, match="ETHUSDT repeats"):
        shared_window(
            (_bar("BTCUSDT", day, "1"),),
            (_bar("ETHUSDT", day, "1"), _bar("ETHUSDT", day, "1")),
        )


def test_shared_window_rejects_an_empty_overlap() -> None:
    with pytest.raises(BacktestError, match="share no dates"):
        shared_window(
            (_bar("BTCUSDT", date(2020, 1, 1), "1"),),
            (_bar("ETHUSDT", date(2020, 1, 2), "1"),),
        )


def test_shared_window_rejects_a_calendar_gap() -> None:
    first = date(2020, 1, 1)
    second = date(2020, 1, 2)
    third = date(2020, 1, 3)
    btc = (
        _bar("BTCUSDT", first, "1"),
        _bar("BTCUSDT", second, "1"),
        _bar("BTCUSDT", third, "1"),
    )
    eth_with_gap = (_bar("ETHUSDT", first, "1"), _bar("ETHUSDT", third, "1"))
    with pytest.raises(BacktestError, match="ETHUSDT is missing 2020-01-02"):
        shared_window(btc, eth_with_gap)

    btc_with_gap = (_bar("BTCUSDT", first, "1"), _bar("BTCUSDT", third, "1"))
    eth = (
        _bar("ETHUSDT", first, "1"),
        _bar("ETHUSDT", second, "1"),
        _bar("ETHUSDT", third, "1"),
    )
    with pytest.raises(BacktestError, match="BTCUSDT is missing 2020-01-02"):
        shared_window(btc_with_gap, eth)


def test_one_day_and_two_day_windows_are_rejected() -> None:
    day = date(2020, 1, 1)
    with pytest.raises(BacktestError, match="two daily returns"):
        require_two_daily_returns((day,))
    with pytest.raises(BacktestError, match="two daily returns"):
        require_two_daily_returns((day, day + timedelta(days=1)))
    require_two_daily_returns((day, day + timedelta(days=1), day + timedelta(days=2)))


def test_fee_is_taken_in_the_base_quantity() -> None:
    day = date(2020, 8, 17)
    bars = {"BTCUSDT": {day: _bar("BTCUSDT", day, "10", close="10")}}

    result = simulate(bars, (day,), ((day, Decimal("100")),), {"BTCUSDT": Decimal(1)}, _FEE)

    quantity = Decimal("100") * (1 - _FEE) / Decimal("10")
    assert result.equity == ((day, quantity * Decimal("10")),)
    assert result.fee_drag_usd == Decimal("100") * _FEE


def test_a_buy_date_missing_from_the_symbol_is_rejected() -> None:
    day = date(2020, 8, 17)
    missing = date(2020, 8, 18)
    bars = {"BTCUSDT": {day: _bar("BTCUSDT", day, "10")}}

    with pytest.raises(BacktestError, match="BTCUSDT has no bar on 2020-08-18"):
        simulate(
            bars,
            (day,),
            ((day, Decimal("100")), (missing, Decimal("100"))),
            {"BTCUSDT": Decimal(1)},
            _FEE,
        )


def test_a_non_positive_price_on_an_unheld_symbol_is_rejected() -> None:
    day = date(2020, 8, 17)
    bars = {
        "BTCUSDT": {day: _bar("BTCUSDT", day, "10")},
        "ETHUSDT": {day: _bar("ETHUSDT", day, "10", close="0")},
    }

    with pytest.raises(BacktestError, match="ETHUSDT price"):
        simulate(bars, (day,), ((day, Decimal("100")),), {"BTCUSDT": Decimal(1)}, _FEE)


def test_a_non_positive_open_is_rejected() -> None:
    day = date(2020, 8, 17)
    bars = {"BTCUSDT": {day: _bar("BTCUSDT", day, "0", close="10")}}

    with pytest.raises(BacktestError, match="must be positive"):
        simulate(bars, (day,), ((day, Decimal("100")),), {"BTCUSDT": Decimal(1)}, _FEE)


def test_a_price_drop_on_a_contribution_day_draws_down_the_index() -> None:
    day0 = date(2020, 8, 17)
    day1 = date(2020, 8, 18)
    bars = {
        "BTCUSDT": {
            day0: _bar("BTCUSDT", day0, "10", close="10"),
            day1: _bar("BTCUSDT", day1, "10", close="5"),
        }
    }
    schedule = ((day0, Decimal("100")), (day1, Decimal("100")))

    result = simulate(bars, (day0, day1), schedule, {"BTCUSDT": Decimal(1)}, _FEE)

    assert result.equity[0][1] == result.equity[1][1]
    returns = daily_returns(result.equity, {day0: Decimal("100"), day1: Decimal("100")})
    assert max_drawdown(returns) > 0


def test_a_contribution_day_below_cash_leaves_calmar_null() -> None:
    days = [date(2020, 1, 15) + timedelta(days=offset) for offset in range(20)]
    btc: dict[date, DailyBar] = {}
    for index, day in enumerate(days):
        if index == 1:
            btc[day] = _bar("BTCUSDT", day, "100", close="50")
        else:
            close = str(100 + index * 20)
            btc[day] = _bar("BTCUSDT", day, close, close=close)
    schedule = ((days[0], Decimal("650")), (days[1], Decimal("800")))

    book = simulate(
        {"BTCUSDT": btc},
        tuple(days),
        schedule,
        {"BTCUSDT": Decimal(1)},
        _FEE,
    )
    report = build_report("a" * 64, tuple(days), schedule, book, book)

    assert report["books"]["btc"]["calmar"] is None
    assert report["books"]["btc_eth"]["calmar"] is None


def test_a_later_month_start_is_invested(tmp_path: Path) -> None:
    days = [date(2020, 1, 31) + timedelta(days=offset) for offset in range(6)]
    prices = ("100", "110", "120", "130", "140", "150")
    _write(
        tmp_path,
        tuple(_bar("BTCUSDT", day, price) for day, price in zip(days, prices, strict=True)),
    )
    _write(
        tmp_path,
        tuple(_bar("ETHUSDT", day, price) for day, price in zip(days, prices, strict=True)),
    )

    report = run_benchmarks(tmp_path, _POLICY)

    assert report["contributed_usd"] == "1450.0"
    assert report["books"]["btc"]["equity"][1]["date"] == "2020-02-01"


def test_repository_policy_run_uses_the_full_opening_contribution(tmp_path: Path) -> None:
    days = [date(2020, 8, 17) + timedelta(days=offset) for offset in range(3)]
    btc_prices = ("100", "110", "130")
    eth_prices = ("50", "40", "40")
    _write(
        tmp_path,
        tuple(_bar("BTCUSDT", day, price) for day, price in zip(days, btc_prices, strict=True)),
    )
    _write(
        tmp_path,
        tuple(_bar("ETHUSDT", day, price) for day, price in zip(days, eth_prices, strict=True)),
    )

    report = run_benchmarks(tmp_path, _POLICY)

    assert len(report["policy_sha256"]) == 64
    assert report["start"] == "2020-08-17"
    assert report["end"] == "2020-08-19"
    assert report["contributed_usd"] == "650.0"
    btc = report["books"]["btc"]
    mixed = report["books"]["btc_eth"]
    assert btc["fee_drag_usd"] == mixed["fee_drag_usd"] == format(Decimal("650.0") * _FEE, "f")
    assert btc["sortino"] is None
    assert btc["calmar"] is None
    assert mixed["beta"] is not None
    assert list(btc) == [
        "equity",
        "twr",
        "irr",
        "sharpe",
        "sortino",
        "max_drawdown",
        "calmar",
        "fee_drag_usd",
    ]
    assert list(mixed)[-3:] == ["excess_twr", "beta", "alpha"]


def test_a_short_parquet_window_is_rejected(tmp_path: Path) -> None:
    day = date(2020, 8, 17)
    _write(tmp_path, (_bar("BTCUSDT", day, "1"),))
    _write(tmp_path, (_bar("ETHUSDT", day, "1"),))

    with pytest.raises(BacktestError, match="two daily returns"):
        run_benchmarks(tmp_path, _POLICY)
