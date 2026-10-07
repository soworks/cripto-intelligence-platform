"""Point-in-time classification and market cap for one prospective session.

Unknown stays unknown. This module does not call a provider, does not change
eligibility thresholds, and does not fill the later lane fields.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

from cip.domain.errors import EvaluationError
from cip.evaluation.eligibility import CandidateFacts
from cip.evaluation.prospect import prospective_session
from cip.evaluation.session import session_close

_SYMBOL = re.compile(r"^[A-Z0-9]{1,20}$")
_SEALED = date(2026, 10, 6)
_Mapping = Literal["unambiguous", "ambiguous", "unmapped"]


@dataclass(frozen=True)
class AssetRecord:
    """Tag and swap evidence for one base asset. Missing stays missing."""

    fan_token: bool | None
    monitoring_tag: bool | None
    delisting: bool | None
    pending_migration: bool | None


@dataclass(frozen=True)
class CoinRecord:
    """Coin-level transfer switches. A disabled network is not a suspension."""

    deposits_suspended: bool | None
    withdrawals_suspended: bool | None


@dataclass(frozen=True)
class IdMap:
    """Pairs with one provider id. Ambiguous pairs are not in ``ids``."""

    ids: dict[str, str]
    ambiguous: frozenset[str]


@dataclass(frozen=True)
class CapReading:
    """One dated USD cap. The id is the provider's, not a ticker guess."""

    provider: str
    asset_id: str
    market_cap_usd: Decimal
    source_timestamp: datetime


@dataclass(frozen=True)
class SymbolClassification:
    """Flags and caps for one universe symbol. Later lane fields are absent."""

    symbol: str
    base_asset: str
    eur_stable: bool | None
    fan_token: bool | None
    monitoring_tag: bool | None
    delisting: bool | None
    deposits_suspended: bool | None
    withdrawals_suspended: bool | None
    pending_migration: bool | None
    coin_id: str | None
    mapping: _Mapping
    market_cap_usd: Decimal | None
    market_cap_source_timestamp: datetime | None
    cmc_id: str | None
    cmc_market_cap_usd: Decimal | None
    cmc_source_timestamp: datetime | None


def parse_asset_catalog(payload: object) -> dict[str, AssetRecord]:
    """Read Binance's public asset catalog. A repeated code is unusable."""
    found: dict[str, AssetRecord] = {}
    for row in _rows(payload):
        if not isinstance(row, dict):
            raise EvaluationError("capture is unusable")
        code = row.get("assetCode")
        if not isinstance(code, str) or code == "":
            raise EvaluationError("capture is unusable")
        if code in found:
            raise EvaluationError("asset catalog repeats an asset")
        found[code] = AssetRecord(
            fan_token=_tag(row.get("tags"), "fan_token"),
            monitoring_tag=_tag(row.get("tags"), "Monitoring"),
            delisting=_delisting(row.get("delisted"), row.get("preDelist")),
            pending_migration=_migration(code, row),
        )
    return found


def parse_coin_config(payload: object) -> dict[str, CoinRecord]:
    """Read deposit and withdrawal switches. Balances in the payload are ignored."""
    found: dict[str, CoinRecord] = {}
    for row in _rows(payload):
        if not isinstance(row, dict):
            raise EvaluationError("capture is unusable")
        coin = row.get("coin")
        if not isinstance(coin, str) or coin == "":
            raise EvaluationError("capture is unusable")
        if coin in found:
            raise EvaluationError("coin catalog repeats a coin")
        found[coin] = CoinRecord(
            deposits_suspended=_suspended(row.get("depositAllEnable")),
            withdrawals_suspended=_suspended(row.get("withdrawAllEnable")),
        )
    return found


def map_binance_tickers(tickers: Sequence[object]) -> IdMap:
    """Map a Binance pair to one CoinGecko id. Two ids are a refusal."""
    grouped: dict[str, set[str]] = {}
    for item in tickers:
        if not isinstance(item, dict):
            raise EvaluationError("capture is unusable")
        pair = _pair(item.get("base"), item.get("target"), item.get("market"))
        coin_id = item.get("coin_id")
        if pair is None or not isinstance(coin_id, str) or coin_id == "":
            continue
        grouped.setdefault(pair, set()).add(coin_id)
    return _split(grouped)


def map_cmc_ids(rows: Sequence[object]) -> IdMap:
    """Map a symbol to one CMC id. A null id is unmapped, not false."""
    if not isinstance(rows, list):
        raise EvaluationError("capture is unusable")
    grouped: dict[str, set[str]] = {}
    for item in rows:
        if not isinstance(item, dict):
            raise EvaluationError("capture is unusable")
        symbol = item.get("symbol")
        raw = item.get("cmcUniqueId")
        if not isinstance(symbol, str) or type(raw) is not int:
            continue
        grouped.setdefault(symbol, set()).add(str(raw))
    return _split(grouped)


