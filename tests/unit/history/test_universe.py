from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

import cip.history.universe as universe
from cip.domain.errors import HistoryError
from cip.history.bars import DailyBar, parse_kline_zip
from cip.history.continuity import load_continuity
from cip.history.universe import Listing, build_listings, read_listings, write_listings

FIXTURES = Path(__file__).parents[2] / "fixtures" / "binance-vision"
REPO_CONTINUITY = Path(__file__).parents[3] / "data" / "symbol-continuity.yaml"


def _parsed(name: str) -> tuple[DailyBar, ...]:
    symbol, _, period = name.partition("-1d-")
    payload = (FIXTURES / f"{name}.zip").read_bytes()
    checksum = (FIXTURES / f"{name}.zip.CHECKSUM").read_text()
    return parse_kline_zip(payload, symbol=symbol, period=period, checksum_text=checksum)


def _bar(symbol: str, opened: date, close: str = "1.00000000") -> DailyBar:
    value = Decimal(close)
    return DailyBar(
        symbol=symbol,
        open_date=opened,
        open=value,
        high=value,
        low=value,
        close=value,
        volume=Decimal("1.00000000"),
        quote_volume=Decimal("1.00000000"),
        trade_count=1,
        taker_buy_base_volume=Decimal("1.00000000"),
        taker_buy_quote_volume=Decimal("1.00000000"),
    )


def _write_yaml(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "continuity.yaml"
    path.write_text(text)
    return path


def test_repository_continuity_loads_without_luna_to_lunc_rename() -> None:
    continuity = load_continuity(REPO_CONTINUITY)

    assert continuity.schema_version == 1
    assert continuity.breaks[0].symbol == "LUNAUSDT"
    assert all(
        rename.predecessor != "LUNAUSDT" and rename.successor != "LUNCUSDT"
        for rename in continuity.renames
    )


@pytest.mark.parametrize(
    "document",
    [
        "schema_version: true\nbreaks: []\nrenames: []\nscales: []\n",
        "schema_version: 1\nbreaks: []\nrenames: []\nscales: []\nunknown: value\n",
        "schema_version: 1\nrenames: []\nscales: []\n",
        "[]\n",
        "schema_version: 1\nschema_version: 1\nbreaks: []\nrenames: []\nscales: []\n",
    ],
)
def test_invalid_continuity_documents_raise_history_error(tmp_path: Path, document: str) -> None:
    with pytest.raises(HistoryError):
        load_continuity(_write_yaml(tmp_path, document))


def test_unreadable_continuity_file_raises_history_error(tmp_path: Path) -> None:
    with pytest.raises(HistoryError):
        load_continuity(tmp_path / "missing.yaml")


@pytest.mark.parametrize(
    ("section", "entries"),
    [
        (
            "breaks",
            """
  - {symbol: LUNAUSDT, first_discontinuous_date: 2022-05-31, reason: one}
  - {symbol: LUNAUSDT, first_discontinuous_date: 2022-06-01, reason: two}
""",
        ),
        (
            "renames",
            """
  - predecessor: OLDUSDT
    successor: NEWUSDT
    predecessor_last_date: 2024-01-01
    successor_first_date: 2024-01-02
    reason: one
  - predecessor: OLDUSDT
    successor: OTHERUSDT
    predecessor_last_date: 2024-01-01
    successor_first_date: 2024-01-02
    reason: two
""",
        ),
        (
            "renames",
            """
  - predecessor: OLDUSDT
    successor: NEWUSDT
    predecessor_last_date: 2024-01-01
    successor_first_date: 2024-01-02
    reason: one
  - predecessor: OTHERUSDT
    successor: NEWUSDT
    predecessor_last_date: 2024-01-01
    successor_first_date: 2024-01-02
    reason: two
""",
        ),
    ],
)
def test_duplicate_continuity_identities_are_rejected(
    tmp_path: Path, section: str, entries: str
) -> None:
    values = {"breaks": "[]", "renames": "[]", "scales": "[]"}
    values[section] = entries
    document = (
        f"schema_version: 1\nbreaks: {values['breaks']}\n"
        f"renames: {values['renames']}\nscales: {values['scales']}\n"
    )
    with pytest.raises(HistoryError):
        load_continuity(_write_yaml(tmp_path, document))


def test_invalid_scale_pattern_is_rejected(tmp_path: Path) -> None:
    document = """
schema_version: 1
breaks: []
renames: []
scales:
  - {pattern: "[", price_scale: 1000, reason: invalid}
"""
    with pytest.raises(HistoryError):
        load_continuity(_write_yaml(tmp_path, document))


def test_recorded_listings_keep_dates_prices_and_discontinuity() -> None:
    continuity = load_continuity(REPO_CONTINUITY)
    luna = _parsed("LUNAUSDT-1d-2022-05")
    ftt = _parsed("FTTUSDT-1d-2022-11")
    bts = _parsed("BTSUSDT-1d-2023-12")
    blz = _parsed("BLZUSDT-1d-2024-12")
    alpaca = _parsed("ALPACAUSDT-1d-2025-05")
    bars = {
        "LUNAUSDT": luna,
        "FTTUSDT": ftt,
        "BTSUSDT": bts,
        "BLZUSDT": blz,
        "ALPACAUSDT": alpaca,
    }
    collapse_close = next(bar.close for bar in luna if bar.open_date == date(2022, 5, 13))

    listings = {listing.symbol: listing for listing in build_listings(bars, continuity)}

    assert listings["LUNAUSDT"].first_discontinuous_date == date(2022, 5, 31)
    assert listings["LUNAUSDT"].bar_count == 14
    assert collapse_close == Decimal("0.00005000")
    assert next(bar.close for bar in luna if bar.open_date == date(2022, 5, 13)) == collapse_close
    assert ftt[0].open_date == date(2022, 11, 1)
    assert listings["BTSUSDT"].last_open_date.month == 12
    assert listings["BTSUSDT"].last_open_date.year == 2023
    assert listings["BLZUSDT"].last_open_date.month == 12
    assert listings["BLZUSDT"].last_open_date.year == 2024
    assert listings["ALPACAUSDT"].last_open_date == date(2025, 5, 2)
    assert listings["ALPACAUSDT"].bar_count == 2
    assert all(listing.quote_asset == "USDT" for listing in listings.values())


def test_renames_label_both_sides_at_exact_dates() -> None:
    continuity = load_continuity(REPO_CONTINUITY)
    bars = {
        "MATICUSDT": (_bar("MATICUSDT", date(2024, 9, 10)),),
        "POLUSDT": (_bar("POLUSDT", date(2024, 9, 13)),),
        "FTMUSDT": (_bar("FTMUSDT", date(2025, 1, 13)),),
        "SUSDT": (_bar("SUSDT", date(2025, 1, 16)),),
    }

    listings = {listing.symbol: listing for listing in build_listings(bars, continuity)}

    assert listings["MATICUSDT"].successor == "POLUSDT"
    assert listings["POLUSDT"].predecessor == "MATICUSDT"
    assert listings["FTMUSDT"].successor == "SUSDT"
    assert listings["SUSDT"].predecessor == "FTMUSDT"


@pytest.mark.parametrize(
    "bars",
    [
        {"MATICUSDT": (_bar("MATICUSDT", date(2024, 9, 9)),)},
        {"POLUSDT": (_bar("POLUSDT", date(2024, 9, 14)),)},
        {"LUNAUSDT": (_bar("LUNAUSDT", date(2022, 5, 13), "0.00005000"),)},
    ],
)
def test_continuity_date_mismatch_raises_before_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bars: dict[str, tuple[DailyBar, ...]]
) -> None:
    continuity = load_continuity(REPO_CONTINUITY)
    called = False

    def fail_if_called(path: Path, listings: tuple[Listing, ...]) -> None:
        del path, listings
        nonlocal called
        called = True

    monkeypatch.setattr("cip.history.universe.write_listings", fail_if_called)

    def build_and_write() -> None:
        listings = build_listings(bars, continuity)
        universe.write_listings(tmp_path / "listings.parquet", listings)

    with pytest.raises(HistoryError):
        build_and_write()
    assert not called


