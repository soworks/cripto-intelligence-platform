from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from cip.domain.policy import load_policy
from cip.evaluation.eligibility import CandidateFacts, Lane, assess
from cip.evaluation.liquidity import MarketSnapshot, assess_market

_POLICY = Path(__file__).parents[3] / "policies" / "investment-policy.yaml"
UNIVERSE = load_policy(_POLICY).policy.hypotheses.universe
NOW = datetime(2026, 10, 4, tzinfo=UTC)


def _money(value: float) -> Decimal:
    return Decimal(str(value))


def _facts(**overrides: object) -> CandidateFacts:
    lane = UNIVERSE.normal
    values: dict[str, object] = {
        "symbol": "SOLUSDT",
        "base_asset": "SOL",
        "quote_asset": "USDT",
        "status": "TRADING",
        "eur_stable": False,
        "fan_token": False,
        "monitoring_tag": False,
        "delisting": False,
        "deposits_suspended": False,
        "withdrawals_suspended": False,
        "pending_migration": False,
        "market_cap_usd": _money(lane.minimum_market_cap_usd),
        "market_cap_rank": lane.market_cap_rank_ceiling,
        "circulating_ratio": _money(lane.minimum_circulating_ratio),
        "fdv_to_market_cap": _money(lane.maximum_fdv_to_market_cap),
        "history_days": lane.minimum_history_days,
        "unlock_schedule_known": None,
    }
    values.update(overrides)
    return CandidateFacts(**values)  # type: ignore[arg-type]


def _high_facts(**overrides: object) -> CandidateFacts:
    lane = UNIVERSE.high_risk
    values: dict[str, object] = {
        "market_cap_usd": _money(UNIVERSE.normal.minimum_market_cap_usd) - 1,
        "market_cap_rank": None,
        "circulating_ratio": _money(lane.minimum_circulating_ratio),
        "fdv_to_market_cap": None,
        "history_days": lane.minimum_history_days,
        "unlock_schedule_known": True,
    }
    values.update(overrides)
    return _facts(**values)


def _market(**overrides: object) -> MarketSnapshot:
    normal = UNIVERSE.normal
    rules = UNIVERSE.manipulation
    low = _money(rules.binance_volume_share_below)
    high = _money(rules.binance_volume_share_above)
    share = (low + high) / 2
    values: dict[str, object] = {
        "as_of": NOW,
        "median_quote_volume_30d_usd": _money(normal.median_quote_volume_30d_usd),
        "day_quote_volume_usd": _money(normal.minimum_day_quote_volume_usd),
        "median_spread_bps": _money(normal.maximum_median_spread_bps),
        "spread_snapshots": normal.minimum_spread_snapshots,
        "depth_usd_per_side": _money(normal.minimum_depth_usd_per_side),
        "turnover": _money(normal.turnover_min),
        "volume_zscore": Decimal("0"),
        "price_move": Decimal("0"),
        "spike_candle_count": rules.spike_candle_ceiling,
        "trade_size_stdev": Decimal("0"),
        "taker_buy_ratio": Decimal("0"),
        "binance_volume_share": share,
        "stablecoin_peg_deviation": Decimal("0"),
        "peg_deviation_hours": 0,
        "manipulation_blocked_until": None,
    }
    values.update(overrides)
    return MarketSnapshot(**values)  # type: ignore[arg-type]


def _high_market(**overrides: object) -> MarketSnapshot:
    high = UNIVERSE.high_risk
    values: dict[str, object] = {
        "median_quote_volume_30d_usd": _money(high.median_quote_volume_30d_usd),
        "day_quote_volume_usd": None,
        "median_spread_bps": _money(high.maximum_median_spread_bps),
        "spread_snapshots": None,
        "depth_usd_per_side": _money(high.minimum_depth_usd_per_side),
        "turnover": _money(high.turnover_flag_above),
    }
    values.update(overrides)
    return _market(**values)


def test_a_liquid_normal_symbol_stays_eligible_and_is_not_a_buy() -> None:
    floor = format(_money(UNIVERSE.normal.median_quote_volume_30d_usd), "f")
    result = assess_market(_facts(), _market(median_quote_volume_30d_usd=floor), UNIVERSE)
    assert result.eligible is True
    assert result.lane is Lane.NORMAL
    assert result.reason_codes == ("normal_lane",)


def test_high_risk_liquidity_uses_the_high_risk_thresholds() -> None:
    result = assess_market(_high_facts(), _high_market(), UNIVERSE)
    assert result.reason_codes == ("high_risk_lane",)
    thin = _money(UNIVERSE.high_risk.median_quote_volume_30d_usd) - 1
    refused = assess_market(_high_facts(), _high_market(median_quote_volume_30d_usd=thin), UNIVERSE)
    assert refused.reason_codes == ("median_quote_volume_below_minimum",)


