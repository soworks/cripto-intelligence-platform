"""Raw liquidity observations. A short sample stays missing."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from cip.adapters.market import Depth, DepthLevel
from cip.domain.errors import EvaluationError
from cip.evaluation.features import _median
from cip.evaluation.liquidity_capture import (
    book_metrics,
    closed_hour_closes,
    derive_liquidity,
    parse_daily_klines,
    peg_reading,
    quote_window,
    store_liquidity,
    volume_share,
)
from cip.history.bars import DailyBar

SESSION = date(2026, 10, 7)
WHEN = datetime(2026, 10, 7, 15, 0, tzinfo=UTC)
END = date(2026, 10, 6)


def _bar(day: date, quote: str, *, close: str = "10", trades: int = 2) -> DailyBar:
    return DailyBar(
        symbol="BTCUSDT",
        open_date=day,
        open=Decimal("10"),
        high=Decimal("11"),
        low=Decimal("9"),
        close=Decimal(close),
        volume=Decimal("1"),
        quote_volume=Decimal(quote),
        trade_count=trades,
        taker_buy_base_volume=Decimal("1"),
        taker_buy_quote_volume=Decimal("1"),
    )


def _series(count: int, *, end: date = END) -> tuple[DailyBar, ...]:
    start = end - timedelta(days=count - 1)
    return tuple(
        _bar(start + timedelta(days=offset), str(100 + offset), close=str(10 + offset))
        for offset in range(count)
    )


def test_thirty_consecutive_days_keep_the_feature_median_and_the_minimum() -> None:
    bars = _series(31)
    window = quote_window(bars, SESSION)
    assert window is not None
    assert len(window) == 31
    quotes = [bar.quote_volume for bar in window[-30:]]
    derived = derive_liquidity(bars, SESSION)
    assert derived.median_quote_volume_30d_usd == _median(quotes)
    assert derived.day_quote_volume_usd == min(quotes)
    assert derived.taker_buy_ratio is None
    assert derived.spike_candle_count is None
    assert derived.price_move == (bars[-1].close / bars[-2].close) - 1


def test_a_short_or_gapped_window_does_not_become_a_median() -> None:
    short = derive_liquidity(_series(29), SESSION)
    assert short.median_quote_volume_30d_usd is None
    assert short.day_quote_volume_usd is None
    assert short.volume_zscore is None
    assert short.trade_size_stdev is None
    gapped = list(_series(31))
    gapped.pop(3)
    assert quote_window(tuple(gapped), SESSION) is not None
    broken = derive_liquidity(tuple(gapped), SESSION)
    assert broken.median_quote_volume_30d_usd is None
    session_bar = _bar(SESSION, "999")
    held = derive_liquidity((*_series(31), session_bar), SESSION)
    fresh = derive_liquidity(_series(31), SESSION)
    assert held.median_quote_volume_30d_usd == fresh.median_quote_volume_30d_usd


def test_a_flat_baseline_and_a_zero_trade_count_stay_missing() -> None:
    flat = tuple(_bar(END - timedelta(days=30 - offset), "10") for offset in range(31))
    # rebuild in date order
    ordered = tuple(sorted(flat, key=lambda bar: bar.open_date))
    derived = derive_liquidity(ordered, SESSION)
    assert derived.volume_zscore is None
    assert derived.trade_size_stdev is None
    quiet = list(_series(31))
    quiet[-1] = _bar(END, "130", trades=0)
    assert derive_liquidity(tuple(quiet), SESSION).trade_size_stdev is None
    assert derive_liquidity(tuple(quiet), SESSION).volume_zscore is not None
    small = list(_series(31))
    small[-1] = _bar(END, "1")
    below = derive_liquidity(tuple(small), SESSION)
    assert below.trade_size_stdev is not None
    assert below.trade_size_stdev > 0
    assert below.volume_zscore is not None
    assert below.volume_zscore < 0


def test_one_book_is_one_snapshot_and_an_empty_book_is_missing() -> None:
    book = Depth(
        last_update_id=1,
        bids=(DepthLevel(Decimal("100"), Decimal("2")),),
        asks=(DepthLevel(Decimal("101"), Decimal("3")),),
    )
    reading = book_metrics(book, band=Decimal("0.02"), observed_at=WHEN)
    assert reading is not None
    assert reading.snapshots == 1
    assert reading.spread_bps > 0
    assert reading.depth_usd_per_side == min(reading.bid_usd, reading.ask_usd)
    assert (
        book_metrics(
            Depth(last_update_id=1, bids=(), asks=()), band=Decimal("0.02"), observed_at=WHEN
        )
        is None
    )


def test_share_and_turnover_stay_missing_without_a_usable_denominator() -> None:
    assert volume_share(Decimal("40"), Decimal("100")) == Decimal("0.4")
    assert volume_share(Decimal("150"), Decimal("100")) is None
    assert volume_share(Decimal("10"), Decimal("0")) is None
    derived = derive_liquidity(
        _series(31), SESSION, binance_quote_volume=Decimal("50"), market_cap=Decimal("200")
    )
    assert derived.turnover == Decimal("0.25")
    missing = derive_liquidity(
        _series(31), SESSION, binance_quote_volume=Decimal("50"), market_cap=Decimal("0")
    )
    assert missing.turnover is None


def test_peg_hours_count_only_closed_hours_beyond_the_policy_band() -> None:
    assert peg_reading((), limit=Decimal("0.005")) is None
    inside = peg_reading((Decimal("1.001"),), limit=Decimal("0.005"))
    assert inside == (Decimal("0.001"), 0)
    beyond = peg_reading((Decimal("1"), Decimal("1.01"), Decimal("1.02")), limit=Decimal("0.005"))
    assert beyond == (Decimal("0.02"), 2)
    hour = 3_600_000
    opened = int(datetime(2026, 10, 7, 12, tzinfo=UTC).timestamp() * 1000)
    payload = [
        [opened, "1", "1", "1", "1.010", "1", opened + hour - 1, "1", 1],
        [opened + hour, "1", "1", "1", "1.020", "1", opened + 2 * hour - 1, "1", 1],
    ]
    during = datetime(2026, 10, 7, 13, 30, tzinfo=UTC)
    assert closed_hour_closes(payload, captured_at=during) == (Decimal("1.010"),)
    finished = datetime(2026, 10, 7, 16, tzinfo=UTC)
    third = [opened + 2 * hour, "1", "1", "1", "1.000", "1", opened + 3 * hour - 1, "1", 1]
    assert closed_hour_closes([*payload, third], captured_at=finished) == (
        Decimal("1.010"),
        Decimal("1.020"),
        Decimal("1.000"),
    )


def test_malformed_observations_stay_unusable(tmp_path: Path) -> None:
    assert quote_window((_bar(SESSION, "1"),), SESSION) is None
    assert quote_window(_series(5, end=date(2026, 10, 5)), SESSION) is None
    duplicate = (_bar(END, "10"), _bar(END, "11"))
    with pytest.raises(EvaluationError, match="unusable"):
        quote_window(duplicate, SESSION)
    with pytest.raises(EvaluationError, match="unusable"):
        volume_share(Decimal("-1"), Decimal("10"))
    with pytest.raises(EvaluationError, match="unusable"):
        derive_liquidity(
            _series(2),
            SESSION,
            binance_quote_volume=Decimal("-1"),
            market_cap=Decimal("5"),
        )
    zero_close = list(_series(2))
    zero_close[-2] = _bar(END - timedelta(days=1), "10", close="0")
    assert derive_liquidity(tuple(zero_close), SESSION).price_move is None
    quiet = list(_series(31))
    quiet[-2] = _bar(END - timedelta(days=1), "10", trades=0)
    assert derive_liquidity(tuple(quiet), SESSION).trade_size_stdev is None
    assert peg_reading((Decimal("1.01"), Decimal("1.02")), limit=Decimal("0.005")) == (
        Decimal("0.02"),
        2,
    )
    opened = int(datetime(2026, 10, 6, tzinfo=UTC).timestamp() * 1000)
    row = [opened, "10", "11", "9", "10", "1", opened + 86_400_000 - 1, "100", 2, "1", "4", "0"]
    parsed = parse_daily_klines([row], symbol="BTCUSDT")
    assert parsed[0].taker_buy_quote_volume == Decimal("4")
    negative = row.copy()
    negative[10] = "-1"
    with pytest.raises(EvaluationError, match="unusable"):
        parse_daily_klines([negative], symbol="BTCUSDT")
    with pytest.raises(EvaluationError, match="unusable"):
        parse_daily_klines([row], symbol="btc")
    with pytest.raises(EvaluationError, match="unusable"):
        parse_daily_klines("nope", symbol="BTCUSDT")
    with pytest.raises(EvaluationError, match="unusable"):
        parse_daily_klines(["row"], symbol="BTCUSDT")
    with pytest.raises(EvaluationError, match="unusable"):
        parse_daily_klines([row[:9]], symbol="BTCUSDT")
    bad_taker = row.copy()
    bad_taker[9] = 1
    with pytest.raises(EvaluationError, match="unusable"):
        parse_daily_klines([bad_taker], symbol="BTCUSDT")
    infinite = row.copy()
    infinite[10] = "Infinity"
    with pytest.raises(EvaluationError, match="unusable"):
        parse_daily_klines([infinite], symbol="BTCUSDT")
    text = row.copy()
    text[10] = "nope"
    with pytest.raises(EvaluationError, match="unusable"):
        parse_daily_klines([text], symbol="BTCUSDT")
    with pytest.raises(EvaluationError, match="unusable"):
        closed_hour_closes({"hour": 1}, captured_at=WHEN)
    with pytest.raises(EvaluationError, match="unusable"):
        closed_hour_closes([[1, "1", "1", "1", "1"]], captured_at=WHEN)
    with pytest.raises(EvaluationError, match="unusable"):
        closed_hour_closes([[1, "1", "1", "1", "1", "1", "1"]], captured_at=WHEN)
    with pytest.raises(EvaluationError, match="unusable"):
        closed_hour_closes([[1, "1", "1", "1", "-1", "1", 0]], captured_at=WHEN)
    refused = (
        _capture(symbol="btc"),
        _capture(taker_buy_ratio=Decimal("0.5")),
        _capture(spike_candle_count=1),
        _capture(
            spread_bps=(),
            median_spread_bps=Decimal("1"),
            spread_snapshots=1,
            depth_usd_per_side=None,
            bid_usd=None,
            ask_usd=None,
        ),
        _capture(
            spread_bps=(),
            median_spread_bps=None,
            spread_snapshots=None,
            depth_usd_per_side=Decimal("1"),
            bid_usd=None,
            ask_usd=None,
        ),
        _capture(median_spread_bps=Decimal("0")),
        _capture(spread_snapshots=2),
        _capture(bid_usd=None),
        _capture(depth_usd_per_side=Decimal("1")),
        _capture(peg_deviation_hours=None),
        _capture(bar_captured_at=datetime(2026, 10, 7, 15, 0)),  # noqa: DTZ001
        _capture(bar_captured_at=datetime(2026, 10, 8, 0, 0, 1, tzinfo=UTC)),
    )
    for item in refused:
        with pytest.raises(EvaluationError):
            store_liquidity(tmp_path, SESSION, item)


def test_a_partial_candle_an_undated_input_and_a_peg_without_closes_are_refused(
    tmp_path: Path,
) -> None:
    opened = int(datetime(2026, 10, 6, tzinfo=UTC).timestamp() * 1000)
    day = 86_400_000
    hour = 3_600_000
    row = [opened, "10", "11", "9", "10", "1", opened + day - 1, "100", 2, "1", "4", "0"]
    partial = row.copy()
    partial[6] = opened + hour - 1
    with pytest.raises(EvaluationError, match="unusable"):
        parse_daily_klines([partial], symbol="BTCUSDT")
    shifted = row.copy()
    shifted[0] = opened + 1
    shifted[6] = opened + day
    with pytest.raises(EvaluationError, match="unusable"):
        parse_daily_klines([shifted], symbol="BTCUSDT")
    start = int(datetime(2026, 10, 7, 10, tzinfo=UTC).timestamp() * 1000)
    wide = [start, "1", "1", "1", "1", "1", start + 2 * hour - 1, "1", 1]
    with pytest.raises(EvaluationError, match="unusable"):
        closed_hour_closes([wide], captured_at=WHEN)
    first = [start, "1", "1", "1", "1.01", "1", start + hour - 1, "1", 1]
    later = [start + 2 * hour, "1", "1", "1", "1.02", "1", start + 3 * hour - 1, "1", 1]
    with pytest.raises(EvaluationError, match="unusable"):
        closed_hour_closes([first, later], captured_at=WHEN)
    text_open = ["1", "1", "1", "1", "1", "1", start + hour - 1]
    with pytest.raises(EvaluationError, match="unusable"):
        closed_hour_closes([text_open], captured_at=WHEN)
    huge = 8_640_000_000_000_000
    with pytest.raises(EvaluationError, match="unusable"):
        closed_hour_closes(
            [[huge, "1", "1", "1", "1", "1", huge + hour - 1, "1", 1]],
            captured_at=WHEN,
        )
    with pytest.raises(EvaluationError, match="session bar was retrieved before it closed"):
        store_liquidity(tmp_path, SESSION, _capture(bars=(*_series(31), _bar(SESSION, "9"))))
    with pytest.raises(EvaluationError, match="capture is after the close"):
        store_liquidity(tmp_path, SESSION, _capture(bars=(_bar(date(2026, 10, 8), "9"),)))
    early = datetime(2026, 10, 6, 23, tzinfo=UTC)
    with pytest.raises(EvaluationError, match="bar was retrieved before it closed"):
        store_liquidity(tmp_path, SESSION, _capture(bar_captured_at=early))
    after = datetime(2026, 10, 8, 0, 0, 1, tzinfo=UTC)
    with pytest.raises(EvaluationError, match="capture is after the close"):
        store_liquidity(tmp_path, SESSION, _capture(book_observed_at=after))
    for undated in (
        {"book_observed_at": None},
        {"ticker_captured_at": None},
        {"market_source_timestamp": None},
        {"peg_captured_at": None},
    ):
        with pytest.raises(EvaluationError, match="undated observation"):
            store_liquidity(tmp_path, SESSION, _capture(**undated))
    with pytest.raises(EvaluationError, match="unusable"):
        store_liquidity(tmp_path, SESSION, _capture(hourly_closes=(), peg_limit=None))
    with pytest.raises(EvaluationError, match="unusable"):
        store_liquidity(tmp_path, SESSION, _capture(peg_limit=Decimal("-1")))
    with pytest.raises(EvaluationError, match="unusable"):
        store_liquidity(
            tmp_path,
            SESSION,
            _capture(
                stablecoin_peg_deviation=None,
                peg_deviation_hours=None,
                hourly_closes=(Decimal("1.001"),),
            ),
        )
    with pytest.raises(EvaluationError, match="does not match"):
        store_liquidity(
            tmp_path,
            SESSION,
            _capture(stablecoin_peg_deviation=Decimal("0.5"), peg_deviation_hours=4),
        )
    store_liquidity(
        tmp_path,
        SESSION,
        _capture(
            symbol="SOLUSDT",
            bars=(),
            median_quote_volume_30d_usd=None,
            day_quote_volume_usd=None,
            volume_zscore=None,
            price_move=None,
            trade_size_stdev=None,
        ),
    )
    store_liquidity(
        tmp_path,
        SESSION,
        _capture(
            symbol="ADAUSDT",
            spread_bps=(),
            median_spread_bps=None,
            spread_snapshots=None,
            bid_usd=None,
            ask_usd=None,
            depth_usd_per_side=None,
        ),
    )
    store_liquidity(
        tmp_path,
        SESSION,
        _capture(
            symbol="XRPUSDT",
            stablecoin_peg_deviation=None,
            peg_deviation_hours=None,
            hourly_closes=(),
            peg_limit=None,
            peg_captured_at=None,
        ),
    )


def test_a_capture_that_appears_during_the_write_is_kept_or_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = _capture(symbol="ETHUSDT")

    def same(source: str, destination: str) -> None:
        Path(destination).write_bytes(Path(source).read_bytes())
        raise FileExistsError

    monkeypatch.setattr("cip.evaluation.liquidity_capture.os.link", same)
    store_liquidity(tmp_path, SESSION, item)
    monkeypatch.undo()

    def different(source: str, destination: str) -> None:
        del source
        Path(destination).write_bytes(b"{}")
        raise FileExistsError

    monkeypatch.setattr("cip.evaluation.liquidity_capture.os.link", different)
    with pytest.raises(EvaluationError, match="different payload"):
        store_liquidity(tmp_path / "other", SESSION, item)


def test_a_sealed_session_does_not_receive_liquidity(tmp_path: Path) -> None:
    item = _capture()
    with pytest.raises(EvaluationError, match="sealed session stays sealed"):
        store_liquidity(tmp_path, date(2026, 10, 6), item)
    manifest = tmp_path / "sessions" / "date=2026-10-07" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}")
    with pytest.raises(EvaluationError, match="finalized session is sealed"):
        store_liquidity(tmp_path, SESSION, item)


def test_storage_keeps_identical_bytes_and_refuses_a_rewritten_median(tmp_path: Path) -> None:
    item = _capture()
    store_liquidity(tmp_path, SESSION, item)
    store_liquidity(tmp_path, SESSION, item)
    store_liquidity(
        tmp_path,
        SESSION,
        _capture(
            symbol="BNBUSDT",
            spread_bps=(),
            median_spread_bps=None,
            spread_snapshots=None,
            bid_usd=None,
            ask_usd=None,
            depth_usd_per_side=None,
            book_observed_at=None,
        ),
    )
    later = WHEN + timedelta(seconds=1)
    with pytest.raises(EvaluationError, match="different payload"):
        store_liquidity(tmp_path, SESSION, _capture(ticker_captured_at=later))
    rewritten = derive_liquidity(_series(31), SESSION)
    assert rewritten.median_quote_volume_30d_usd is not None
    with pytest.raises(EvaluationError, match="does not match"):
        store_liquidity(
            tmp_path,
            SESSION,
            _capture(median_quote_volume_30d_usd=rewritten.median_quote_volume_30d_usd + 1),
        )


def _capture(**overrides: object) -> object:
    from cip.evaluation.liquidity_capture import LiquidityCapture

    derived = derive_liquidity(
        _series(31),
        SESSION,
        binance_quote_volume=Decimal("50"),
        market_cap=Decimal("200"),
        aggregate_volume=Decimal("80"),
    )
    book = book_metrics(
        Depth(
            last_update_id=1,
            bids=(DepthLevel(Decimal("100"), Decimal("2")),),
            asks=(DepthLevel(Decimal("101"), Decimal("3")),),
        ),
        band=Decimal("0.02"),
        observed_at=WHEN,
    )
    assert book is not None
    body: dict[str, object] = {
        "symbol": "BTCUSDT",
        "bars": _series(31),
        "median_quote_volume_30d_usd": derived.median_quote_volume_30d_usd,
        "day_quote_volume_usd": derived.day_quote_volume_usd,
        "spread_bps": (book.spread_bps,),
        "median_spread_bps": book.spread_bps,
        "spread_snapshots": 1,
        "bid_usd": book.bid_usd,
        "ask_usd": book.ask_usd,
        "depth_usd_per_side": book.depth_usd_per_side,
        "binance_quote_volume_24h": Decimal("50"),
        "market_cap_usd": Decimal("200"),
        "aggregate_volume_usd": Decimal("80"),
        "turnover": derived.turnover,
        "volume_zscore": derived.volume_zscore,
        "price_move": derived.price_move,
        "taker_buy_ratio": None,
        "spike_candle_count": None,
        "trade_size_stdev": derived.trade_size_stdev,
        "binance_volume_share": derived.binance_volume_share,
        "stablecoin_peg_deviation": Decimal("0.001"),
        "peg_deviation_hours": 0,
        "hourly_closes": (Decimal("1.001"),),
        "peg_limit": Decimal("0.005"),
        "bar_captured_at": WHEN,
        "book_observed_at": WHEN,
        "ticker_captured_at": WHEN,
        "market_source_timestamp": WHEN,
        "peg_captured_at": WHEN,
    }
    body.update(overrides)
    return LiquidityCapture(**body)  # type: ignore[arg-type]
