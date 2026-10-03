from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import cast

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from cip.domain.errors import HistoryError
from cip.history.bars import DailyBar

_SOURCE_SHA256_KEY = b"cip.source_sha256"
_DECIMAL = pa.decimal128(38, 8)
_SCHEMA = pa.schema(
    [
        pa.field("symbol", pa.string()),
        pa.field("open_date", pa.date32()),
        pa.field("open", _DECIMAL),
        pa.field("high", _DECIMAL),
        pa.field("low", _DECIMAL),
        pa.field("close", _DECIMAL),
        pa.field("volume", _DECIMAL),
        pa.field("quote_volume", _DECIMAL),
        pa.field("trade_count", pa.int64()),
        pa.field("taker_buy_base_volume", _DECIMAL),
        pa.field("taker_buy_quote_volume", _DECIMAL),
    ]
)


def month_path(root: Path, symbol: str, year: int, month: int) -> Path:
    return (
        root
        / "klines"
        / "interval=1d"
        / "quote=USDT"
        / f"symbol={symbol}"
        / f"year={year:04d}"
        / f"month={month:02d}"
        / "part.parquet"
    )


def write_month(path: Path, bars: tuple[DailyBar, ...], *, source_sha256: str) -> None:
    if not bars:
        raise HistoryError("cannot write an empty kline month")
    schema = _SCHEMA.with_metadata({_SOURCE_SHA256_KEY: source_sha256.encode()})
    table = pa.Table.from_pylist(
        [
            {
                "symbol": bar.symbol,
                "open_date": bar.open_date,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "quote_volume": bar.quote_volume,
                "trade_count": bar.trade_count,
                "taker_buy_base_volume": bar.taker_buy_base_volume,
                "taker_buy_quote_volume": bar.taker_buy_quote_volume,
            }
            for bar in bars
        ],
        schema=schema,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    pq.write_table(table, temporary)
    temporary.replace(path)


def read_month(path: Path) -> tuple[DailyBar, ...]:
    rows = pq.ParquetFile(path).read().to_pylist()
    return tuple(
        DailyBar(
            symbol=cast(str, row["symbol"]),
            open_date=cast(date, row["open_date"]),
            open=cast(Decimal, row["open"]),
            high=cast(Decimal, row["high"]),
            low=cast(Decimal, row["low"]),
            close=cast(Decimal, row["close"]),
            volume=cast(Decimal, row["volume"]),
            quote_volume=cast(Decimal, row["quote_volume"]),
            trade_count=cast(int, row["trade_count"]),
            taker_buy_base_volume=cast(Decimal, row["taker_buy_base_volume"]),
            taker_buy_quote_volume=cast(Decimal, row["taker_buy_quote_volume"]),
        )
        for row in rows
    )


def read_source_sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    metadata = cast(dict[bytes, bytes], pq.ParquetFile(path).schema_arrow.metadata)
    return metadata[_SOURCE_SHA256_KEY].decode()


def load_bars(root: Path) -> dict[str, tuple[DailyBar, ...]]:
    by_symbol: defaultdict[str, list[DailyBar]] = defaultdict(list)
    pattern = "klines/interval=1d/quote=USDT/symbol=*/year=*/month=*/part.parquet"
    for path in root.glob(pattern):
        for bar in read_month(path):
            by_symbol[bar.symbol].append(bar)
    return {
        symbol: tuple(sorted(bars, key=lambda bar: bar.open_date))
        for symbol, bars in by_symbol.items()
    }