def parse_coingecko_markets(payload: object) -> dict[str, CapReading]:
    """Dated CoinGecko caps. Rank and supply in the payload are not kept."""
    if not isinstance(payload, list):
        raise EvaluationError("capture is unusable")
    found: dict[str, CapReading] = {}
    for row in payload:
        if not isinstance(row, dict):
            raise EvaluationError("capture is unusable")
        asset_id = row.get("id")
        if not isinstance(asset_id, str) or asset_id == "":
            raise EvaluationError("capture is unusable")
        amount = _amount(row.get("market_cap"))
        if amount is None:
            continue
        stamp = _moment(row.get("last_updated"))
        if stamp is None:
            raise EvaluationError("undated fundamental")
        if asset_id in found:
            raise EvaluationError("market catalog repeats an asset")
        found[asset_id] = CapReading("coingecko", asset_id, amount, stamp)
    return found


def parse_cmc_quotes(payload: object) -> dict[str, CapReading]:
    """Dated CMC caps keyed by CMC id. A ticker-shaped payload is refused."""
    if not isinstance(payload, dict):
        raise EvaluationError("capture is unusable")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise EvaluationError("capture is unusable")
    rows: list[tuple[str, object]] = []
    for asset_id, body in data.items():
        if not isinstance(asset_id, str) or asset_id == "":
            raise EvaluationError("capture is unusable")
        rows.append((asset_id, body))
    found: dict[str, CapReading] = {}
    for asset_id, body in rows:
        reading = _cmc_row(asset_id, body)
        if reading is None:
            continue
        found[asset_id] = reading
    return found


def classify_universe(
    symbols: Sequence[tuple[str, str]],
    *,
    assets: Mapping[str, AssetRecord],
    coins: Mapping[str, CoinRecord],
    mapping: IdMap,
    eur_ids: frozenset[str] | None,
    caps: Mapping[str, CapReading],
    cmc: IdMap,
    cmc_caps: Mapping[str, CapReading],
) -> tuple[SymbolClassification, ...]:
    """Join catalogs onto the universe. An ambiguous id does not receive a cap."""
    return tuple(
        _symbol(symbol, base, assets, coins, mapping, eur_ids, caps, cmc, cmc_caps)
        for symbol, base in symbols
    )


def to_facts(item: SymbolClassification, *, quote_asset: str, status: str | None) -> CandidateFacts:
    """Eligibility facts. Rank, float, history, and unlocks stay missing."""
    return CandidateFacts(
        symbol=item.symbol,
        base_asset=item.base_asset,
        quote_asset=quote_asset,
        status=status,
        eur_stable=item.eur_stable,
        fan_token=item.fan_token,
        monitoring_tag=item.monitoring_tag,
        delisting=item.delisting,
        deposits_suspended=item.deposits_suspended,
        withdrawals_suspended=item.withdrawals_suspended,
        pending_migration=item.pending_migration,
        market_cap_usd=item.market_cap_usd,
        market_cap_rank=None,
        circulating_ratio=None,
        fdv_to_market_cap=None,
        history_days=None,
        unlock_schedule_known=None,
    )


def store_classification(
    root: Path, session: date, item: SymbolClassification, captured_at: datetime
) -> None:
    """Store one pre-close classification. A sealed session is refused."""
    prospective_session(session)
    if session == _SEALED:
        raise EvaluationError("sealed session stays sealed")
    if _manifest(root, session).exists():
        raise EvaluationError("finalized session is sealed")
    _before_close(captured_at, session_close(session))
    _create(_path(root, session, item.symbol), _body(session, item, captured_at))


def _symbol(
    symbol: str,
    base: str,
    assets: Mapping[str, AssetRecord],
    coins: Mapping[str, CoinRecord],
    mapping: IdMap,
    eur_ids: frozenset[str] | None,
    caps: Mapping[str, CapReading],
    cmc: IdMap,
    cmc_caps: Mapping[str, CapReading],
) -> SymbolClassification:
    asset = assets.get(base)
    coin = coins.get(base)
    coin_id, state = _lookup(symbol, mapping)
    cmc_id = _lookup(symbol, cmc)[0]
    cap = None if coin_id is None else caps.get(coin_id)
    cmc_cap = None if cmc_id is None else cmc_caps.get(cmc_id)
    return SymbolClassification(
        symbol=symbol,
        base_asset=base,
        eur_stable=_eur(coin_id, eur_ids),
        fan_token=None if asset is None else asset.fan_token,
        monitoring_tag=None if asset is None else asset.monitoring_tag,
        delisting=None if asset is None else asset.delisting,
        deposits_suspended=None if coin is None else coin.deposits_suspended,
        withdrawals_suspended=None if coin is None else coin.withdrawals_suspended,
        pending_migration=None if asset is None else asset.pending_migration,
        coin_id=coin_id,
        mapping=state,
        market_cap_usd=None if cap is None else cap.market_cap_usd,
        market_cap_source_timestamp=None if cap is None else cap.source_timestamp,
        cmc_id=cmc_id,
        cmc_market_cap_usd=None if cmc_cap is None else cmc_cap.market_cap_usd,
        cmc_source_timestamp=None if cmc_cap is None else cmc_cap.source_timestamp,
    )


