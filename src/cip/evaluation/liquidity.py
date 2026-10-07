from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, field_validator

from cip.domain.policy import UniverseHypotheses
from cip.evaluation.eligibility import (
    CandidateFacts,
    EligibilityDecision,
    Lane,
    assess,
)

_LANE_CODES = frozenset({"normal_lane", "high_risk_lane"})


def _finite_decimal(value: object) -> Decimal:
    if isinstance(value, (bool, float, int)):
        raise ValueError("decimal values are Decimal or decimal strings")
    if isinstance(value, str):
        try:
            parsed = Decimal(value)
        except InvalidOperation as error:
            raise ValueError("decimal values are Decimal or decimal strings") from error
        if not parsed.is_finite():
            raise ValueError("decimal values are finite")
        return parsed
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("decimal values are finite")
        return value
    raise ValueError("decimal values are Decimal or decimal strings")


def _optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    return _finite_decimal(value)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError("counts are zero or more")
    return value


def _utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{label} must be timezone-aware UTC")
    return value.astimezone(UTC)


def _threshold(value: float) -> Decimal:
    return Decimal(str(value))


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class MarketSnapshot(_Strict):
    """Liquidity and manipulation inputs. Listing age stays on the eligibility facts."""

    as_of: datetime
    median_quote_volume_30d_usd: Annotated[Decimal | None, BeforeValidator(_optional_decimal)]
    day_quote_volume_usd: Annotated[Decimal | None, BeforeValidator(_optional_decimal)]
    median_spread_bps: Annotated[Decimal | None, BeforeValidator(_optional_decimal)]
    spread_snapshots: Annotated[int | None, BeforeValidator(_optional_int)]
    depth_usd_per_side: Annotated[Decimal | None, BeforeValidator(_optional_decimal)]
    turnover: Annotated[Decimal | None, BeforeValidator(_optional_decimal)]
    volume_zscore: Annotated[Decimal | None, BeforeValidator(_optional_decimal)]
    price_move: Annotated[Decimal | None, BeforeValidator(_optional_decimal)]
    spike_candle_count: Annotated[int | None, BeforeValidator(_optional_int)]
    trade_size_stdev: Annotated[Decimal | None, BeforeValidator(_optional_decimal)]
    taker_buy_ratio: Annotated[Decimal | None, BeforeValidator(_optional_decimal)]
    binance_volume_share: Annotated[Decimal | None, BeforeValidator(_optional_decimal)]
    stablecoin_peg_deviation: Annotated[Decimal | None, BeforeValidator(_optional_decimal)]
    peg_deviation_hours: Annotated[int | None, BeforeValidator(_optional_int)]
    manipulation_blocked_until: datetime | None = None

    @field_validator("as_of", "manipulation_blocked_until")
    @classmethod
    def _clock(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _utc(value, "market clock")


def assess_market(
    facts: CandidateFacts, market: MarketSnapshot, universe: UniverseHypotheses
) -> EligibilityDecision:
    """Add liquidity and manipulation reasons. This calls eligibility and does not copy it."""
    decision = assess(facts, universe)
    if decision.lane is None:
        return decision
    extra = _liquidity(decision.lane, market, universe) + _manipulation(market, universe)
    if not extra:
        return decision
    kept = tuple(code for code in decision.reason_codes if code not in _LANE_CODES)
    return EligibilityDecision(eligible=False, lane=decision.lane, reason_codes=kept + extra)


def _liquidity(lane: Lane, market: MarketSnapshot, universe: UniverseHypotheses) -> tuple[str, ...]:
    normal = universe.normal
    high = universe.high_risk
    volume_floor = (
        normal.median_quote_volume_30d_usd
        if lane is Lane.NORMAL
        else high.median_quote_volume_30d_usd
    )
    spread_ceiling = (
        normal.maximum_median_spread_bps if lane is Lane.NORMAL else high.maximum_median_spread_bps
    )
    depth_floor = (
        normal.minimum_depth_usd_per_side
        if lane is Lane.NORMAL
        else high.minimum_depth_usd_per_side
    )
    reasons: list[str] = []
    reasons.extend(
        _at_least(
            market.median_quote_volume_30d_usd,
            volume_floor,
            missing="missing_median_quote_volume",
            below="median_quote_volume_below_minimum",
        )
    )
    if lane is Lane.NORMAL:
        reasons.extend(
            _at_least(
                market.day_quote_volume_usd,
                normal.minimum_day_quote_volume_usd,
                missing="missing_day_quote_volume",
                below="day_quote_volume_below_minimum",
            )
        )
    reasons.extend(
        _at_most(
            market.median_spread_bps,
            spread_ceiling,
            missing="missing_median_spread",
            above="median_spread_above_maximum",
        )
    )
    if lane is Lane.NORMAL:
        reasons.extend(
            _count_at_least(
                market.spread_snapshots,
                normal.minimum_spread_snapshots,
                missing="missing_spread_snapshots",
                below="spread_snapshots_below_minimum",
            )
        )
    reasons.extend(
        _at_least(
            market.depth_usd_per_side,
            depth_floor,
            missing="missing_depth",
            below="depth_below_minimum",
        )
    )
    reasons.extend(_turnover(lane, market.turnover, universe))
    return tuple(reasons)


def _manipulation(market: MarketSnapshot, universe: UniverseHypotheses) -> tuple[str, ...]:
    rules = universe.manipulation
    reasons: list[str] = []
    if market.turnover is not None and market.turnover > _threshold(rules.turnover_above):
        reasons.append("manipulation_turnover")
    if market.volume_zscore is None:
        reasons.append("missing_volume_zscore")
    if market.price_move is None:
        reasons.append("missing_price_move")
    else:
        move = abs(market.price_move)
        zscore = market.volume_zscore
        wash_volume = zscore is not None and zscore > _threshold(rules.volume_zscore_above)
        if wash_volume and move < _threshold(rules.price_move_below):
            reasons.append("wash_volume")
        taker = market.taker_buy_ratio
        if (
            taker is not None
            and taker > _threshold(rules.taker_buy_ratio_above)
            and move >= _threshold(rules.price_move_below)
        ):
            reasons.append("taker_buy_vertical")
    if market.taker_buy_ratio is None:
        reasons.append("missing_taker_buy_ratio")
    if market.spike_candle_count is None:
        reasons.append("missing_spike_candle_count")
    elif rules.spike_candle_floor < market.spike_candle_count < rules.spike_candle_ceiling:
        reasons.append("spike_candles")
    if market.trade_size_stdev is None:
        reasons.append("missing_trade_size_stdev")
    elif market.trade_size_stdev > _threshold(rules.trade_size_stdev_above):
        reasons.append("trade_size_outlier")
    if market.binance_volume_share is None:
        reasons.append("missing_binance_volume_share")
    else:
        share = market.binance_volume_share
        above = share > _threshold(rules.binance_volume_share_above)
        below = share < _threshold(rules.binance_volume_share_below)
        if above or below:
            reasons.append("binance_volume_share_outside_band")
    if market.stablecoin_peg_deviation is None or market.peg_deviation_hours is None:
        reasons.append("missing_stablecoin_peg")
    elif (
        market.stablecoin_peg_deviation > _threshold(rules.stablecoin_peg_deviation)
        and market.peg_deviation_hours >= rules.peg_deviation_hours
    ):
        reasons.append("stablecoin_peg")
    blocked_until = market.manipulation_blocked_until
    if blocked_until is not None and market.as_of < blocked_until:
        reasons.append("manipulation_block")
    return tuple(reasons)


def _turnover(lane: Lane, value: Decimal | None, universe: UniverseHypotheses) -> tuple[str, ...]:
    if value is None:
        return ("missing_turnover",)
    reasons: list[str] = []
    if lane is Lane.NORMAL:
        normal = universe.normal
        if value < _threshold(normal.turnover_min):
            reasons.append("turnover_below_minimum")
        elif value > _threshold(normal.turnover_max):
            reasons.append("turnover_above_maximum")
    elif value > _threshold(universe.high_risk.turnover_flag_above):
        reasons.append("turnover_above_flag")
    return tuple(reasons)


def _at_least(
    value: Decimal | None, minimum: float, *, missing: str, below: str
) -> tuple[str, ...]:
    if value is None:
        return (missing,)
    if value < _threshold(minimum):
        return (below,)
    return ()


def _at_most(value: Decimal | None, maximum: float, *, missing: str, above: str) -> tuple[str, ...]:
    if value is None:
        return (missing,)
    if value > _threshold(maximum):
        return (above,)
    return ()


def _count_at_least(
    value: int | None, minimum: int, *, missing: str, below: str
) -> tuple[str, ...]:
    if value is None:
        return (missing,)
    if value < minimum:
        return (below,)
    return ()
