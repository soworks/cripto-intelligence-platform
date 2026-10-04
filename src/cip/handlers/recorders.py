from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
import httpx
from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext

from cip.adapters.binance import BinanceMarketClient
from cip.domain.errors import RecorderError
from cip.domain.policy import load_policy
from cip.recorders.collect import collect_live, persist_to
from cip.recorders.observation import failure_id, family_of, observation_id
from cip.recorders.store import S3Store, object_key

logger = Logger(service="cip-recorders")


def record(_event: dict[str, Any], _context: LambdaContext) -> dict[str, Any]:
    """One collection cycle. Failures are stored. A 418 still aborts the invocation."""
    policy = load_policy(Path(os.environ["POLICY_PATH"]))
    symbols = tuple(
        part.strip() for part in os.environ["RECORDER_SYMBOLS"].split(",") if part.strip()
    )
    if not symbols:
        raise RecorderError("at least one symbol is required")
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
    persist_to(S3Store(boto3.client("s3"), os.environ["DATA_BUCKET"]), result)
    observation_keys = [
        object_key(
            "observations",
            item.family,
            item.series,
            item.identity_time,
            observation_id(item),
        )
        for item in result.observations
    ]
    failure_keys = [
        object_key(
            "observation-failures",
            family_of(item.series),
            item.series,
            item.observed_at,
            failure_id(item),
        )
        for item in result.failures
    ]
    logger.info("recorder cycle stored")
    return {
        "observations": len(observation_keys),
        "failures": len(failure_keys),
        "observation_keys": observation_keys,
        "failure_keys": failure_keys,
    }
