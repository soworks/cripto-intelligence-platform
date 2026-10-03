from __future__ import annotations

import hashlib
from collections.abc import Hashable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictStr,
    ValidationError,
    model_validator,
)

from cip.domain.errors import PolicyError


def _exact_type(kind: type) -> BeforeValidator:
    # Strict Literal still treats 1 == True, so pin the YAML scalar type first.
    def check(value: object) -> object:
        if type(value) is not kind:
            raise ValueError(f"must be a {kind.__name__}")
        return value

    return BeforeValidator(check)


Fraction = Annotated[float, Field(gt=0, le=1)]
PositiveUsd = Annotated[float, Field(gt=0)]
Symbols = Annotated[tuple[StrictStr, ...], Field(strict=False)]
AlwaysTrue = Annotated[Literal[True], _exact_type(bool)]
AlwaysFalse = Annotated[Literal[False], _exact_type(bool)]

# Hard ceilings far above the reference policy, so a typo cannot authorise an outsized trade.
MAX_TRADE_USD_CEILING = 10_000
MAX_DAILY_TRADE_USD_CEILING = 25_000
MAX_MONTHLY_TRADE_USD_CEILING = 100_000


class ExecutionMode(StrEnum):
    SHADOW = "SHADOW"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    LIVE_DISABLED = "LIVE_DISABLED"


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, allow_inf_nan=False)


class UniversePolicy(_Strict):
    quote_assets: Annotated[Symbols, Field(min_length=1)]
    exclude_stablecoins: bool
    exclude_leveraged_tokens: bool
    minimum_trading_history_days: int = Field(ge=0)
    minimum_daily_quote_volume_usd: PositiveUsd
    minimum_market_cap_usd: PositiveUsd
    maximum_spread_bps: float = Field(gt=0, le=1000)


class DiscoveryPolicy(_Strict):
    max_quant_candidates: int = Field(ge=1)
    max_enriched_candidates: int = Field(ge=1)
    max_llm_candidates_per_scan: int = Field(ge=0)
    new_listing_observation_days: int = Field(ge=0)

    @model_validator(mode="after")
    def _funnel_narrows(self) -> Self:
        if not (
            self.max_quant_candidates
            >= self.max_enriched_candidates
            >= self.max_llm_candidates_per_scan
        ):
            raise ValueError("discovery funnel must narrow: quant >= enriched >= llm")
        return self


class PortfolioPolicy(_Strict):
    core_assets: Symbols
    discovery_max_portfolio_pct: Fraction


class TradingPolicy(_Strict):
    spot_only: AlwaysTrue
    margin_enabled: AlwaysFalse
    futures_enabled: AlwaysFalse
    leverage_enabled: AlwaysFalse
    withdrawals_enabled: AlwaysFalse


class RiskPolicy(_Strict):
    max_trade_usd: Annotated[PositiveUsd, Field(le=MAX_TRADE_USD_CEILING)]
    max_trade_portfolio_pct: Fraction
    minimum_cash_reserve_pct: Fraction
    max_discovery_asset_pct: Fraction
    max_daily_trade_usd: Annotated[PositiveUsd, Field(le=MAX_DAILY_TRADE_USD_CEILING)]
    max_monthly_trade_usd: Annotated[PositiveUsd, Field(le=MAX_MONTHLY_TRADE_USD_CEILING)]

    @model_validator(mode="after")
    def _limits_nest(self) -> Self:
        if not (self.max_trade_usd <= self.max_daily_trade_usd <= self.max_monthly_trade_usd):
            raise ValueError("trade limits must nest: per-trade <= daily <= monthly")
        return self


class AiPolicy(_Strict):
    max_calls_per_day: int = Field(ge=0)
    max_calls_per_month: int = Field(ge=0)

    @model_validator(mode="after")
    def _daily_within_monthly(self) -> Self:
        if self.max_calls_per_day > self.max_calls_per_month:
            raise ValueError("max_calls_per_day cannot exceed max_calls_per_month")
        return self


class ExecutionPolicy(_Strict):
    mode: Annotated[ExecutionMode, Field(strict=False)]
    human_approval_required: AlwaysTrue


class InvestmentPolicy(_Strict):
    schema_version: Annotated[Literal[1], _exact_type(int)]
    universe: UniversePolicy
    discovery: DiscoveryPolicy
    portfolio: PortfolioPolicy
    trading: TradingPolicy
    risk: RiskPolicy
    ai: AiPolicy
    execution: ExecutionPolicy


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys instead of keeping the last one."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Hashable, Any]:
        mapping = super().construct_mapping(node, deep=deep)
        seen: set[Hashable] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            seen.add(key)
        return mapping


@dataclass(frozen=True)
class LoadedPolicy:
    policy: InvestmentPolicy
    version: str


def load_policy(path: Path) -> LoadedPolicy:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise PolicyError(f"cannot read policy file {path}") from error
    try:
        document = yaml.load(raw, Loader=_UniqueKeyLoader)  # noqa: S506 - SafeLoader subclass
        policy = InvestmentPolicy.model_validate(document)
    except (yaml.YAMLError, ValidationError) as error:
        raise PolicyError(f"invalid policy file {path}: {error}") from error
    return LoadedPolicy(policy=policy, version=hashlib.sha256(raw).hexdigest())
