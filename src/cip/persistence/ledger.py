from __future__ import annotations

from typing import TYPE_CHECKING, Any

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from cip.domain.errors import DuplicateEventError
from cip.domain.events import LedgerEvent

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import Table


class LedgerRepository:
    def __init__(self, table: Table) -> None:
        self._table = table

    def append(self, event: LedgerEvent) -> None:
        try:
            self._table.put_item(
                Item=event.to_item(), ConditionExpression="attribute_not_exists(PK)"
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise DuplicateEventError(event.event_id) from error
            raise

    def list_by_correlation(self, correlation_id: str) -> list[LedgerEvent]:
        condition = Key("GSI1PK").eq(f"CORR#{correlation_id}")
        events: list[LedgerEvent] = []
        start_key: dict[str, Any] | None = None
        while True:
            if start_key is None:
                response = self._table.query(IndexName="GSI1", KeyConditionExpression=condition)
            else:
                response = self._table.query(
                    IndexName="GSI1",
                    KeyConditionExpression=condition,
                    ExclusiveStartKey=start_key,
                )
            events.extend(LedgerEvent.from_item(item) for item in response["Items"])
            start_key = response.get("LastEvaluatedKey")
            if start_key is None:
                return events
