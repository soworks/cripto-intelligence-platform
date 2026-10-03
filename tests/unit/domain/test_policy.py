import hashlib
from pathlib import Path
from typing import Any

import pytest
import yaml

from cip.domain.errors import PolicyError
from cip.domain.policy import ExecutionMode, load_policy

REPO_POLICY = Path(__file__).parents[3] / "policies" / "investment-policy.yaml"


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
