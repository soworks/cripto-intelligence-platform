from __future__ import annotations

import hashlib
from collections.abc import Hashable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
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
CapitalUsd = Annotated[float, Field(gt=0, le=1_000_000)]
FeeRate = Annotated[float, Field(gt=0, le=0.01)]
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


def _cents(amount: float, *fractions: float) -> Decimal:
    product = Decimal(str(amount))
    for fraction in fractions:
        product *= Decimal(str(fraction))
    return product.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class ContributionSplit(_Strict):
    core: Fraction
    discovery: Fraction
    reserve: Fraction

    @model_validator(mode="after")
    def _sums_to_one(self) -> Self:
        total = self.core + self.discovery + self.reserve
        if abs(total - 1.0) > 1e-9:
            raise ValueError("contribution_split must sum to 1")
        return self


class PortfolioPolicy(_Strict):
    core_assets: Symbols
    discovery_max_portfolio_pct: Fraction
    starting_value_usd: CapitalUsd
    holdings_are_approximate: bool
    holdings_detail_due: date | None = None
    monthly_contribution_usd: CapitalUsd
    contribution_split: ContributionSplit
    core_mix: dict[StrictStr, Fraction]

    @model_validator(mode="after")
    def _holdings_and_mix(self) -> Self:
        if self.holdings_are_approximate and self.holdings_detail_due is None:
            raise ValueError("holdings_detail_due is required while holdings are approximate")
        if set(self.core_assets) != {"BTCUSDT", "ETHUSDT"}:
            raise ValueError("core_assets must be BTCUSDT and ETHUSDT")
        if set(self.core_mix) != set(self.core_assets):
            raise ValueError("core_mix keys must match core_assets")
        if abs(sum(self.core_mix.values()) - 1.0) > 1e-9:
            raise ValueError("core_mix must sum to 1")
        object.__setattr__(self, "core_mix", MappingProxyType(dict(self.core_mix)))
        return self

    @property
    def core_monthly_usd(self) -> Decimal:
        return _cents(self.monthly_contribution_usd, self.contribution_split.core)

    @property
    def discovery_monthly_usd(self) -> Decimal:
        return _cents(self.monthly_contribution_usd, self.contribution_split.discovery)

    @property
    def reserve_monthly_usd(self) -> Decimal:
        return _cents(self.monthly_contribution_usd, self.contribution_split.reserve)

    @property
    def core_btc_monthly_usd(self) -> Decimal:
        return _cents(
            self.monthly_contribution_usd,
            self.contribution_split.core,
            self.core_mix["BTCUSDT"],
        )

    @property
    def core_eth_monthly_usd(self) -> Decimal:
        return _cents(
            self.monthly_contribution_usd,
            self.contribution_split.core,
            self.core_mix["ETHUSDT"],
        )


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


class VenuePolicy(_Strict):
    execution_venue: Literal["binance.com"]
    market_data_base_url: Literal["https://data-api.binance.vision"]
    maker_fee_rate: FeeRate
    taker_fee_rate: FeeRate


class TaxPolicy(_Strict):
    residency: Literal["CO"]
    cost_basis_method: Literal["FIFO"]


class DataPolicy(_Strict):
    monthly_budget_usd: Annotated[Literal[0], _exact_type(int)]


class StrategyPolicy(_Strict):
    bar_interval: Literal["1d"]
    bar_close: Literal["00:00:00Z"]
    min_holding_days: int = Field(ge=1)
    max_holding_days: int = Field(ge=1, le=3650)
    kill_after_closed_trades: int = Field(ge=1, le=100_000)
    kill_after_months: int = Field(ge=1, le=120)

    @model_validator(mode="after")
    def _holding_window(self) -> Self:
        if self.max_holding_days < self.min_holding_days:
            raise ValueError("max_holding_days must be >= min_holding_days")
        return self


