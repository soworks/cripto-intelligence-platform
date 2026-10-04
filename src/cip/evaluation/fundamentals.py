from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cip.domain.policy import FundamentalsHypotheses

_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{label} must be timezone-aware UTC")
    return value.astimezone(UTC)


def _number(value: object, label: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, (bool, float)):
        raise ValueError(f"{label} is a decimal string or integer")
    if isinstance(value, str):
        try:
            parsed = Decimal(value)
        except InvalidOperation as error:
            raise ValueError(f"{label} is a decimal string or integer") from error
    elif isinstance(value, int):
        parsed = Decimal(value)
    elif isinstance(value, Decimal):
        parsed = value
    else:
        raise ValueError(f"{label} is a decimal string or integer")
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"{label} is a non-negative finite decimal")
    return parsed


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} is an object")
    return value


def _child(body: Mapping[str, Any], key: str) -> dict[str, Any] | None:
    if key not in body or body[key] is None:
        return None
    return _mapping(body[key], key)


def _clock(value: object, label: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _utc(value, label)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"{label} is an ISO timestamp") from error
        return _utc(parsed, label)
    raise ValueError(f"{label} is an ISO timestamp")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class CoinGeckoReading(_Strict):
    coingecko_id: str | None
    market_cap_usd: Decimal | None
    circulating_supply: Decimal | None
    total_supply: Decimal | None
    fully_diluted_valuation_usd: Decimal | None
    source_timestamp: datetime | None


class CmcReading(_Strict):
    market_cap_usd: Decimal | None
    source_timestamp: datetime | None


class DefiLlamaFees(_Strict):
    """Parsed fees. They are not a gate until policy names a threshold."""

    fees_24h_usd: Decimal | None
    revenue_24h_usd: Decimal | None


class UnlockReading(_Strict):
    schedule_known: bool | None
    pct_circ_14d: Decimal | None
    pct_circ_90d: Decimal | None


class FundamentalsDecision(_Strict):
    """Fundamental reasons. An acceptance is not a BUY."""

    accepted: bool
    reason_codes: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _acceptance_is_explicit(self) -> Self:
        if any(_CODE.fullmatch(code) is None for code in self.reason_codes):
            raise ValueError("reason codes are lowercase snake_case")
        if self.accepted and self.reason_codes != ("fundamentals_ok",):
            raise ValueError("an accepted fundamental reading carries fundamentals_ok")
        if not self.accepted and "fundamentals_ok" in self.reason_codes:
            raise ValueError("a rejection does not carry fundamentals_ok")
        return self


def parse_coingecko_market(payload: object) -> CoinGeckoReading:
    body = _mapping(payload, "coingecko")
    coin_id = body.get("id")
    if coin_id is not None and not isinstance(coin_id, str):
        raise ValueError("coingecko id is a string")
    market = _child(body, "market_data")
    if market is None:
        return CoinGeckoReading(
            coingecko_id=coin_id if isinstance(coin_id, str) else None,
            market_cap_usd=None,
            circulating_supply=None,
            total_supply=None,
            fully_diluted_valuation_usd=None,
            source_timestamp=None,
        )
    cap = _child(market, "market_cap")
    fdv = _child(market, "fully_diluted_valuation")
    return CoinGeckoReading(
        coingecko_id=coin_id if isinstance(coin_id, str) else None,
        market_cap_usd=None if cap is None else _number(cap.get("usd"), "market cap"),
        circulating_supply=_number(market.get("circulating_supply"), "circulating supply"),
        total_supply=_number(market.get("total_supply"), "total supply"),
        fully_diluted_valuation_usd=None if fdv is None else _number(fdv.get("usd"), "fdv"),
        source_timestamp=_clock(market.get("last_updated"), "coingecko timestamp"),
    )


def parse_cmc_quote(payload: object, symbol: str) -> CmcReading:
    body = _mapping(payload, "cmc")
    data = _child(body, "data")
    if data is None or symbol not in data or data[symbol] is None:
        return CmcReading(market_cap_usd=None, source_timestamp=None)
    quote = _child(_mapping(data[symbol], symbol), "quote")
    usd = None if quote is None else _child(quote, "USD")
    if usd is None:
        return CmcReading(market_cap_usd=None, source_timestamp=None)
    return CmcReading(
        market_cap_usd=_number(usd.get("market_cap"), "cmc market cap"),
        source_timestamp=_clock(usd.get("last_updated"), "cmc timestamp"),
    )


