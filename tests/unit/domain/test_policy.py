import copy
import hashlib
import math
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