def test_high_risk_turnover_above_the_flag_is_refused() -> None:
    flagged = _money(UNIVERSE.high_risk.turnover_flag_above) + Decimal("0.01")
    result = assess_market(_high_facts(), _high_market(turnover=flagged), UNIVERSE)
    assert result.reason_codes == ("turnover_above_flag", "manipulation_turnover")


def test_a_short_history_is_refused_once_and_is_not_a_buy() -> None:
    facts = _facts(history_days=UNIVERSE.normal.minimum_history_days - 1)
    result = assess_market(facts, _market(), UNIVERSE)
    assert result.eligible is False
    assert result.reason_codes == ("history_below_minimum",)
    assert result.reason_codes == assess(facts, UNIVERSE).reason_codes


def test_no_lane_skips_liquidity() -> None:
    cap = _money(UNIVERSE.high_risk.minimum_market_cap_usd) - 1
    result = assess_market(_facts(market_cap_usd=cap), _market(depth_usd_per_side=None), UNIVERSE)
    assert result.reason_codes == ("below_market_cap",)


def test_normal_liquidity_failures_keep_the_lane() -> None:
    normal = UNIVERSE.normal
    cases = {
        "median_quote_volume_30d_usd": (
            None,
            _money(normal.median_quote_volume_30d_usd) - 1,
            "missing_median_quote_volume",
            "median_quote_volume_below_minimum",
        ),
        "day_quote_volume_usd": (
            None,
            _money(normal.minimum_day_quote_volume_usd) - 1,
            "missing_day_quote_volume",
            "day_quote_volume_below_minimum",
        ),
        "median_spread_bps": (
            None,
            _money(normal.maximum_median_spread_bps) + 1,
            "missing_median_spread",
            "median_spread_above_maximum",
        ),
        "spread_snapshots": (
            None,
            normal.minimum_spread_snapshots - 1,
            "missing_spread_snapshots",
            "spread_snapshots_below_minimum",
        ),
        "depth_usd_per_side": (
            None,
            _money(normal.minimum_depth_usd_per_side) - 1,
            "missing_depth",
            "depth_below_minimum",
        ),
    }
    for field, (missing, breach, missing_code, breach_code) in cases.items():
        assert assess_market(_facts(), _market(**{field: missing}), UNIVERSE).reason_codes == (
            missing_code,
        )
        assert assess_market(_facts(), _market(**{field: breach}), UNIVERSE).reason_codes == (
            breach_code,
        )
    low = _money(normal.turnover_min) - Decimal("0.001")
    high = _money(normal.turnover_max) + Decimal("0.01")
    assert assess_market(_facts(), _market(turnover=None), UNIVERSE).reason_codes == (
        "missing_turnover",
    )
    assert assess_market(_facts(), _market(turnover=low), UNIVERSE).reason_codes == (
        "turnover_below_minimum",
    )
    assert assess_market(_facts(), _market(turnover=high), UNIVERSE).reason_codes == (
        "turnover_above_maximum",
        "manipulation_turnover",
    )


@pytest.mark.parametrize(
    ("count", "reasons"),
    [
        (None, ("missing_spike_candle_count",)),
        (0, ("normal_lane",)),
        (1, ("spike_candles",)),
        (2, ("spike_candles",)),
        (3, ("normal_lane",)),
        (4, ("normal_lane",)),
    ],
)
def test_spike_count_flags_only_a_concentrated_event(
    count: int | None, reasons: tuple[str, ...]
) -> None:
    rules = UNIVERSE.manipulation
    assert (rules.spike_candle_floor, rules.spike_candle_ceiling) == (0, 3)
    result = assess_market(_facts(), _market(spike_candle_count=count), UNIVERSE)
    assert result.reason_codes == reasons