def parse_defillama_fees(payload: object) -> DefiLlamaFees:
    body = _mapping(payload, "defillama")
    return DefiLlamaFees(
        fees_24h_usd=_number(body.get("total24h"), "fees"),
        revenue_24h_usd=_number(body.get("totalRevenue24h"), "revenue"),
    )


def assess_fundamentals(
    *,
    base_asset: str,
    verified_ids: Mapping[str, str],
    coingecko: CoinGeckoReading,
    cmc: CmcReading,
    unlocks: UnlockReading,
    as_of: datetime,
    hypotheses: FundamentalsHypotheses,
) -> FundamentalsDecision:
    """Apply the fundamental gates. Missing numbers stay missing."""
    checked_at = _utc(as_of, "as_of")
    reasons: list[str] = []
    expected = verified_ids.get(base_asset)
    if expected is None or coingecko.coingecko_id is None:
        reasons.append("coingecko_id_unverified")
    elif coingecko.coingecko_id != expected:
        reasons.append("coingecko_id_mismatch")
    reasons.extend(_fresh("coingecko", coingecko.source_timestamp, checked_at, hypotheses))
    reasons.extend(_fresh("cmc", cmc.source_timestamp, checked_at, hypotheses))
    cap = coingecko.market_cap_usd
    if cap is None:
        reasons.append("missing_market_cap")
    elif cap == 0:
        reasons.append("invalid_market_cap")
    cross = cmc.market_cap_usd
    if cross is None:
        reasons.append("missing_cmc_market_cap")
    elif cross == 0:
        reasons.append("invalid_cmc_market_cap")
    elif cap is not None and cap > 0:
        disagreement = abs(cap / cross - 1)
        if disagreement > Decimal(str(hypotheses.mcap_disagreement)):
            reasons.append("mcap_disagreement")
    circulating = coingecko.circulating_supply
    total = coingecko.total_supply
    if circulating is None:
        reasons.append("missing_circulating_supply")
    if total is None or total == 0:
        reasons.append("missing_total_supply")
    elif circulating is not None and circulating > total:
        reasons.append("circulating_exceeds_total")
    fdv = coingecko.fully_diluted_valuation_usd
    if fdv is None:
        reasons.append("missing_fully_diluted_valuation")
    elif cap is not None and cap > 0:
        ceiling = Decimal(str(hypotheses.maximum_fdv_to_market_cap))
        if fdv / cap > ceiling:
            reasons.append("fdv_to_market_cap_above_maximum")
    reasons.extend(_unlocks(circulating, total, unlocks, hypotheses))
    if reasons:
        return FundamentalsDecision(accepted=False, reason_codes=tuple(reasons))
    return FundamentalsDecision(accepted=True, reason_codes=("fundamentals_ok",))


def _fresh(
    source: str,
    timestamp: datetime | None,
    as_of: datetime,
    hypotheses: FundamentalsHypotheses,
) -> tuple[str, ...]:
    if timestamp is None:
        return (f"missing_{source}_timestamp",)
    if timestamp > as_of:
        return (f"{source}_timestamp_in_the_future",)
    age = as_of - timestamp
    if age > timedelta(hours=hypotheses.data_max_age_hours):
        return (f"stale_{source}",)
    return ()


def _unlocks(
    circulating: Decimal | None,
    total: Decimal | None,
    unlocks: UnlockReading,
    hypotheses: FundamentalsHypotheses,
) -> tuple[str, ...]:
    if unlocks.schedule_known is None:
        return ("missing_unlock_schedule",)
    reasons: list[str] = []
    if unlocks.schedule_known:
        reasons.extend(_known_unlock(unlocks.pct_circ_14d, hypotheses.unlock_pct_circ_14d, "14d"))
        reasons.extend(_known_unlock(unlocks.pct_circ_90d, hypotheses.unlock_pct_circ_90d, "90d"))
        return tuple(reasons)
    if circulating is None or total is None or total == 0:
        return ("missing_circulating_ratio",)
    ratio = circulating / total
    floor = Decimal(str(hypotheses.minimum_circulating_ratio_without_unlocks))
    if ratio < floor:
        reasons.append("circulating_ratio_without_unlocks")
    return tuple(reasons)


def _known_unlock(value: Decimal | None, limit: float, horizon: str) -> tuple[str, ...]:
    if value is None:
        return (f"missing_unlock_{horizon}",)
    if value >= Decimal(str(limit)):
        return (f"unlock_{horizon}",)
    return ()