NonNegativeFraction = Annotated[float, Field(ge=0, le=1)]
PositiveNumber = Annotated[float, Field(gt=0)]
Score = Annotated[int, Field(ge=0, le=100)]
ForcedExit = Literal[
    "delisting_announced",
    "monitoring_tag_added",
    "regime_risk_off",
    "unlock_pct_circ_within_7d_ge_0.02",
    "rs_rank_percentile_below_0.40",
]
RevalidateField = Literal["regime", "gates", "filters", "portfolio", "stablecoin_peg"]
FundamentalField = Literal["market_cap", "circulating_supply", "coingecko_id_verified"]
_REQUIRED_EXITS = frozenset(
    {
        "delisting_announced",
        "monitoring_tag_added",
        "regime_risk_off",
        "unlock_pct_circ_within_7d_ge_0.02",
        "rs_rank_percentile_below_0.40",
    }
)
_REQUIRED_REVALIDATE = frozenset({"regime", "gates", "filters", "portfolio", "stablecoin_peg"})
_REQUIRED_FUNDAMENTALS = frozenset({"market_cap", "circulating_supply", "coingecko_id_verified"})


def _unique(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} contains a duplicate")
    return values


class NormalLane(_Strict):
    median_quote_volume_30d_usd: PositiveUsd
    minimum_day_quote_volume_usd: PositiveUsd
    minimum_market_cap_usd: PositiveUsd
    market_cap_rank_ceiling: int = Field(ge=1)
    minimum_circulating_ratio: Fraction
    maximum_fdv_to_market_cap: PositiveNumber
    minimum_history_days: int = Field(ge=1)
    maximum_median_spread_bps: PositiveNumber
    minimum_spread_snapshots: int = Field(ge=1)
    minimum_depth_usd_per_side: PositiveUsd
    turnover_min: Fraction
    turnover_max: Fraction

    @model_validator(mode="after")
    def _turnover_band(self) -> Self:
        if self.turnover_min >= self.turnover_max:
            raise ValueError("turnover_min must be below turnover_max")
        return self


class HighRiskLane(_Strict):
    median_quote_volume_30d_usd: PositiveUsd
    minimum_market_cap_usd: PositiveUsd
    maximum_market_cap_usd: PositiveUsd
    minimum_circulating_ratio: Fraction
    require_known_unlock_schedule: AlwaysTrue
    minimum_history_days: int = Field(ge=1)
    maximum_median_spread_bps: PositiveNumber
    minimum_depth_usd_per_side: PositiveUsd
    turnover_flag_above: Fraction

    @model_validator(mode="after")
    def _cap_band(self) -> Self:
        if self.minimum_market_cap_usd >= self.maximum_market_cap_usd:
            raise ValueError("high-risk market-cap band is empty")
        return self


class ExclusionHypotheses(_Strict):
    stablecoin_symbols: Annotated[Symbols, Field(min_length=1)]
    exclude_eur_stables: AlwaysTrue
    wrapped_symbols: Annotated[Symbols, Field(min_length=1)]
    exclude_fan_tokens: AlwaysTrue
    exclude_non_trading: AlwaysTrue
    exclude_monitoring_tag: AlwaysTrue
    exclude_delisting: AlwaysTrue
    exclude_suspended_deposits: AlwaysTrue
    exclude_suspended_withdrawals: AlwaysTrue
    exclude_pending_migration: AlwaysTrue

    @model_validator(mode="after")
    def _unique_symbols(self) -> Self:
        _unique(self.stablecoin_symbols, "stablecoin_symbols")
        _unique(self.wrapped_symbols, "wrapped_symbols")
        return self


class SpikeCandleFormula(_Strict):
    """Parameters that reproduce spike_candle_count. The z-score value lives on the parent."""

    baseline_days: int = Field(ge=1)
    minimum_daily_bars: int = Field(ge=2)
    event_interval: Literal["1d"]
    concentration_interval: Literal["1h"]
    exact_hours: int = Field(ge=1)
    zscore_threshold: Literal["volume_zscore_above"]
    variance: Literal["sample_n_minus_1"]
    material_hour: Literal["daily_mean_over_exact_hours"]
    comparisons: Literal["strict_greater_than"]
    zero_variance: Literal["missing"]
    hourly_daily_volume: Literal["exact_equal"]
    mismatch: Literal["integrity_failure"]


