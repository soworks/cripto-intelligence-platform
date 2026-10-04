from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from subprocess import CompletedProcess

from cip.domain.errors import BacktestError

FIXTURE = "FIXTURE"
OWNER_INVENTORY = "OWNER_INVENTORY"
_SHA_LENGTH = 40


def holdings_source(holdings_are_approximate: bool) -> str:
    if holdings_are_approximate:
        return FIXTURE
    return OWNER_INVENTORY


def code_version(
    runner: Callable[..., CompletedProcess[str]] = subprocess.run,
) -> str:
    try:
        completed = runner(
            ["git", "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    sha = completed.stdout.strip()
    if completed.returncode != 0 or len(sha) != _SHA_LENGTH:
        return "unknown"
    return sha


@dataclass(frozen=True)
class RunMetadata:
    policy_version: str
    dataset_start: str
    dataset_end: str
    code_version: str
    benchmark: str
    random_seed: int | None
    taker_fee_rate: str
    spread: str
    slippage: str
    holdings_source: str

    def __post_init__(self) -> None:
        if self.benchmark == "random_selection" and self.random_seed is None:
            raise BacktestError("a random baseline requires a seed")
        if self.holdings_source not in {FIXTURE, OWNER_INVENTORY}:
            raise BacktestError("holdings source must be FIXTURE or OWNER_INVENTORY")

    def as_dict(self) -> dict[str, str | int | None]:
        return {
            "policy_version": self.policy_version,
            "dataset_start": self.dataset_start,
            "dataset_end": self.dataset_end,
            "code_version": self.code_version,
            "benchmark": self.benchmark,
            "random_seed": self.random_seed,
            "taker_fee_rate": self.taker_fee_rate,
            "spread": self.spread,
            "slippage": self.slippage,
            "holdings_source": self.holdings_source,
        }
