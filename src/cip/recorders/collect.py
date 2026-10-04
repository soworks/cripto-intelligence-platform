from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import httpx

from cip.adapters.market import MarketData
from cip.domain.errors import ExchangeBannedError, MarketDataError, RecorderError
from cip.recorders.observation import CollectionFailure, Observation
from cip.recorders.sources import (
    COINGECKO_GLOBAL,
    DEFILLAMA_STABLECOINS,
    FUTURES_BASE_URL,
    book_observations,
    fetch_json,
    parse_btc_dominance,
    parse_funding,
    parse_open_interest,
    parse_stablecoin_supply,
)
from cip.recorders.store import (
    DirectoryStore,
    ObjectStore,
    append_failure_to,
    append_observation_to,
)

_SYMBOL = re.compile(r"^[A-Z0-9]{1,20}$")


@dataclass(frozen=True)
class CollectionResult:
    observations: tuple[Observation, ...]
    failures: tuple[CollectionFailure, ...]


def collect(
    *,
    observed_at: datetime,
    symbols: tuple[str, ...],
    dominance: Callable[[], Observation],
    stablecoins: Callable[[], Observation],
    funding: Callable[[str], Observation],
    open_interest: Callable[[str], Observation],
    book: Callable[[str], tuple[Observation, Observation]],
) -> CollectionResult:
    """Each source is isolated. A failure is recorded and does not drop the others."""
    observations: list[Observation] = []
    failures: list[CollectionFailure] = []
    _take(observations, failures, "btc_dominance", "coingecko", None, observed_at, dominance)
    _take(observations, failures, "stablecoin_supply", "defillama", None, observed_at, stablecoins)
    for symbol in symbols:
        if _SYMBOL.fullmatch(symbol) is None:
            _reject_symbol(failures, symbol, observed_at)
            continue
        _take(
            observations,
            failures,
            "funding",
            "binance",
            symbol,
            observed_at,
            _bind(funding, symbol),
        )
        _take(
            observations,
            failures,
            "open_interest",
            "binance",
            symbol,
            observed_at,
            _bind(open_interest, symbol),
        )
        _take_book(observations, failures, symbol, observed_at, _bind(book, symbol))
    return CollectionResult(observations=tuple(observations), failures=tuple(failures))


def collect_live(
    *,
    observed_at: datetime,
    symbols: tuple[str, ...],
    spot: MarketData,
    public: httpx.Client,
    futures: httpx.Client,
    depth_band: Decimal,
) -> CollectionResult:
    return collect(
        observed_at=observed_at,
        symbols=symbols,
        dominance=lambda: parse_btc_dominance(
            fetch_json(public, COINGECKO_GLOBAL), observed_at=observed_at
        ),
        stablecoins=lambda: parse_stablecoin_supply(
            fetch_json(public, DEFILLAMA_STABLECOINS), observed_at=observed_at
        ),
        funding=lambda symbol: parse_funding(
            fetch_json(
                futures,
                f"{FUTURES_BASE_URL}/fapi/v1/premiumIndex",
                {"symbol": symbol},
            ),
            observed_at=observed_at,
            symbol=symbol,
        ),
        open_interest=lambda symbol: parse_open_interest(
            fetch_json(
                futures,
                f"{FUTURES_BASE_URL}/fapi/v1/openInterest",
                {"symbol": symbol},
            ),
            observed_at=observed_at,
            symbol=symbol,
        ),
        book=lambda symbol: book_observations(
            symbol,
            spot.depth(symbol, limit=100),
            observed_at=observed_at,
            depth_band=depth_band,
        ),
    )


def persist(root: Path, result: CollectionResult) -> None:
    persist_to(DirectoryStore(root), result)


def persist_to(store: ObjectStore, result: CollectionResult) -> None:
    errors: list[str] = []
    for observation in result.observations:
        _store(errors, _call(append_observation_to, store, observation))
    for failure in result.failures:
        _store(errors, _call(append_failure_to, store, failure))
    if errors:
        raise RecorderError("; ".join(errors))


def _call[T](
    write: Callable[[ObjectStore, T], bool], store: ObjectStore, item: T
) -> Callable[[], bool]:
    def call() -> bool:
        return write(store, item)

    return call


def _store(errors: list[str], write: Callable[[], bool]) -> None:
    try:
        write()
    except RecorderError as error:
        errors.append(str(error))


def _bind[T](function: Callable[[str], T], symbol: str) -> Callable[[], T]:
    def call() -> T:
        return function(symbol)

    return call


def _take(
    observations: list[Observation],
    failures: list[CollectionFailure],
    series: str,
    provider: str,
    symbol: str | None,
    observed_at: datetime,
    fetch: Callable[[], Observation],
) -> None:
    try:
        fetched = fetch()
    except ExchangeBannedError:
        raise
    except (RecorderError, MarketDataError, httpx.HTTPError) as error:
        failures.append(
            CollectionFailure(
                series=series,
                provider=provider,
                observed_at=observed_at,
                symbol=symbol,
                error=str(error),
            )
        )
        return
    observations.append(fetched)


def _take_book(
    observations: list[Observation],
    failures: list[CollectionFailure],
    symbol: str,
    observed_at: datetime,
    fetch: Callable[[], tuple[Observation, Observation]],
) -> None:
    try:
        fetched = fetch()
    except ExchangeBannedError:
        raise
    except (RecorderError, MarketDataError, httpx.HTTPError) as error:
        for series in ("spread", "depth"):
            failures.append(
                CollectionFailure(
                    series=series,
                    provider="binance",
                    observed_at=observed_at,
                    symbol=symbol,
                    error=str(error),
                )
            )
        return
    observations.extend(fetched)


def _reject_symbol(failures: list[CollectionFailure], symbol: str, observed_at: datetime) -> None:
    for series in ("funding", "open_interest", "spread", "depth"):
        failures.append(
            CollectionFailure(
                series=series,
                provider="binance",
                observed_at=observed_at,
                symbol=symbol,
                error=f"invalid symbol {symbol}",
            )
        )