def test_manipulation_heuristics_block_without_an_order() -> None:
    rules = UNIVERSE.manipulation
    wash = assess_market(
        _facts(),
        _market(
            volume_zscore=_money(rules.volume_zscore_above) + 1,
            price_move=Decimal("-0.01"),
        ),
        UNIVERSE,
    )
    assert wash.reason_codes == ("wash_volume",)
    vertical = assess_market(
        _facts(),
        _market(
            price_move=_money(rules.price_move_below),
            taker_buy_ratio=_money(rules.taker_buy_ratio_above) + Decimal("0.01"),
        ),
        UNIVERSE,
    )
    assert vertical.reason_codes == ("taker_buy_vertical",)
    assert assess_market(
        _facts(), _market(spike_candle_count=rules.spike_candle_ceiling - 1), UNIVERSE
    ).reason_codes == ("spike_candles",)
    assert assess_market(
        _facts(),
        _market(trade_size_stdev=_money(rules.trade_size_stdev_above) + 1),
        UNIVERSE,
    ).reason_codes == ("trade_size_outlier",)
    assert assess_market(
        _facts(),
        _market(binance_volume_share=_money(rules.binance_volume_share_above) + Decimal("0.01")),
        UNIVERSE,
    ).reason_codes == ("binance_volume_share_outside_band",)
    assert assess_market(
        _facts(),
        _market(binance_volume_share=_money(rules.binance_volume_share_below) - Decimal("0.01")),
        UNIVERSE,
    ).reason_codes == ("binance_volume_share_outside_band",)
    assert assess_market(
        _facts(),
        _market(
            stablecoin_peg_deviation=_money(rules.stablecoin_peg_deviation) + Decimal("0.001"),
            peg_deviation_hours=rules.peg_deviation_hours,
        ),
        UNIVERSE,
    ).reason_codes == ("stablecoin_peg",)
    assert (
        assess_market(
            _facts(),
            _market(
                stablecoin_peg_deviation=_money(rules.stablecoin_peg_deviation),
                peg_deviation_hours=rules.peg_deviation_hours,
            ),
            UNIVERSE,
        ).eligible
        is True
    )
    blocked = assess_market(
        _facts(),
        _market(manipulation_blocked_until=NOW + timedelta(seconds=1)),
        UNIVERSE,
    )
    assert blocked.reason_codes == ("manipulation_block",)
    assert assess_market(_facts(), _market(manipulation_blocked_until=NOW), UNIVERSE).eligible


def test_missing_manipulation_inputs_fail_closed() -> None:
    assert assess_market(_facts(), _market(volume_zscore=None), UNIVERSE).reason_codes == (
        "missing_volume_zscore",
    )
    assert assess_market(_facts(), _market(price_move=None), UNIVERSE).reason_codes == (
        "missing_price_move",
    )
    assert assess_market(_facts(), _market(taker_buy_ratio=None), UNIVERSE).reason_codes == (
        "missing_taker_buy_ratio",
    )
    assert assess_market(_facts(), _market(spike_candle_count=None), UNIVERSE).reason_codes == (
        "missing_spike_candle_count",
    )
    assert assess_market(_facts(), _market(trade_size_stdev=None), UNIVERSE).reason_codes == (
        "missing_trade_size_stdev",
    )
    assert assess_market(_facts(), _market(binance_volume_share=None), UNIVERSE).reason_codes == (
        "missing_binance_volume_share",
    )
    missing_peg = assess_market(_facts(), _market(stablecoin_peg_deviation=None), UNIVERSE)
    assert missing_peg.reason_codes == ("missing_stablecoin_peg",)
    assert assess_market(_facts(), _market(peg_deviation_hours=None), UNIVERSE).reason_codes == (
        "missing_stablecoin_peg",
    )


def test_an_exclusion_and_a_thin_book_are_both_recorded() -> None:
    stable = UNIVERSE.exclusions.stablecoin_symbols[0]
    result = assess_market(
        _facts(symbol=f"{stable}USDT", base_asset=stable),
        _market(depth_usd_per_side=Decimal("0")),
        UNIVERSE,
    )
    assert result.eligible is False
    assert result.reason_codes == ("stablecoin", "depth_below_minimum")


def test_malformed_market_inputs_are_rejected() -> None:
    with pytest.raises(ValidationError):
        _market(median_quote_volume_30d_usd=0.1 + 0.2)
    with pytest.raises(ValidationError):
        _market(median_quote_volume_30d_usd=True)
    with pytest.raises(ValidationError):
        _market(median_quote_volume_30d_usd="nope")
    with pytest.raises(ValidationError):
        _market(median_quote_volume_30d_usd="NaN")
    with pytest.raises(ValidationError):
        _market(median_quote_volume_30d_usd=Decimal("NaN"))
    with pytest.raises(ValidationError):
        _market(median_quote_volume_30d_usd=object())
    with pytest.raises(ValidationError):
        _market(spread_snapshots=True)
    with pytest.raises(ValidationError):
        _market(spread_snapshots=-1)
    with pytest.raises(ValidationError):
        _market(as_of=datetime(2026, 10, 4))  # noqa: DTZ001
    ahead = timezone(timedelta(hours=1))
    with pytest.raises(ValidationError):
        _market(as_of=datetime(2026, 10, 4, tzinfo=ahead))
    with pytest.raises(ValidationError):
        _market(manipulation_blocked_until=datetime(2026, 10, 5, tzinfo=ahead))