def _lookup(symbol: str, mapped: IdMap) -> tuple[str | None, _Mapping]:
    if symbol in mapped.ambiguous:
        return None, "ambiguous"
    found = mapped.ids.get(symbol)
    if found is None:
        return None, "unmapped"
    return found, "unambiguous"


def _eur(coin_id: str | None, eur_ids: frozenset[str] | None) -> bool | None:
    if coin_id is None or eur_ids is None:
        return None
    return coin_id in eur_ids


def _rows(payload: object) -> list[object]:
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise EvaluationError("capture is unusable")
    data = payload.get("data")
    if not isinstance(data, list):
        raise EvaluationError("capture is unusable")
    return data


def _tag(tags: object, name: str) -> bool | None:
    if not isinstance(tags, list):
        return None
    return name in tags


def _delisting(delisted: object, pre_delist: object) -> bool | None:
    if delisted is True or pre_delist is True:
        return True
    if delisted is False and pre_delist is False:
        return False
    return None


def _migration(code: str, row: Mapping[str, object]) -> bool | None:
    tag = row.get("swapTag")
    if tag == "no":
        return False
    if tag == "ps":
        return True
    if tag != "sw":
        return None
    old = row.get("oldAssetCode")
    new = row.get("newAssetCode")
    if code == old and code != new:
        return True
    if code == new and code != old:
        return False
    return None


def _suspended(value: object) -> bool | None:
    if type(value) is not bool:
        return None
    return not value


def _pair(base: object, target: object, market: object) -> str | None:
    identifier = market.get("identifier") if isinstance(market, dict) else None
    if identifier != "binance":
        return None
    if not isinstance(base, str) or not isinstance(target, str):
        return None
    if _SYMBOL.fullmatch(base) is None or _SYMBOL.fullmatch(target) is None:
        return None
    return f"{base}{target}"


def _split(grouped: Mapping[str, set[str]]) -> IdMap:
    ids: dict[str, str] = {}
    ambiguous: set[str] = set()
    for symbol, found in grouped.items():
        if len(found) == 1:
            ids[symbol] = next(iter(found))
        else:
            ambiguous.add(symbol)
    return IdMap(ids, frozenset(ambiguous))


def _cmc_row(asset_id: str, body: object) -> CapReading | None:
    if not isinstance(body, dict):
        raise EvaluationError("capture is unusable")
    quote = body.get("quote")
    usd = quote.get("USD") if isinstance(quote, dict) else None
    if not isinstance(usd, dict):
        return None
    amount = _amount(usd.get("market_cap"))
    if amount is None:
        return None
    stamp = _moment(usd.get("last_updated"))
    if stamp is None:
        raise EvaluationError("undated fundamental")
    return CapReading("cmc", asset_id, amount, stamp)


def _amount(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise EvaluationError("capture is unusable")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation as error:
            raise EvaluationError("capture is unusable") from error
    raise EvaluationError("capture is unusable")


def _moment(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise EvaluationError("capture is unusable")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise EvaluationError("capture is unusable") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise EvaluationError("capture is unusable")
    return parsed.astimezone(UTC)


def _before_close(moment: datetime, close: datetime) -> None:
    if moment.tzinfo is None or moment.utcoffset() != timedelta(0):
        raise EvaluationError("captured_at must be timezone-aware UTC")
    if moment > close:
        raise EvaluationError("capture is after the close")


def _manifest(root: Path, session: date) -> Path:
    return root / "sessions" / f"date={session.isoformat()}" / "manifest.json"


def _path(root: Path, session: date, symbol: str) -> Path:
    return (
        root
        / "captures"
        / f"session={session.isoformat()}"
        / "classification"
        / f"symbol={symbol}.json"
    )


def _body(session: date, item: SymbolClassification, captured_at: datetime) -> bytes:
    document = {
        "session": session.isoformat(),
        "symbol": item.symbol,
        "base_asset": item.base_asset,
        "captured_at": _iso(captured_at),
        "eur_stable": item.eur_stable,
        "fan_token": item.fan_token,
        "monitoring_tag": item.monitoring_tag,
        "delisting": item.delisting,
        "deposits_suspended": item.deposits_suspended,
        "withdrawals_suspended": item.withdrawals_suspended,
        "pending_migration": item.pending_migration,
        "coin_id": item.coin_id,
        "mapping": item.mapping,
        "market_cap_usd": _decimal(item.market_cap_usd),
        "market_cap_source_timestamp": _stamp(item.market_cap_source_timestamp),
        "cmc_id": item.cmc_id,
        "cmc_market_cap_usd": _decimal(item.cmc_market_cap_usd),
        "cmc_source_timestamp": _stamp(item.cmc_source_timestamp),
    }
    return json.dumps(document, sort_keys=True).encode()


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _stamp(moment: datetime | None) -> str | None:
    return None if moment is None else _iso(moment)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _create(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() == body:
            return
        raise EvaluationError(f"capture {path.name} already exists with a different payload")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(body)
    try:
        os.link(temporary, path)
    except FileExistsError:
        if path.read_bytes() != body:
            raise EvaluationError(
                f"capture {path.name} already exists with a different payload"
            ) from None
    finally:
        temporary.unlink(missing_ok=True)
