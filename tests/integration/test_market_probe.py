import json

import boto3
import pytest

from tests.integration.conftest import require_env

pytestmark = pytest.mark.integration


def test_market_probe_reaches_binance_vision_from_lambda() -> None:
    client = boto3.client("lambda")
    response = client.invoke(
        FunctionName=require_env("CIP_MARKET_PROBE_FUNCTION"),
        InvocationType="RequestResponse",
        Payload=b"{}",
    )

    assert response["StatusCode"] == 200
    assert response.get("FunctionError") is None
    body = json.loads(response["Payload"].read())
    assert body == {"candle_count": 1, "geo_blocked": 0}
