"""Point-in-time portfolio book. Holdings are manual. Policy value is not a position."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

from cip.domain.errors import PortfolioError
from cip.domain.policy import LoadedPolicy

_SHA = re.compile(r"^[0-9a-f]{40}$")
_SYMBOL = re.compile(r"^[A-Z0-9]{1,20}$")
SCHEMA_VERSION: Literal[1] = 1


def _finite_decimal(value: object) -> Decimal:
    if isinstance(value, (bool, float, int)):
        raise ValueError("decimal values are Decimal or decimal strings")
    if isinstance(value, str):
        try:
            parsed = Decimal(value)
        except InvalidOperation as error:
            raise ValueError("decimal values are Decimal or decimal strings") from error
    elif isinstance(value, Decimal):
        parsed = value
    else:
        raise ValueError("decimal values are Decimal or decimal strings")
    if not parsed.is_finite():
        raise ValueError("decimal values are finite")
    return parsed


def _positive_decimal(value: object) -> Decimal:
    parsed = _finite_decimal(value)
    if parsed <= 0:
        raise ValueError("quantity must be positive")
    return parsed


def _non_negative_decimal(value: object) -> Decimal:
    parsed = _finite_decimal(value)
    if parsed < 0:
        raise ValueError("a sleeve balance cannot be negative")
    return parsed


def _optional_balance(value: object) -> Decimal | None:
    if value is None:
        return None
    return _non_negative_decimal(value)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class ManualHolding(_Strict):
    """One owner-entered line. A missing quantity stays missing."""

    symbol: str
    quantity: Annotated[Decimal, BeforeValidator(_positive_decimal)]
    venue: Literal["off_exchange"]
    provenance: str = Field(min_length=1)

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, value: str) -> str:
        if _SYMBOL.fullmatch(value) is None:
            raise ValueError("symbol must be 1 to 20 uppercase letters or digits")
        return value


class PortfolioBook(_Strict):
    """Stored sleeves and manual holdings. This is not an order."""

    schema_version: Literal[1] = SCHEMA_VERSION
    session: date
    recorded_at: datetime
    policy_version: str = Field(min_length=64, max_length=64)
    git_sha: str
    holdings_are_approximate: bool
    core_usd: Annotated[Decimal | None, BeforeValidator(_optional_balance)]
    discovery_usd: Annotated[Decimal | None, BeforeValidator(_optional_balance)]
    reserve_usd: Annotated[Decimal | None, BeforeValidator(_optional_balance)]
    core_monthly_usd: Annotated[Decimal, BeforeValidator(_non_negative_decimal)]
    discovery_monthly_usd: Annotated[Decimal, BeforeValidator(_non_negative_decimal)]
    reserve_monthly_usd: Annotated[Decimal, BeforeValidator(_non_negative_decimal)]
    holdings: tuple[ManualHolding, ...] = ()

    @field_validator("recorded_at")
    @classmethod
    def _recorded(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("recorded_at must be timezone-aware UTC")
        return value

    @field_validator("git_sha")
    @classmethod
    def _sha(cls, value: str) -> str:
        if _SHA.fullmatch(value) is None:
            raise ValueError("git_sha must be 40 lowercase hex characters")
        return value

    @model_validator(mode="after")
    def _inventory(self) -> Self:
        if not self.holdings_are_approximate and not self.holdings:
            raise ValueError("an empty inventory stays approximate")
        symbols = [holding.symbol for holding in self.holdings]
        if len(symbols) != len(set(symbols)):
            raise ValueError("a book repeats a holding")
        return self

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session": self.session.isoformat(),
            "recorded_at": self.recorded_at.isoformat(),
            "policy_version": self.policy_version,
            "git_sha": self.git_sha,
            "holdings_are_approximate": self.holdings_are_approximate,
            "core_usd": _money(self.core_usd),
            "discovery_usd": _money(self.discovery_usd),
            "reserve_usd": _money(self.reserve_usd),
            "core_monthly_usd": format(self.core_monthly_usd, "f"),
            "discovery_monthly_usd": format(self.discovery_monthly_usd, "f"),
            "reserve_monthly_usd": format(self.reserve_monthly_usd, "f"),
            "holdings": [
                {
                    "symbol": holding.symbol,
                    "quantity": format(holding.quantity, "f"),
                    "venue": holding.venue,
                    "provenance": holding.provenance,
                }
                for holding in self.holdings
            ],
        }

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> Self:
        holdings = document.get("holdings", [])
        if not isinstance(holdings, list):
            raise ValueError("holdings are a list")
        session = date.fromisoformat(str(document["session"]))
        recorded = datetime.fromisoformat(str(document["recorded_at"]))
        if recorded.tzinfo is not None:
            recorded = recorded.astimezone(UTC)
        return cls(
            schema_version=document["schema_version"],
            session=session,
            recorded_at=recorded,
            policy_version=str(document["policy_version"]),
            git_sha=str(document["git_sha"]),
            holdings_are_approximate=bool(document["holdings_are_approximate"]),
            core_usd=document["core_usd"],
            discovery_usd=document["discovery_usd"],
            reserve_usd=document["reserve_usd"],
            core_monthly_usd=document["core_monthly_usd"],
            discovery_monthly_usd=document["discovery_monthly_usd"],
            reserve_monthly_usd=document["reserve_monthly_usd"],
            holdings=tuple(ManualHolding.model_validate(item) for item in holdings),
        )


def open_book(
    *,
    session: date,
    recorded_at: datetime,
    policy: LoadedPolicy,
    git_sha: str,
    holdings: Sequence[ManualHolding] = (),
    core_usd: Decimal | None = None,
    discovery_usd: Decimal | None = None,
    reserve_usd: Decimal | None = None,
    infer_holdings: bool = False,
) -> PortfolioBook:
    """Copy sleeve budgets from policy. Do not invent quantities."""
    if infer_holdings:
        raise PortfolioError("holdings are not inferred from policy")
    portfolio = policy.policy.portfolio
    return PortfolioBook(
        session=session,
        recorded_at=recorded_at,
        policy_version=policy.version,
        git_sha=git_sha,
        holdings_are_approximate=portfolio.holdings_are_approximate,
        core_usd=core_usd,
        discovery_usd=discovery_usd,
        reserve_usd=reserve_usd,
        core_monthly_usd=portfolio.core_monthly_usd,
        discovery_monthly_usd=portfolio.discovery_monthly_usd,
        reserve_monthly_usd=portfolio.reserve_monthly_usd,
        holdings=tuple(holdings),
    )


def book_key(session: date) -> str:
    return f"portfolio/date={session.isoformat()}/book.json"


def write_book(root: Path, book: PortfolioBook) -> str:
    """Write the book once. The same document is a no-op. A different document is refused."""
    key = book_key(book.session)
    path = root / key
    _require_inside(root / "portfolio", path)
    _create(path, _body(book.to_document()))
    return key


def read_book(root: Path, session: date) -> PortfolioBook:
    path = root / book_key(session)
    _require_inside(root / "portfolio", path)
    if not path.exists():
        raise PortfolioError("portfolio book is missing")
    try:
        document = json.loads(path.read_text())
        book = PortfolioBook.from_document(document)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise PortfolioError("portfolio book is unusable") from error
    if book.session != session:
        raise PortfolioError("portfolio book is for a different session")
    return book


def _money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _body(document: dict[str, Any]) -> bytes:
    return json.dumps(document, sort_keys=True).encode()


def _require_inside(directory: Path, path: Path) -> None:
    if not path.resolve().is_relative_to(directory.resolve()):
        raise PortfolioError("record path escapes the store")


def _create(path: Path, body: bytes) -> bool:
    if path.exists():
        return _same_or_refuse(path, body)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(body)
    try:
        try:
            os.link(temporary, path)
        except FileExistsError:
            return _same_or_refuse(path, body)
        return True
    finally:
        temporary.unlink(missing_ok=True)


def _same_or_refuse(path: Path, body: bytes) -> bool:
    if path.read_bytes() == body:
        return False
    raise PortfolioError(f"record {path.name} already exists with a different payload")
