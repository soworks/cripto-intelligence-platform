from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from moto import mock_aws


@pytest.fixture(autouse=True)
def _fake_aws_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.delenv("AWS_PROFILE", raising=False)


@pytest.fixture
def ssm() -> Iterator[Any]:
    with mock_aws():
        yield boto3.client("ssm", region_name="us-east-1")


@pytest.fixture
def ledger_table() -> Iterator[Any]:
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        yield dynamodb.create_table(
            TableName="cip-test-ledger",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": name, "AttributeType": "S"}
                for name in ("PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK")
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": index,
                    "KeySchema": [
                        {"AttributeName": f"{index}PK", "KeyType": "HASH"},
                        {"AttributeName": f"{index}SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
                for index in ("GSI1", "GSI2")
            ],
        )
