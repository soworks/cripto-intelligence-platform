import json
import os
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from cip.domain.errors import PortfolioError
from cip.domain.policy import load_policy
from cip.portfolio import book as book_module
from cip.portfolio.book import (
    ManualHolding,
    PortfolioBook,
    book_key,
    open_book,
    read_book,
    write_book,
)

_POLICY = Path(__file__).parents[3] / "policies" / "investment-policy.yaml"
SHA = "a" * 40
WHEN = datetime(2026, 10, 5, 12, tzinfo=UTC)


def _open(**overrides: object) -> PortfolioBook:
    policy = load_policy(_POLICY)
    values: dict[str, object] = {
        "session": WHEN.date(),
        "recorded_at": WHEN,
        "policy": policy,
        "git_sha": SHA,
    }
    values.update(overrides)
    return open_book(**values)  # type: ignore[arg-type]


def test_an_empty_inventory_is_a_valid_approximate_book() -> None:
    policy = load_policy(_POLICY).policy.portfolio
    book = _open()
    assert book.holdings == ()
    assert book.holdings_are_approximate is True
    assert book.core_usd is None
    assert book.discovery_usd is None
    assert book.reserve_usd is None
    assert book.core_monthly_usd == policy.core_monthly_usd
    assert book.discovery_monthly_usd == policy.discovery_monthly_usd
    assert book.reserve_monthly_usd == policy.reserve_monthly_usd
    assert "BTCUSDT" not in {holding.symbol for holding in book.holdings}
    assert book.to_document()["schema_version"] == 1
    assert "order_id" not in book.to_document()


def test_policy_value_is_not_turned_into_a_holding() -> None:
    with pytest.raises(PortfolioError, match="not inferred"):
        _open(infer_holdings=True)
    book = _open(core_usd=Decimal("10.00"))
    assert book.core_usd == Decimal("10.00")
    assert book.holdings == ()


def test_a_manual_off_exchange_line_round_trips(tmp_path: Path) -> None:
    holding = ManualHolding(
        symbol="BTCUSDT",
        quantity=Decimal("0.01"),
        venue="off_exchange",
        provenance="owner",
    )
    book = _open(holdings=(holding,))
    key = write_book(tmp_path, book)
    assert read_book(tmp_path, book.session) == book
    assert (tmp_path / key).is_file()
    again = write_book(tmp_path, book)
    assert again == key
    changed = book.model_copy(update={"core_usd": Decimal("1")})
    with pytest.raises(PortfolioError, match="different payload"):
        write_book(tmp_path, changed)


def test_an_empty_inventory_cannot_be_declared_finished() -> None:
    document = _open().to_document()
    document["holdings_are_approximate"] = False
    with pytest.raises(ValidationError, match="approximate"):
        PortfolioBook.from_document(document)
    with pytest.raises(ValidationError):
        _open(core_usd=Decimal("-1"))


def test_a_manual_line_needs_a_quantity_and_a_source() -> None:
    with pytest.raises(ValidationError):
        ManualHolding(symbol="BTCUSDT", venue="off_exchange", provenance="owner")
    with pytest.raises(ValidationError):
        ManualHolding(
            symbol="BTCUSDT",
            quantity=Decimal("1"),
            venue="off_exchange",
            provenance="",
        )
    with pytest.raises(ValidationError):
        _open(
            holdings=(
                ManualHolding(
                    symbol="BTCUSDT",
                    quantity=Decimal("1"),
                    venue="off_exchange",
                    provenance="owner",
                ),
                ManualHolding(
                    symbol="BTCUSDT",
                    quantity=Decimal("2"),
                    venue="off_exchange",
                    provenance="owner",
                ),
            )
        )


def test_a_missing_book_is_not_invented(tmp_path: Path) -> None:
    with pytest.raises(PortfolioError, match="missing"):
        read_book(tmp_path, WHEN.date())
    book = _open()
    write_book(tmp_path, book)
    path = tmp_path / book_key(book.session)
    path.write_text("{", encoding="utf-8")
    with pytest.raises(PortfolioError, match="unusable"):
        read_book(tmp_path, book.session)
    document = book.to_document()
    document["session"] = "2026-10-04"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(PortfolioError, match="different session"):
        read_book(tmp_path, book.session)
    document["holdings"] = "no"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(PortfolioError, match="unusable"):
        read_book(tmp_path, book.session)
    document = book.to_document()
    document["recorded_at"] = "2026-10-05T12:00:00"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(PortfolioError, match="unusable"):
        read_book(tmp_path, book.session)


def test_bad_manual_inputs_are_refused() -> None:
    with pytest.raises(ValidationError):
        ManualHolding(symbol="btc", quantity=Decimal("1"), venue="off_exchange", provenance="owner")
    with pytest.raises(ValidationError):
        ManualHolding(
            symbol="BTCUSDT", quantity=Decimal("0"), venue="off_exchange", provenance="owner"
        )
    with pytest.raises(ValidationError):
        ManualHolding(symbol="BTCUSDT", quantity=1, venue="off_exchange", provenance="owner")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ManualHolding(symbol="BTCUSDT", quantity="nope", venue="off_exchange", provenance="owner")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ManualHolding(symbol="BTCUSDT", quantity=object(), venue="off_exchange", provenance="owner")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ManualHolding(
            symbol="BTCUSDT",
            quantity=Decimal("NaN"),
            venue="off_exchange",
            provenance="owner",
        )
    with pytest.raises(ValidationError):
        _open(recorded_at=datetime(2026, 10, 5, 12))  # noqa: DTZ001
    with pytest.raises(ValidationError):
        _open(recorded_at=datetime(2026, 10, 5, 12, tzinfo=timezone(timedelta(hours=-5))))
    with pytest.raises(ValidationError):
        _open(git_sha="abc")


def test_a_store_path_cannot_escape_and_a_create_race_keeps_the_bytes(tmp_path: Path) -> None:
    with pytest.raises(PortfolioError, match="escapes"):
        book_module._require_inside(tmp_path / "portfolio", tmp_path / "book.json")
    book = _open(core_usd=Decimal("3"))
    real_link = os.link

    def race(source: str, destination: str) -> None:
        real_link(source, destination)
        raise FileExistsError

    monkeypatch_link = race
    original = book_module.os.link
    book_module.os.link = monkeypatch_link  # type: ignore[method-assign]
    try:
        assert write_book(tmp_path, book) == book_key(book.session)
    finally:
        book_module.os.link = original  # type: ignore[method-assign]
    assert read_book(tmp_path, book.session) == book
