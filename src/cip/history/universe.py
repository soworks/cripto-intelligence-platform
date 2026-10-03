from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import cast

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from cip.domain.errors import HistoryError
from cip.history.bars import DailyBar
from cip.history.continuity import Continuity

_SCHEMA = pa.schema(
    [
        pa.field("symbol", pa.string()),
        pa.field("quote_asset", pa.string()),
        pa.field("first_open_date", pa.date32()),
        pa.field("last_open_date", pa.date32()),
        pa.field("bar_count", pa.int64()),
        pa.field("first_discontinuous_date", pa.date32()),
        pa.field("successor", pa.string()),
        pa.field("predecessor", pa.string()),
        pa.field("price_scale", pa.int64()),
    ]
)


@dataclass(frozen=True)
class Listing:
    symbol: str
    quote_asset: str
    first_open_date: date
    last_open_date: date
    bar_count: int
    first_discontinuous_date: date | None
    successor: str | None
    predecessor: str | None
    price_scale: int


def build_listings(
    bars: dict[str, tuple[DailyBar, ...]], continuity: Continuity
) -> tuple[Listing, ...]:
    breaks = {item.symbol: item for item in continuity.breaks}
    predecessor_renames = {item.predecessor: item for item in continuity.renames}
    successor_renames = {item.successor: item for item in continuity.renames}
    listings: list[Listing] = []
    for symbol in sorted(bars):
        symbol_bars = bars[symbol]
        dates = [bar.open_date for bar in symbol_bars]
        first_open_date = min(dates)
        last_open_date = max(dates)
        continuity_break = breaks.get(symbol)
        predecessor_rename = predecessor_renames.get(symbol)
        successor_rename = successor_renames.get(symbol)
        if continuity_break is not None and continuity_break.first_discontinuous_date not in dates:
            raise HistoryError(f"{symbol} is missing its first discontinuous date")
        if (
            predecessor_rename is not None
            and last_open_date != predecessor_rename.predecessor_last_date
        ):
            raise HistoryError(f"{symbol} has an unexpected last open date")
        if (
            successor_rename is not None
            and first_open_date != successor_rename.successor_first_date
        ):
            raise HistoryError(f"{symbol} has an unexpected first open date")
        matching_scales = [
            scale for scale in continuity.scales if re.search(scale.pattern, symbol) is not None
        ]
        if len(matching_scales) > 1:
            raise HistoryError(f"{symbol} matches multiple price scales")
        price_scale = matching_scales[0].price_scale if matching_scales else 1
        listings.append(
            Listing(
                symbol=symbol,
                quote_asset="USDT",
                first_open_date=first_open_date,
                last_open_date=last_open_date,
                bar_count=len(symbol_bars),
                first_discontinuous_date=(
                    continuity_break.first_discontinuous_date
                    if continuity_break is not None
                    else None
                ),
                successor=(
                    predecessor_rename.successor if predecessor_rename is not None else None
                ),
                predecessor=(
                    successor_rename.predecessor if successor_rename is not None else None
                ),
                price_scale=price_scale,
            )
        )
    return tuple(listings)


def write_listings(path: Path, listings: tuple[Listing, ...]) -> None:
    table = pa.Table.from_pylist(
        [
            {
                "symbol": listing.symbol,
                "quote_asset": listing.quote_asset,
                "first_open_date": listing.first_open_date,
                "last_open_date": listing.last_open_date,
                "bar_count": listing.bar_count,
                "first_discontinuous_date": listing.first_discontinuous_date,
                "successor": listing.successor,
                "predecessor": listing.predecessor,
                "price_scale": listing.price_scale,
            }
            for listing in listings
        ],
        schema=_SCHEMA,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    pq.write_table(table, temporary)
    temporary.replace(path)


def read_listings(path: Path) -> tuple[Listing, ...]:
    return tuple(
        Listing(
            symbol=cast(str, row["symbol"]),
            quote_asset=cast(str, row["quote_asset"]),
            first_open_date=cast(date, row["first_open_date"]),
            last_open_date=cast(date, row["last_open_date"]),
            bar_count=cast(int, row["bar_count"]),
            first_discontinuous_date=cast(date | None, row["first_discontinuous_date"]),
            successor=cast(str | None, row["successor"]),
            predecessor=cast(str | None, row["predecessor"]),
            price_scale=cast(int, row["price_scale"]),
        )
        for row in pq.read_table(path).to_pylist()
    )