class ManipulationHypotheses(_Strict):
    turnover_above: Fraction
    volume_zscore_above: PositiveNumber
    price_move_below: Fraction
    spike_candle_floor: int = Field(ge=0)
    spike_candle_ceiling: int = Field(ge=1)
    spike_count: SpikeCandleFormula
    trade_size_stdev_above: PositiveNumber
    taker_buy_ratio_above: Fraction
    escalation_block_days: int = Field(ge=1)
    binance_volume_share_above: Fraction
    binance_volume_share_below: Fraction
    stablecoin_peg_deviation: Fraction
    peg_deviation_hours: int = Field(ge=1)

    @model_validator(mode="after")
    def _share_band(self) -> Self:
        if self.binance_volume_share_below >= self.binance_volume_share_above:
            raise ValueError("binance volume-share band is empty")
        if self.spike_candle_ceiling - self.spike_candle_floor < 2:
            raise ValueError("spike candle band is empty")
        formula = self.spike_count
        if formula.minimum_daily_bars != formula.baseline_days + 1:
            raise ValueError("spike baseline does not match the daily sample")
        if formula.exact_hours != 24:
            raise ValueError("spike hour grid must cover the UTC day")
        return self


class NewListingHypotheses(_Strict):
    high_risk_lane_days: int = Field(ge=1)
    normal_lane_days: int = Field(ge=1)

    @model_validator(mode="after")
    def _lanes_progress(self) -> Self:
        if self.normal_lane_days <= self.high_risk_lane_days:
            raise ValueError("normal lane must be after the high-risk lane")
        return self


class UniverseHypotheses(_Strict):
    normal: NormalLane
    high_risk: HighRiskLane
    exclusions: ExclusionHypotheses
    manipulation: ManipulationHypotheses
    new_listing: NewListingHypotheses

    @model_validator(mode="after")
    def _lanes_meet(self) -> Self:
        if self.high_risk.maximum_market_cap_usd != self.normal.minimum_market_cap_usd:
            raise ValueError("high-risk market-cap ceiling must meet the normal-lane floor")
        return self


class FundamentalsHypotheses(_Strict):
    block_if_missing: Annotated[
        tuple[FundamentalField, ...], Field(min_length=3, max_length=3, strict=False)
    ]
    minimum_circulating_ratio_without_unlocks: Fraction
    unlock_pct_circ_14d: Fraction
    unlock_pct_circ_90d: Fraction
    mcap_disagreement: Fraction
    maximum_fdv_to_market_cap: PositiveNumber
    data_max_age_hours: int = Field(ge=1, le=168)

    @model_validator(mode="after")
    def _required_fields(self) -> Self:
        if set(self.block_if_missing) != _REQUIRED_FUNDAMENTALS:
            raise ValueError("block_if_missing must list the required fundamental fields")
        if self.unlock_pct_circ_14d >= self.unlock_pct_circ_90d:
            raise ValueError("the 14-day unlock gate must be tighter than the 90-day gate")
        return self


class DiscoveryState(_Strict):
    new_entries: bool
    size_mult: Fraction | None = None
    min_score: Score | None = None
    require_rs_vs_btc_30d_positive: bool | None = None
    trailing_atr_mult: PositiveNumber | None = None


class RegimeHypotheses(_Strict):
    hysteresis_days: int = Field(ge=1, le=30)
    breadth_risk_on: Fraction
    breadth_risk_off: Fraction
    btc_drawdown_90d_risk_off: Fraction
    risk_on: DiscoveryState
    neutral: DiscoveryState
    risk_off: DiscoveryState

    @model_validator(mode="after")
    def _states(self) -> Self:
        if self.breadth_risk_off >= self.breadth_risk_on:
            raise ValueError("risk-off breadth must be below risk-on breadth")
        if not (
            self.risk_on.new_entries is True
            and self.risk_on.size_mult == 1
            and self.risk_on.min_score is not None
        ):
            raise ValueError("RISK_ON must allow entries at full size with a minimum score")
        if not (
            self.neutral.new_entries is True
            and self.neutral.size_mult is not None
            and self.risk_on.min_score is not None
            and self.neutral.min_score is not None
            and self.neutral.min_score >= self.risk_on.min_score
            and self.neutral.size_mult <= self.risk_on.size_mult
            and self.neutral.require_rs_vs_btc_30d_positive is True
        ):
            raise ValueError("NEUTRAL must be stricter than RISK_ON")
        if self.risk_off.new_entries is not False or self.risk_off.trailing_atr_mult is None:
            raise ValueError("RISK_OFF must block new entries and set a trailing ATR multiple")
        return self


