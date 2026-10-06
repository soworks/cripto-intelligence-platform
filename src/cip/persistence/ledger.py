from __future__ import annotations

from typing import TYPE_CHECKING, Any

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from cip.domain.errors import DuplicateEventError, InvalidEventError
from cip.domain.events import LedgerEvent

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import Table

_NEW_ITEM = "attribute_not_exists(PK)"


class LedgerRepository:
    def __init__(self, table: Table) -> None:
        self._table = table

    def append(self, event: LedgerEvent) -> None:
        """Write the event plus an ``IDEMP#<event_id>`` guard item in one transaction.

        The event SK is time-ordered, so only the guard makes a retried write collide.
        """
        item = event.to_item()
        guard = {
            "PK": f"IDEMP#{event.event_id}",
            "SK": "IDEMP",
            "event_pk": item["PK"],
            "event_sk": item["SK"],
        }
        table = self._table.name
        try:
            # The table resource's client accepts plain Python values, not AttributeValue maps.
            self._table.meta.client.transact_write_items(
                TransactItems=[
                    {"Put": {"TableName": table, "Item": guard, "ConditionExpression": _NEW_ITEM}},
                    {"Put": {"TableName": table, "Item": item, "ConditionExpression": _NEW_ITEM}},
                ]
            )
        except ClientError as error:
            # Cancellation reasons are positional; index 0 is the guard.
            reasons = error.response.get("CancellationReasons") or [{}]
            if reasons[0].get("Code") == "ConditionalCheckFailed":
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

    def existing(self, event_id: str) -> LedgerEvent:
        """Read the event a duplicate write collided with. The read is consistent."""
        guard = self._table.get_item(
            Key={"PK": f"IDEMP#{event_id}", "SK": "IDEMP"},
            ConsistentRead=True,
        )
        pointer = guard.get("Item")
        if pointer is None:
            raise InvalidEventError("ledger event is missing")
        stored = self._table.get_item(
            Key={"PK": pointer["event_pk"], "SK": pointer["event_sk"]},
            ConsistentRead=True,
        )
        item = stored.get("Item")
        if item is None:
            raise InvalidEventError("ledger event is missing")
        return LedgerEvent.from_item(item)
