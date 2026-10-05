"""Point-in-time portfolio book. Holdings are manual. Policy value is not a position."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from cip.domain.errors import PortfolioError
from cip.domain.policy import LoadedPolicy

_SHA = re.compile(r"^[0-9a-f]{40}$")
_POLICY_SHA = re.compile(r"^[0-9a-f]{64}$")
_SYMBOL = re.compile(r"^[A-Z0-9]{1,20}$")
_BOOK_FIELDS = frozenset(
    {
        "schema_version",
        "session",
        "recorded_at",
        "policy_version",
        "git_sha",
        "holdings_are_approximate",
        "core_usd",
        "discovery_usd",
        "reserve_usd",
        "core_monthly_usd",
        "discovery_monthly_usd",
        "reserve_monthly_usd",
        "holdings",
    }
)
_HOLDING_FIELDS = frozenset({"symbol", "quantity", "venue", "provenance"})
SCHEMA_VERSION: Literal[1] = 1


def _exact_type(kind: type[object]) -> BeforeValidator:
    def check(value: object) -> object:
        if type(value) is not kind:
            raise ValueError(f"must be a {kind.__name__}")
        return value

    return BeforeValidator(check)


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
    """One owner-entered line. Quantity is required and positive."""

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

    schema_version: Annotated[Literal[1], _exact_type(int)] = SCHEMA_VERSION
    session: date
    recorded_at: datetime
    policy_version: str
    git_sha: str
    holdings_are_approximate: Annotated[bool, _exact_type(bool)]
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

    @field_validator("policy_version")
    @classmethod
    def _policy_version(cls, value: str) -> str:
        if _POLICY_SHA.fullmatch(value) is None:
            raise ValueError("policy_version must be 64 lowercase hex characters")
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
        if set(document) != _BOOK_FIELDS:
            raise ValueError("portfolio document keys are fixed")
        holdings = document["holdings"]
        if not isinstance(holdings, list) or any(
            not isinstance(item, dict) or set(item) != _HOLDING_FIELDS for item in holdings
        ):
            raise ValueError("portfolio document keys are fixed")
        return cls(
            schema_version=document["schema_version"],
            session=_canonical_date(document["session"]),
            recorded_at=_canonical_utc(document["recorded_at"]),
            policy_version=document["policy_version"],
            git_sha=document["git_sha"],
            holdings_are_approximate=document["holdings_are_approximate"],
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
    try:
        checked = PortfolioBook.model_validate(book.model_dump())
    except ValidationError as error:
        raise PortfolioError("portfolio book is invalid") from error
    key = book_key(checked.session)
    path = root / key
    _require_inside(root / "portfolio", path)
    _create(path, _body(checked.to_document()))
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


def _canonical_date(value: object) -> date:
    if not isinstance(value, str):
        raise ValueError("session must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("session must be YYYY-MM-DD") from error
    if parsed.isoformat() != value:
        raise ValueError("session must be YYYY-MM-DD")
    return parsed


def _canonical_utc(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("recorded_at must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("recorded_at must be canonical UTC") from error
    if parsed.isoformat() != value:
        raise ValueError("recorded_at must be canonical UTC")
    return parsed


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
