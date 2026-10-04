from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

from cip.adapters.binance import BinanceMarketClient
from cip.domain.errors import MarketDataError, PolicyError, RecorderError
from cip.domain.policy import load_policy
from cip.recorders.collect import collect_live, persist

_DEFAULT_POLICY = Path("policies/investment-policy.yaml")
_DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    symbols = tuple(part.strip() for part in args.symbols.split(",") if part.strip())
    if not symbols:
        print("error: at least one symbol is required", file=sys.stderr)
        return 2
    try:
        policy = load_policy(args.policy)
        with (
            httpx.Client(timeout=10.0, follow_redirects=False) as public,
            httpx.Client(timeout=10.0, follow_redirects=False) as futures,
            BinanceMarketClient(policy.policy.venue.market_data_base_url) as spot,
        ):
            result = collect_live(
                observed_at=datetime.now(UTC),
                symbols=symbols,
                spot=spot,
                public=public,
                futures=futures,
                depth_band=policy.policy.recorders.depth_band,
            )
        persist(args.output, result)
    except (RecorderError, PolicyError, MarketDataError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(
        f"observations={len(result.observations)} failures={len(result.failures)} "
        f"output={args.output}"
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m cip.recorders")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="append point-in-time market observations")
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--policy", type=Path, default=_DEFAULT_POLICY)
    run.add_argument("--symbols", default=",".join(_DEFAULT_SYMBOLS))
    return parser
