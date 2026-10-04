from datetime import date
from decimal import Decimal
from subprocess import CompletedProcess

import pytest

from cip.backtest.contracts import Eligibility, ExitRules, FeatureCalculator, Scorer, Sizer
from cip.backtest.metadata import (
    OWNER_INVENTORY,
    RunMetadata,
    code_version,
    holdings_source,
)
from cip.domain.errors import BacktestError


class _Rules:
    def features(self, symbol: str, as_of: date) -> dict[str, Decimal]:
        return {"symbol": Decimal(len(symbol))}

    def eligible(self, symbol: str, as_of: date) -> bool:
        return symbol == "BTCUSDT"

    def score(self, symbol: str, as_of: date) -> Decimal:
        return Decimal(as_of.year)

    def size_usd(self, symbol: str, as_of: date) -> Decimal:
        return Decimal(25)

    def exit_due(self, symbol: str, as_of: date) -> bool:
        return False


def test_one_object_can_satisfy_the_replay_contracts() -> None:
    rules = _Rules()
    day = date(2026, 10, 4)
    assert isinstance(rules, FeatureCalculator)
    assert isinstance(rules, Eligibility)
    assert isinstance(rules, Scorer)
    assert isinstance(rules, Sizer)
    assert isinstance(rules, ExitRules)
    assert rules.eligible("BTCUSDT", day) is True
    assert rules.exit_due("BTCUSDT", day) is False


def _metadata(**overrides: object) -> RunMetadata:
    fields: dict[str, object] = {
        "policy_version": "abc",
        "dataset_start": "2020-01-01",
        "dataset_end": "2020-01-03",
        "code_version": "unknown",
        "benchmark": "btc_dca_and_btc_eth_dca",
        "random_seed": None,
        "taker_fee_rate": "0.00075",
        "spread": "not_applied",
        "slippage": "not_applied",
        "holdings_source": "FIXTURE",
    }
    fields.update(overrides)
    return RunMetadata(**fields)  # type: ignore[arg-type]


def test_a_random_baseline_without_a_seed_is_rejected() -> None:
    with pytest.raises(BacktestError, match="seed"):
        _metadata(benchmark="random_selection")


def test_an_unknown_holdings_source_is_rejected() -> None:
    with pytest.raises(BacktestError, match="holdings source"):
        _metadata(holdings_source="PRODUCTION")


def test_exact_holdings_are_named_owner_inventory() -> None:
    assert holdings_source(True) == "FIXTURE"
    assert holdings_source(False) == OWNER_INVENTORY


def test_code_version_keeps_a_full_sha_and_rejects_anything_else() -> None:
    sha = "a" * 40

    def ok(*_args: object, **_kwargs: object) -> CompletedProcess[str]:
        return CompletedProcess(args=[], returncode=0, stdout=f"{sha}\n")

    def missing(*_args: object, **_kwargs: object) -> CompletedProcess[str]:
        raise OSError("git is not installed")

    def failed(*_args: object, **_kwargs: object) -> CompletedProcess[str]:
        return CompletedProcess(args=[], returncode=1, stdout="")

    def short(*_args: object, **_kwargs: object) -> CompletedProcess[str]:
        return CompletedProcess(args=[], returncode=0, stdout="abc\n")

    assert code_version(ok) == sha
    assert code_version(missing) == "unknown"
    assert code_version(failed) == "unknown"
    assert code_version(short) == "unknown"
    assert _metadata().as_dict()["benchmark"] == "btc_dca_and_btc_eth_dca"