def test_scales_default_and_first_match_rules(tmp_path: Path) -> None:
    continuity = load_continuity(REPO_CONTINUITY)
    listings = {
        listing.symbol: listing
        for listing in build_listings(
            {
                "1000SATSUSDT": (_bar("1000SATSUSDT", date(2024, 1, 1)),),
                "BTCUSDT": (_bar("BTCUSDT", date(2024, 1, 1)),),
            },
            continuity,
        )
    }
    assert listings["1000SATSUSDT"].price_scale == 1000
    assert listings["BTCUSDT"].price_scale == 1

    overlapping = _write_yaml(
        tmp_path,
        """
schema_version: 1
breaks: []
renames: []
scales:
  - {pattern: "^1000", price_scale: 1000, reason: one}
  - {pattern: "USDT$", price_scale: 2, reason: two}
""",
    )
    with pytest.raises(HistoryError):
        build_listings(
            {"1000SATSUSDT": (_bar("1000SATSUSDT", date(2024, 1, 1)),)},
            load_continuity(overlapping),
        )


def test_listing_parquet_round_trip_preserves_nulls_and_order(tmp_path: Path) -> None:
    continuity = load_continuity(REPO_CONTINUITY)
    listings = build_listings(
        {
            "BTCUSDT": (
                _bar("BTCUSDT", date(2024, 1, 2)),
                _bar("BTCUSDT", date(2024, 1, 1)),
            )
        },
        continuity,
    )
    path = tmp_path / "universe" / "listings.parquet"

    write_listings(path, listings)

    assert read_listings(path) == listings
    assert listings[0].first_open_date == date(2024, 1, 1)
    assert listings[0].last_open_date == date(2024, 1, 2)
    assert listings[0].first_discontinuous_date is None
    assert listings[0].successor is None
    assert listings[0].predecessor is None
    assert build_listings({}, continuity) == ()
