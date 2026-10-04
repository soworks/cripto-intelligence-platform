from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cip.backtest.engine import run_benchmarks
from cip.domain.errors import BacktestError, PolicyError

_DEFAULT_POLICY = Path("policies/investment-policy.yaml")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = run_benchmarks(args.klines, args.policy)
    except (BacktestError, PolicyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m cip.backtest")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="replay the BTC and BTC/ETH contribution books")
    run.add_argument("--klines", type=Path, required=True)
    run.add_argument("--policy", type=Path, default=_DEFAULT_POLICY)
    return parser
