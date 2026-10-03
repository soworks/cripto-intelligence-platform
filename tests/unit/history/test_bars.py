import hashlib
import io
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from cip.domain.errors import HistoryError
from cip.history.bars import DailyBar, parse_kline_zip, verified_sha256

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


def test_unreadable_zip_and_non_utf8_csv_are_rejected() -> None:
    with pytest.raises(HistoryError):
        parse_kline_zip(b"not-a-zip", symbol="BTCUSDT", period="2024-01", checksum_text="x")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("BTCUSDT-1d-2024-01.csv", b"\xff\xfe")
    bad_payload = buffer.getvalue()
    bad_checksum = f"{hashlib.sha256(bad_payload).hexdigest()}  BTCUSDT-1d-2024-01.zip"
    with pytest.raises(HistoryError):
        parse_kline_zip(bad_payload, symbol="BTCUSDT", period="2024-01", checksum_text=bad_checksum)


def test_non_hex_checksum_digest_is_rejected() -> None:
    payload, _ = _zip("BTCUSDT", "2024-01", [_row(1704067200000)])
    checksum = f"{'g' * 64}  BTCUSDT-1d-2024-01.zip"
    with pytest.raises(HistoryError):
        parse_kline_zip(payload, symbol="BTCUSDT", period="2024-01", checksum_text=checksum)


def test_invalid_integer_fields_are_rejected() -> None:
    open_time = 1704067200000
    close_time = open_time + DAY_MS - 1
    rows = [
        f",1,1,1,1,1,{close_time},1,1,1,1,0",
        f"-,1,1,1,1,1,{close_time},1,1,1,1,0",
        f"{open_time},1,1,1,1,1,{close_time},1,-,1,1,0",
    ]
    for line in rows:
        payload, checksum = _zip("BTCUSDT", "2024-01", [line])
        with pytest.raises(HistoryError):
            parse_kline_zip(payload, symbol="BTCUSDT", period="2024-01", checksum_text=checksum)


def test_invalid_decimal_fields_are_rejected() -> None:
    open_time = 1704067200000
    close_time = open_time + DAY_MS - 1
    for bad in ("not-a-number", "NaN", "Infinity"):
        row = f"{open_time},{bad},1,1,1,1,{close_time},1,1,1,1,0"
        payload, checksum = _zip("BTCUSDT", "2024-01", [row])
        with pytest.raises(HistoryError):
            parse_kline_zip(payload, symbol="BTCUSDT", period="2024-01", checksum_text=checksum)


def test_oversized_decimal128_prices_are_rejected() -> None:
    open_time = 1704067200000
    close_time = open_time + DAY_MS - 1
    too_many_integer_digits = "1" * 31
    for bad_open in ("1E+40", too_many_integer_digits):
        row = f"{open_time},{bad_open},1,1,1,1,{close_time},1,1,1,1,0"
        payload, checksum = _zip("BTCUSDT", "2024-01", [row])
        with pytest.raises(HistoryError):
            parse_kline_zip(payload, symbol="BTCUSDT", period="2024-01", checksum_text=checksum)


def test_close_at_open_and_at_next_midnight_are_rejected() -> None:
    open_time = 1704067200000
    for close_time in (open_time, open_time + DAY_MS):
        row = f"{open_time},1,1,1,1,1,{close_time},1,1,1,1,0"
        payload, checksum = _zip("BTCUSDT", "2024-01", [row])
        with pytest.raises(HistoryError):
            parse_kline_zip(payload, symbol="BTCUSDT", period="2024-01", checksum_text=checksum)


def test_partial_day_close_still_parses() -> None:
    open_time = 1704067200000
    close_time = open_time + DAY_MS // 2
    row = f"{open_time},1,1,1,1,1,{close_time},1,1,1,1,0"
    payload, checksum = _zip("BTCUSDT", "2024-01", [row])
    bars = parse_kline_zip(payload, symbol="BTCUSDT", period="2024-01", checksum_text=checksum)
    assert bars[0].open_date == date(2024, 1, 1)


def test_verified_sha256_returns_the_digest_of_a_matching_payload() -> None:
    payload, checksum = _load("BTCUSDT-1d-2025-01")

    digest = verified_sha256(payload, checksum, "BTCUSDT-1d-2025-01.zip")

    assert digest == "3a3eb1b723d944deb4dbef5ae361bbe39340a17fe987fe22e397ef03dca268d2"


def test_verified_sha256_rejects_a_wrong_payload_and_a_wrong_name() -> None:
    payload, checksum = _load("BTCUSDT-1d-2025-01")

    with pytest.raises(HistoryError, match="mismatch"):
        verified_sha256(payload + b"x", checksum, "BTCUSDT-1d-2025-01.zip")
    with pytest.raises(HistoryError, match="invalid kline checksum"):
        verified_sha256(payload, checksum, "other.zip")
