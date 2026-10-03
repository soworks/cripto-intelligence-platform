import hashlib
import io
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from cip.domain.errors import HistoryError
from cip.history.bars import DailyBar, parse_kline_zip

FIXTURES = Path(__file__).parents[2] / "fixtures" / "binance-vision"
DAY_MS = 86_400_000
DAY_US = 86_400_000_000


def _load(name: str) -> tuple[bytes, str]:
    payload = (FIXTURES / f"{name}.zip").read_bytes()
    checksum = (FIXTURES / f"{name}.zip.CHECKSUM").read_text()
    return payload, checksum


def _zip(
    symbol: str, period: str, rows: list[str], *, inner: str | None = None
) -> tuple[bytes, str]:
    csv_name = inner or f"{symbol}-1d-{period}.csv"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(csv_name, ("\n".join(rows) + "\n") if rows else "")
    payload = buffer.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    return payload, f"{digest}  {symbol}-1d-{period}.zip"


def _row(open_time: int, *, unit: str = "ms", close: str = "1.00000000") -> str:
    step = DAY_US if unit == "us" else DAY_MS
    close_time = open_time + step - 1
    return (
        f"{open_time},{close},{close},{close},{close},1.00000000,"
        f"{close_time},1.00000000,1,1.00000000,1.00000000,0"
    )


def _parsed(name: str) -> tuple[DailyBar, ...]:
    symbol, _, period = name.partition("-1d-")
    payload, checksum = _load(name)
    return parse_kline_zip(payload, symbol=symbol, period=period, checksum_text=checksum)


def test_luna_may_2022_keeps_the_collapse_and_the_later_bar() -> None:
    bars = _parsed("LUNAUSDT-1d-2022-05")
    by_date = {bar.open_date: bar for bar in bars}
    assert by_date[date(2022, 5, 1)].close == Decimal("82.23000000")
    assert by_date[date(2022, 5, 13)].close == Decimal("0.00005000")
    assert by_date[date(2022, 5, 31)].close == Decimal("8.87000000")
    assert len(bars) == 14


def test_ftt_november_2022_has_the_first_of_the_month() -> None:
    bars = _parsed("FTTUSDT-1d-2022-11")
    assert bars[0].open_date == date(2022, 11, 1)


def test_bts_december_2023_and_blz_december_2024_parse() -> None:
    assert _parsed("BTSUSDT-1d-2023-12")[-1].open_date.year == 2023
    assert _parsed("BLZUSDT-1d-2024-12")[-1].open_date.month == 12


def test_alpaca_may_2025_ends_on_the_second() -> None:
    bars = _parsed("ALPACAUSDT-1d-2025-05")
    assert [bar.open_date for bar in bars] == [date(2025, 5, 1), date(2025, 5, 2)]


def test_btc_january_2025_uses_microseconds() -> None:
    bars = _parsed("BTCUSDT-1d-2025-01")
    assert bars[0].open_date == date(2025, 1, 1)
    assert bars[0].close == Decimal("94591.79000000")
    assert len(bars) == 31


def test_daily_btc_file_parses() -> None:
    bars = _parsed("BTCUSDT-1d-2026-10-01")
    assert bars[0].open_date == date(2026, 10, 1)


def test_uppercase_checksum_is_accepted() -> None:
    payload, checksum = _zip("BTCUSDT", "2024-01", [_row(1704067200000)])
    digest, _, name = checksum.partition("  ")
    bars = parse_kline_zip(
        payload, symbol="BTCUSDT", period="2024-01", checksum_text=f"{digest.upper()}  {name}"
    )
    assert bars[0].open_date == date(2024, 1, 1)


@pytest.mark.parametrize(
    "checksum",
    ["not-a-digest  BTCUSDT-1d-2024-01.zip", "ab" * 32 + "  OTHER.zip", "no-space"],
)
def test_malformed_checksum_is_rejected(checksum: str) -> None:
    payload, _ = _zip("BTCUSDT", "2024-01", [_row(1704067200000)])
    with pytest.raises(HistoryError):
        parse_kline_zip(payload, symbol="BTCUSDT", period="2024-01", checksum_text=checksum)


def test_checksum_mismatch_is_rejected() -> None:
    payload, _ = _zip("BTCUSDT", "2024-01", [_row(1704067200000)])
    wrong = f"{'a' * 64}  BTCUSDT-1d-2024-01.zip"
    with pytest.raises(HistoryError):
        parse_kline_zip(payload, symbol="BTCUSDT", period="2024-01", checksum_text=wrong)


def test_short_row_non_midnight_duplicate_and_unsorted_are_rejected() -> None:
    good = _row(1704067200000)
    later = _row(1704153600000)
    cases = [
        ["1,2,3"],
        [_row(1704067200000 + 1000)],
        [good, good],
        [later, good],
        [],
    ]
    for rows in cases:
        payload, checksum = _zip("BTCUSDT", "2024-01", rows)
        with pytest.raises(HistoryError):
            parse_kline_zip(payload, symbol="BTCUSDT", period="2024-01", checksum_text=checksum)


def test_bad_ohlc_volume_precision_and_inner_name_are_rejected() -> None:
    open_time = 1704067200000
    close_time = open_time + DAY_MS - 1
    bad_rows = [
        f"{open_time},2,1,1,1,1,{close_time},1,1,1,1,0",
        f"{open_time},1,1,1,1,-1,{close_time},1,1,1,1,0",
        f"{open_time},1.000000001,1.000000001,1.000000001,1.000000001,1,{close_time},1,1,1,1,0",
        f"{open_time},1,1,1,1,1,{close_time},1,-1,1,1,0",
    ]
    for row in bad_rows:
        payload, checksum = _zip("BTCUSDT", "2024-01", [row])
        with pytest.raises(HistoryError):
            parse_kline_zip(payload, symbol="BTCUSDT", period="2024-01", checksum_text=checksum)
    payload, checksum = _zip("BTCUSDT", "2024-01", [_row(open_time)], inner="nope.csv")
    with pytest.raises(HistoryError):
        parse_kline_zip(payload, symbol="BTCUSDT", period="2024-01", checksum_text=checksum)
