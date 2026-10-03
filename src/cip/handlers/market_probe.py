from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from aws_lambda_powertools import Logger, Metrics
from aws_lambda_powertools.metrics import MetricUnit
from aws_lambda_powertools.utilities.typing import LambdaContext

from cip.adapters.binance import BinanceMarketClient
from cip.adapters.market import MarketData
from cip.domain.errors import ExchangeGeoBlockedError
from cip.domain.policy import load_policy

logger = Logger(service="cip-market-probe")
metrics = Metrics(namespace="CIP/MarketData", service="cip-market-probe")

PROBE_SYMBOL = "BTCUSDT"
GEO_BLOCKED = "GeoBlocked"


def run_market_probe(market: MarketData) -> dict[str, int]:
    """One daily candle for BTCUSDT. A 451 is reported; a 418 still aborts the call."""
    try:
        candles = market.klines(PROBE_SYMBOL, interval="1d", limit=1)
    except ExchangeGeoBlockedError:
        logger.error("market data host returned 451")
        return {"candle_count": 0, "geo_blocked": 1}
    return {"candle_count": len(candles), "geo_blocked": 0}


def _client() -> BinanceMarketClient:
    policy = load_policy(Path(os.environ["POLICY_PATH"]))
    return BinanceMarketClient(policy.policy.venue.market_data_base_url)


@logger.inject_lambda_context
@metrics.log_metrics(capture_cold_start_metric=False)
def probe(_event: dict[str, Any], _context: LambdaContext) -> dict[str, int]:
    with _client() as client:
        result = run_market_probe(client)
    metrics.add_metric(name=GEO_BLOCKED, unit=MetricUnit.Count, value=result["geo_blocked"])
    return result
