import copy
import hashlib
import math
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from cip.domain.errors import PolicyError
from cip.domain.policy import ExecutionMode, InvestmentPolicy, load_policy

REPO_POLICY = Path(__file__).parents[3] / "policies" / "investment-policy.yaml"
REPO_DATA: dict[str, Any] = yaml.safe_load(REPO_POLICY.read_text())
USD_LIMITS = ("max_trade_usd", "max_daily_trade_usd", "max_monthly_trade_usd")


@pytest.fixture
def policy_data() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(REPO_POLICY.read_text())
    return data


def _write(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_repository_policy_loads_in_shadow_mode() -> None:
    loaded = load_policy(REPO_POLICY)
    assert loaded.policy.execution.mode is ExecutionMode.SHADOW
    assert loaded.policy.trading.withdrawals_enabled is False


def test_repository_policy_records_owner_decisions() -> None:
    policy = load_policy(REPO_POLICY).policy
    assert policy.schema_version == 2
    portfolio = policy.portfolio
    assert portfolio.starting_value_usd == 650
    assert portfolio.holdings_are_approximate is True
    assert portfolio.holdings_detail_due == date(2026, 10, 5)
    assert portfolio.monthly_contribution_usd == 800
    assert portfolio.core_monthly_usd == Decimal("560.00")
    assert portfolio.discovery_monthly_usd == Decimal("160.00")
    assert portfolio.reserve_monthly_usd == Decimal("80.00")
    assert portfolio.core_btc_monthly_usd == Decimal("392.00")
    assert portfolio.core_eth_monthly_usd == Decimal("168.00")
    assert policy.venue.execution_venue == "binance.com"
    assert policy.venue.market_data_base_url == "https://data-api.binance.vision"
    assert (policy.venue.maker_fee_rate, policy.venue.taker_fee_rate) == (0.00075, 0.00075)
    assert policy.tax.residency == "CO"
    assert policy.tax.cost_basis_method == "FIFO"
    assert policy.data.monthly_budget_usd == 0
    assert policy.strategy.bar_interval == "1d"
    assert policy.strategy.bar_close == "00:00:00Z"
    assert (policy.strategy.min_holding_days, policy.strategy.max_holding_days) == (14, 56)
    assert (policy.strategy.kill_after_closed_trades, policy.strategy.kill_after_months) == (40, 6)
    assert policy.portfolio.discovery_max_portfolio_pct == 0.10
    assert (
        policy.risk.max_trade_usd,
        policy.risk.max_daily_trade_usd,
        policy.risk.max_monthly_trade_usd,
    ) == (75, 150, 750)


def test_version_is_sha256_of_file_bytes() -> None:
    loaded = load_policy(REPO_POLICY)
    assert loaded.version == hashlib.sha256(REPO_POLICY.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    "flag", ["margin_enabled", "futures_enabled", "leverage_enabled", "withdrawals_enabled"]
)
def test_enabling_leverage_or_withdrawals_is_rejected(
    tmp_path: Path, policy_data: dict[str, Any], flag: str
) -> None:
    policy_data["trading"][flag] = True
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, policy_data))


def test_spot_only_false_is_rejected(tmp_path: Path, policy_data: dict[str, Any]) -> None:
    policy_data["trading"]["spot_only"] = False
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, policy_data))


def test_unknown_key_is_rejected(tmp_path: Path, policy_data: dict[str, Any]) -> None:
    policy_data["risk"]["max_leverage"] = 3
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, policy_data))


def test_trade_limits_must_nest(tmp_path: Path, policy_data: dict[str, Any]) -> None:
    policy_data["risk"]["max_daily_trade_usd"] = 50
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, policy_data))


def test_discovery_funnel_must_narrow(tmp_path: Path, policy_data: dict[str, Any]) -> None:
    policy_data["discovery"]["max_llm_candidates_per_scan"] = 50
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, policy_data))


def test_ai_daily_budget_cannot_exceed_monthly(tmp_path: Path, policy_data: dict[str, Any]) -> None:
    policy_data["ai"]["max_calls_per_day"] = 500
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, policy_data))


def test_human_approval_cannot_be_disabled(tmp_path: Path, policy_data: dict[str, Any]) -> None:
    policy_data["execution"]["human_approval_required"] = False
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, policy_data))


