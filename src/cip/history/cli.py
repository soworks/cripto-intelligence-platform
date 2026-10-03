from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from cip.domain.errors import ExchangeGeoBlockedError, HistoryError
from cip.history.client import DumpClient
from cip.history.continuity import load_continuity
from cip.history.store import load_bars
from cip.history.sync import sync_history
from cip.history.universe import build_listings, write_listings

_DEFAULT_CONTINUITY = Path("data/symbol-continuity.yaml")


def _today() -> date:
    return datetime.now(UTC).date()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m cip.history")
    commands = parser.add_subparsers(dest="command", required=True)
    sync = commands.add_parser("sync", help="download daily klines and write the universe table")
    sync.add_argument("--output", type=Path, required=True)
    sync.add_argument("--bucket")
    sync.add_argument("--symbol")
    sync.add_argument("--continuity", type=Path, default=_DEFAULT_CONTINUITY)
    universe = commands.add_parser("universe", help="rebuild the universe table from local klines")
    universe.add_argument("--klines", type=Path, required=True)
    universe.add_argument("--output", type=Path, required=True)
    universe.add_argument("--continuity", type=Path, default=_DEFAULT_CONTINUITY)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "sync":
            with DumpClient() as client:
                sync_history(
                    output=args.output,
                    client=client,
                    today=_today(),
                    continuity_path=args.continuity,
                    symbol=args.symbol,
                    bucket=args.bucket,
                )
        else:
            continuity = load_continuity(args.continuity)
            listings = build_listings(load_bars(args.klines), continuity)
            write_listings(args.output, listings)
    except (HistoryError, ExchangeGeoBlockedError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0
