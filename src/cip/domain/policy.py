from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from cip.domain.errors import PolicyError

Fraction = Annotated[float, Field(gt=0, le=1)]
PositiveUsd = Annotated[float, Field(gt=0)]


class ExecutionMode(StrEnum):
    SHADOW = "SHADOW"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    LIVE_DISABLED = "LIVE_DISABLED"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class UniversePolicy(_Strict):
    quote_assets: tuple[str, ...] = Field(min_length=1)
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
    core_assets: tuple[str, ...]
    discovery_max_portfolio_pct: Fraction


class TradingPolicy(_Strict):
    spot_only: Literal[True]
    margin_enabled: Literal[False]
    futures_enabled: Literal[False]
    leverage_enabled: Literal[False]
    withdrawals_enabled: Literal[False]


class RiskPolicy(_Strict):
    max_trade_usd: PositiveUsd
    max_trade_portfolio_pct: Fraction
    minimum_cash_reserve_pct: Fraction
    max_discovery_asset_pct: Fraction
    max_daily_trade_usd: PositiveUsd
    max_monthly_trade_usd: PositiveUsd

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
    mode: ExecutionMode
    human_approval_required: Literal[True]


class InvestmentPolicy(_Strict):
    schema_version: Literal[1]
    universe: UniversePolicy
    discovery: DiscoveryPolicy
    portfolio: PortfolioPolicy
    trading: TradingPolicy
    risk: RiskPolicy
    ai: AiPolicy
    execution: ExecutionPolicy


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
        policy = InvestmentPolicy.model_validate(yaml.safe_load(raw))
    except (yaml.YAMLError, ValidationError) as error:
        raise PolicyError(f"invalid policy file {path}: {error}") from error
    return LoadedPolicy(policy=policy, version=hashlib.sha256(raw).hexdigest())