def test_fraction_above_one_is_rejected(tmp_path: Path, policy_data: dict[str, Any]) -> None:
    policy_data["risk"]["minimum_cash_reserve_pct"] = 20
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, policy_data))


def test_missing_file_raises_policy_error(tmp_path: Path) -> None:
    with pytest.raises(PolicyError):
        load_policy(tmp_path / "absent.yaml")


def test_malformed_yaml_raises_policy_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("risk: [unclosed")
    with pytest.raises(PolicyError):
        load_policy(path)


@pytest.mark.parametrize("limit", USD_LIMITS)
@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
def test_non_finite_usd_limits_are_rejected(
    tmp_path: Path, policy_data: dict[str, Any], limit: str, value: float
) -> None:
    for name in USD_LIMITS:
        policy_data["risk"][name] = value if name == limit else 100
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, policy_data))


@pytest.mark.parametrize(
    ("limit", "value"),
    [
        ("max_trade_usd", 10_001),
        ("max_daily_trade_usd", 25_001),
        ("max_monthly_trade_usd", 100_001),
    ],
)
def test_usd_limits_above_ceiling_are_rejected(
    tmp_path: Path, policy_data: dict[str, Any], limit: str, value: int
) -> None:
    policy_data["risk"].update(dict.fromkeys(USD_LIMITS, value))
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, policy_data))


def test_usd_limits_at_ceiling_are_accepted(tmp_path: Path, policy_data: dict[str, Any]) -> None:
    policy_data["risk"].update(
        max_trade_usd=10_000, max_daily_trade_usd=25_000, max_monthly_trade_usd=100_000
    )
    assert load_policy(_write(tmp_path, policy_data)).policy.risk.max_monthly_trade_usd == 100_000


def test_integer_and_float_limits_are_both_accepted(
    tmp_path: Path, policy_data: dict[str, Any]
) -> None:
    policy_data["risk"].update(max_trade_usd=75, max_daily_trade_usd=150.5)
    risk = load_policy(_write(tmp_path, policy_data)).policy.risk
    assert (risk.max_trade_usd, risk.max_daily_trade_usd) == (75.0, 150.5)


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("risk", "max_trade_usd", "75"),
        ("risk", "max_trade_portfolio_pct", "0.05"),
        ("discovery", "max_quant_candidates", "20"),
        ("universe", "minimum_trading_history_days", 90.0),
        ("universe", "quote_assets", "USDT"),
        ("universe", "quote_assets", [1]),
        ("execution", "mode", "shadow"),
    ],
)
def test_coercible_but_mistyped_values_are_rejected(
    tmp_path: Path, policy_data: dict[str, Any], section: str, key: str, value: object
) -> None:
    policy_data[section][key] = value
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, policy_data))


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("trading", "withdrawals_enabled", 0),
        ("trading", "spot_only", 1),
        ("execution", "human_approval_required", 1),
        ("universe", "exclude_stablecoins", 1),
        ("universe", "exclude_stablecoins", "off"),
        ("universe", "exclude_stablecoins", "true"),
        ("trading", "spot_only", "true"),
        ("trading", "margin_enabled", "false"),
    ],
)
def test_non_boolean_flags_are_rejected(
    tmp_path: Path, policy_data: dict[str, Any], section: str, key: str, value: object
) -> None:
    policy_data[section][key] = value
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, policy_data))


def test_boolean_schema_version_is_rejected(tmp_path: Path, policy_data: dict[str, Any]) -> None:
    policy_data["schema_version"] = True
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, policy_data))


def test_schema_version_one_is_rejected(tmp_path: Path, policy_data: dict[str, Any]) -> None:
    policy_data["schema_version"] = 1
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, policy_data))


def test_exact_holdings_may_omit_the_due_date(tmp_path: Path, policy_data: dict[str, Any]) -> None:
    policy_data["portfolio"]["holdings_are_approximate"] = False
    del policy_data["portfolio"]["holdings_detail_due"]
    loaded = load_policy(_write(tmp_path, policy_data))
    assert loaded.policy.portfolio.holdings_detail_due is None


def test_core_mix_with_a_renamed_symbol_is_rejected(
    tmp_path: Path, policy_data: dict[str, Any]
) -> None:
    policy_data["portfolio"]["core_mix"] = {"BTCUSDT": 0.70, "SOLUSDT": 0.30}
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, policy_data))