class SizingHypotheses(_Strict):
    risk_per_trade_pct_of_portfolio: Fraction
    min_position_usd_floor: PositiveUsd
    min_notional_multiple: int = Field(ge=1)


class LimitHypotheses(_Strict):
    max_open_discovery_positions: int = Field(ge=1)
    max_positions_per_sector: int = Field(ge=1)
    beta_weighted_discovery_exposure_max: Fraction
    averaging_down_in_discovery: AlwaysFalse


class CircuitBreakerHypotheses(_Strict):
    halt_new_entries_drawdown: Fraction
    review_and_flatten_drawdown: Fraction
    consecutive_losses: int = Field(ge=1)
    pause_days: int = Field(ge=1)
    same_asset_reentry_cooldown_days: int = Field(ge=1)
    monthly_realized_loss_pct_portfolio: Fraction

    @model_validator(mode="after")
    def _drawdown_steps(self) -> Self:
        if self.halt_new_entries_drawdown >= self.review_and_flatten_drawdown:
            raise ValueError("the flatten drawdown must be past the halt drawdown")
        return self


class ExitHypotheses(_Strict):
    atr_period_days: int = Field(ge=1)
    initial_stop_atr_mult: PositiveNumber
    initial_stop_max_pct: Fraction
    partial_take_profit_r: PositiveNumber
    partial_take_profit_fraction: Fraction
    trailing_atr_mult: PositiveNumber
    trailing_activate_after_r: PositiveNumber
    time_stop_days: int = Field(ge=1)
    time_stop_return_vs_btc_ceiling: NonNegativeFraction
    max_holding_days: int = Field(ge=1, le=3650)
    forced_exit_triggers: Annotated[
        tuple[ForcedExit, ...], Field(min_length=5, max_length=5, strict=False)
    ]

    @model_validator(mode="after")
    def _window_and_triggers(self) -> Self:
        if self.time_stop_days > self.max_holding_days:
            raise ValueError("time_stop_days cannot exceed max_holding_days")
        if set(self.forced_exit_triggers) != _REQUIRED_EXITS:
            raise ValueError("forced_exit_triggers must list each defined trigger once")
        return self


class ApprovalHypotheses(_Strict):
    ttl_minutes: int = Field(ge=1, le=1440)
    adverse_drift_atr_fraction: Fraction
    adverse_drift_floor: Fraction
    favorable_drift_atr_multiple: PositiveNumber
    revalidate_on_execute: Annotated[
        tuple[RevalidateField, ...], Field(min_length=5, max_length=5, strict=False)
    ]
    exits_preauthorized_with_entry: AlwaysTrue

    @model_validator(mode="after")
    def _revalidation(self) -> Self:
        if set(self.revalidate_on_execute) != _REQUIRED_REVALIDATE:
            raise ValueError("revalidate_on_execute must list each defined field once")
        return self


class RecorderPolicy(_Strict):
    """How a recorder measures a book. This is not a trade hypothesis."""

    depth_band_bps: Annotated[int, _exact_type(int), Field(ge=1, le=10_000)]

    @property
    def depth_band(self) -> Decimal:
        return Decimal(self.depth_band_bps) / Decimal(10_000)


class Hypotheses(_Strict):
    universe: UniverseHypotheses
    fundamentals: FundamentalsHypotheses
    regime: RegimeHypotheses
    sizing: SizingHypotheses
    limits: LimitHypotheses
    circuit_breakers: CircuitBreakerHypotheses
    exits: ExitHypotheses
    approval: ApprovalHypotheses


class InvestmentPolicy(_Strict):
    schema_version: Annotated[Literal[2], _exact_type(int)]
    universe: UniversePolicy
    discovery: DiscoveryPolicy
    portfolio: PortfolioPolicy
    trading: TradingPolicy
    risk: RiskPolicy
    ai: AiPolicy
    execution: ExecutionPolicy
    venue: VenuePolicy
    tax: TaxPolicy
    data: DataPolicy
    strategy: StrategyPolicy
    recorders: RecorderPolicy
    hypotheses: Hypotheses

    @model_validator(mode="after")
    def _holding_cap_matches(self) -> Self:
        if self.hypotheses.exits.max_holding_days != self.strategy.max_holding_days:
            raise ValueError("exit max_holding_days must match strategy.max_holding_days")
        return self


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
