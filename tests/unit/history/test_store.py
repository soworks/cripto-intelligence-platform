from datetime import date
from decimal import Decimal
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from cip.domain.errors import HistoryError
from cip.history.bars import DailyBar
from cip.history.store import (
    load_bars,
    month_path,
    read_month,
    read_source_sha256,
    write_month,
)


def _bar(open_date: date, close: str) -> DailyBar:
    value = Decimal(close)
    return DailyBar(
        symbol="LUNAUSDT",
        open_date=open_date,
        open=value,
        high=value,
        low=value,
        close=value,
        volume=Decimal("1.00000000"),
        quote_volume=Decimal("2.00000000"),
        trade_count=3,
        taker_buy_base_volume=Decimal("0.50000000"),
        taker_buy_quote_volume=Decimal("1.00000000"),
    )


def test_month_path_uses_partitioned_daily_usdt_layout(tmp_path: Path) -> None:
    assert month_path(tmp_path, "LUNAUSDT", 2022, 5) == (
        tmp_path
        / "klines"
        / "interval=1d"
        / "quote=USDT"
        / "symbol=LUNAUSDT"
        / "year=2022"
        / "month=05"
        / "part.parquet"
    )


def test_month_round_trip_preserves_bars_and_source_hash(tmp_path: Path) -> None:
    path = month_path(tmp_path, "LUNAUSDT", 2022, 5)
    bars = (
        _bar(date(2022, 5, 13), "0.00005000"),
        _bar(date(2022, 5, 31), "8.87000000"),
    )

    write_month(path, bars, source_sha256="abc")

    restored = read_month(path)
    assert [bar.close for bar in restored] == [
        Decimal("0.00005000"),
        Decimal("8.87000000"),
    ]
    assert {bar.symbol for bar in restored} == {"LUNAUSDT"}
    assert read_source_sha256(path) == "abc"


def test_load_bars_concatenates_months_in_date_order(tmp_path: Path) -> None:
    may_path = month_path(tmp_path, "LUNAUSDT", 2022, 5)
    june_path = month_path(tmp_path, "LUNAUSDT", 2022, 6)
    may = _bar(date(2022, 5, 31), "8.87000000")
    june = _bar(date(2022, 6, 1), "7.00000000")
    write_month(june_path, (june,), source_sha256="june")
    write_month(may_path, (may,), source_sha256="may")

    loaded = load_bars(tmp_path)

    assert loaded == {"LUNAUSDT": (may, june)}


def test_write_month_rejects_empty_bars_without_creating_file(tmp_path: Path) -> None:
    path = month_path(tmp_path, "LUNAUSDT", 2022, 5)

    with pytest.raises(HistoryError):
        write_month(path, (), source_sha256="abc")

    assert not path.exists()


def test_missing_source_hash_is_none(tmp_path: Path) -> None:
    assert read_source_sha256(tmp_path / "missing.parquet") is None


@pytest.mark.parametrize("metadata", [None, {b"other": b"value"}])
def test_existing_file_without_source_hash_is_none(
    tmp_path: Path, metadata: dict[bytes, bytes] | None
) -> None:
    path = tmp_path / "bars.parquet"
    table = pa.table({"value": [1]}).replace_schema_metadata(metadata)
    pq.write_table(table, path)

    assert read_source_sha256(path) is None