def test_renamed_core_assets_are_rejected(tmp_path: Path, policy_data: dict[str, Any]) -> None:
    policy_data["portfolio"]["core_assets"] = ["BTCUSDT", "SOLUSDT"]
    policy_data["portfolio"]["core_mix"] = {"BTCUSDT": 0.70, "SOLUSDT": 0.30}
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, policy_data))


def test_core_mix_item_assignment_is_rejected() -> None:
    portfolio = load_policy(REPO_POLICY).policy.portfolio
    with pytest.raises(TypeError):
        portfolio.core_mix["BTCUSDT"] = 0.99
    assert portfolio.core_btc_monthly_usd == Decimal("392.00")
    assert portfolio.core_eth_monthly_usd == Decimal("168.00")


def test_exact_holdings_may_keep_the_due_date(tmp_path: Path, policy_data: dict[str, Any]) -> None:
    policy_data["portfolio"]["holdings_are_approximate"] = False
    loaded = load_policy(_write(tmp_path, policy_data))
    assert loaded.policy.portfolio.holdings_detail_due == date(2026, 10, 5)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("portfolio", "starting_value_usd"), 0),
        (("portfolio", "starting_value_usd"), 1_000_001),
        (("portfolio", "monthly_contribution_usd"), 0),
        (("portfolio", "monthly_contribution_usd"), 1_000_001),
        (("portfolio", "holdings_detail_due"), None),
        (("portfolio", "contribution_split", "core"), 0.50),
        (("portfolio", "contribution_split", "bonus"), 0.10),
        (("portfolio", "core_mix", "ETHUSDT"), 0.20),
        (("venue", "execution_venue"), "binance.us"),
        (("venue", "market_data_base_url"), "https://api.binance.com"),
        (("venue", "maker_fee_rate"), 0),
        (("venue", "taker_fee_rate"), 0.02),
        (("tax", "residency"), "US"),
        (("tax", "cost_basis_method"), "LIFO"),
        (("data", "monthly_budget_usd"), 0.0),
        (("data", "monthly_budget_usd"), 1),
        (("strategy", "bar_interval"), "1h"),
        (("strategy", "bar_close"), "00:00:00"),
        (("strategy", "min_holding_days"), 0),
        (("strategy", "max_holding_days"), 3651),
        (("strategy", "min_holding_days"), 60),
        (("strategy", "kill_after_closed_trades"), 0),
        (("strategy", "kill_after_closed_trades"), 100_001),
        (("strategy", "kill_after_months"), 0),
        (("strategy", "kill_after_months"), 121),
    ],
)
def test_schema_v2_boundary_values_are_rejected(
    tmp_path: Path, policy_data: dict[str, Any], path: tuple[str, ...], value: object
) -> None:
    cursor = policy_data
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, policy_data))


def test_duplicate_nested_key_is_rejected(tmp_path: Path) -> None:
    text = REPO_POLICY.read_text().replace(
        "  max_trade_usd: 75\n", "  max_trade_usd: 75\n  max_trade_usd: 150\n"
    )
    path = tmp_path / "policy.yaml"
    path.write_text(text)
    with pytest.raises(PolicyError, match="duplicate key"):
        load_policy(path)


def test_duplicate_top_level_section_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text(REPO_POLICY.read_text() + yaml.safe_dump({"risk": REPO_DATA["risk"]}))
    with pytest.raises(PolicyError, match="duplicate key"):
        load_policy(path)


def test_unhashable_key_raises_policy_error(tmp_path: Path) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text("? [a, b]\n: 1\n")
    with pytest.raises(PolicyError):
        load_policy(path)


_limit_inputs = st.one_of(
    st.sampled_from([math.inf, math.nan, -math.inf, 0, -1, "75", True]),
    st.floats(min_value=0.01, max_value=150_000),
    st.integers(min_value=-10, max_value=150_000),
    st.floats(),
    st.text(max_size=6),
    st.booleans(),
    st.none(),
)


@given(trade=_limit_inputs, daily=_limit_inputs, monthly=_limit_inputs)
def test_accepted_policy_limits_are_finite_positive_and_nested(
    trade: object, daily: object, monthly: object
) -> None:
    data = copy.deepcopy(REPO_DATA)
    data["risk"].update(
        max_trade_usd=trade, max_daily_trade_usd=daily, max_monthly_trade_usd=monthly
    )
    try:
        risk = InvestmentPolicy.model_validate(data).risk
    except ValidationError:
        return
    limits = (risk.max_trade_usd, risk.max_daily_trade_usd, risk.max_monthly_trade_usd)
    assert all(isinstance(x, float) and math.isfinite(x) and x > 0 for x in limits)
    assert risk.max_trade_usd <= risk.max_daily_trade_usd <= risk.max_monthly_trade_usd


def test_hypotheses_are_separate_from_phase1_placeholders(tmp_path: Path) -> None:
    policy = load_policy(REPO_POLICY).policy
    assert policy.universe.minimum_daily_quote_volume_usd == 5_000_000
    normal = policy.hypotheses.universe.normal
    assert normal.median_quote_volume_30d_usd == 10_000_000
    assert normal.minimum_market_cap_usd == 300_000_000
    assert policy.hypotheses.exits.max_holding_days == policy.strategy.max_holding_days == 56
    assert policy.hypotheses.regime.risk_off.new_entries is False
    assert policy.hypotheses.approval.ttl_minutes == 240
    rewritten = yaml.safe_dump(yaml.safe_load(REPO_POLICY.read_text()))
    assert load_policy(_write(tmp_path, yaml.safe_load(rewritten))).policy == policy


def test_depth_band_is_loaded_from_policy() -> None:
    recorders = load_policy(REPO_POLICY).policy.recorders
    assert recorders.depth_band_bps == 200
    assert recorders.depth_band == Decimal("0.02")


def test_a_policy_without_recorders_is_rejected(
    tmp_path: Path, policy_data: dict[str, Any]
) -> None:
    policy_data.pop("recorders", None)
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, policy_data))


def test_a_schema_v2_file_without_hypotheses_is_rejected(
    tmp_path: Path, policy_data: dict[str, Any]
) -> None:
    del policy_data["hypotheses"]
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, policy_data))


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("recorders", "depth_band_bps"), "200"),
        (("recorders", "depth_band_bps"), 0),
        (("recorders", "depth_band_bps"), 10_001),
        (("recorders", "depth_band_bps"), True),
        (("hypotheses", "regime", "hysteresis_days"), "3"),
        (("hypotheses", "regime", "hysteresis_days"), 0),
        (("hypotheses", "exits", "forced_exit_triggers"), ["not-a-trigger"]),
        (("hypotheses", "approval", "ttl_minutes"), 0),
        (("hypotheses", "universe", "normal", "turnover_min"), 0.9),
        (("hypotheses", "circuit_breakers", "halt_new_entries_drawdown"), 0.9),
        (("hypotheses", "exits", "max_holding_days"), 40),
        (("hypotheses", "universe", "high_risk", "maximum_market_cap_usd"), 200_000_000),
        (("hypotheses", "regime", "risk_off", "new_entries"), True),
        (("hypotheses", "universe", "exclusions", "stablecoin_symbols"), ["FDUSD", "FDUSD"]),
        (("hypotheses", "universe", "high_risk", "minimum_market_cap_usd"), 400_000_000),
        (("hypotheses", "universe", "manipulation", "binance_volume_share_below"), 0.95),
        (("hypotheses", "universe", "new_listing", "normal_lane_days"), 90),
        (
            ("hypotheses", "fundamentals", "block_if_missing"),
            ["market_cap", "market_cap", "market_cap"],
        ),
        (("hypotheses", "fundamentals", "unlock_pct_circ_14d"), 0.9),
        (("hypotheses", "regime", "breadth_risk_off"), 0.9),
        (("hypotheses", "regime", "risk_on", "size_mult"), 0.5),
        (("hypotheses", "regime", "neutral", "min_score"), 60),
        (("hypotheses", "exits", "time_stop_days"), 90),
        (
            ("hypotheses", "exits", "forced_exit_triggers"),
            [
                "delisting_announced",
                "delisting_announced",
                "monitoring_tag_added",
                "regime_risk_off",
                "unlock_pct_circ_within_7d_ge_0.02",
            ],
        ),
        (
            ("hypotheses", "approval", "revalidate_on_execute"),
            ["regime", "regime", "gates", "filters", "portfolio"],
        ),
    ],
)
def test_hypothesis_boundaries_fail_closed(
    tmp_path: Path, policy_data: dict[str, Any], path: tuple[str, ...], value: object
) -> None:
    cursor = policy_data
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, policy_data))
