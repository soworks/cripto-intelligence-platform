"""Mutable position row plus the ledger event for the same move."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from botocore.exceptions import ClientError

from cip.domain.errors import DuplicateEventError, PositionError
from cip.portfolio.position import Position, checked, document_text, transition_event

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import Table

_NEW_ITEM = "attribute_not_exists(PK)"


class PositionStore:
    def __init__(self, state_table: Table, ledger_table: Table) -> None:
        self._state = state_table
        self._ledger = ledger_table

    def commit(self, before: Position | None, after: Position) -> None:
        """Write the state row and its ledger event, or write neither."""
        event = transition_event(before, after)
        after = checked(after)
        before = None if before is None else checked(before)
        ledger_item = event.to_item()
        guard = {
            "PK": f"IDEMP#{event.event_id}",
            "SK": "IDEMP",
            "event_pk": ledger_item["PK"],
            "event_sk": ledger_item["SK"],
        }
        state_item = _state_item(after)
        state_put: dict[str, Any] = {"TableName": self._state.name, "Item": state_item}
        if before is None:
            state_put["ConditionExpression"] = _NEW_ITEM
        else:
            state_put["ConditionExpression"] = "#state = :from_state AND #document = :before"
            state_put["ExpressionAttributeNames"] = {"#state": "state", "#document": "document"}
            state_put["ExpressionAttributeValues"] = {
                ":from_state": before.state.value,
                ":before": document_text(before),
            }
        writes: Any = [
            {
                "Put": {
                    "TableName": self._ledger.name,
                    "Item": guard,
                    "ConditionExpression": _NEW_ITEM,
                }
            },
            {
                "Put": {
                    "TableName": self._ledger.name,
                    "Item": ledger_item,
                    "ConditionExpression": _NEW_ITEM,
                }
            },
            {"Put": state_put},
        ]
        try:
            self._state.meta.client.transact_write_items(TransactItems=writes)
        except ClientError as error:
            reasons = error.response.get("CancellationReasons") or []
            if reasons and reasons[0].get("Code") == "ConditionalCheckFailed":
                raise DuplicateEventError(event.event_id) from error
            if len(reasons) > 2 and reasons[2].get("Code") == "ConditionalCheckFailed":
                message = (
                    "position already exists"
                    if before is None
                    else "position is no longer in the expected state"
                )
                raise PositionError(message) from error
            raise

    def read(self, position_id: str) -> Position:
        response = self._state.get_item(Key={"PK": f"POSITION#{position_id}", "SK": "STATE"})
        item = response.get("Item")
        if item is None:
            raise PositionError("position is missing")
        try:
            raw = item["document"]
            if not isinstance(raw, str):
                raise ValueError("position document is not text")
            position = Position.from_document(json.loads(raw))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise PositionError("position is unusable") from error
        if item.get("state") != position.state.value or position.position_id != position_id:
            raise PositionError("position is unusable")
        return position


def _state_item(position: Position) -> dict[str, str]:
    return {
        "PK": f"POSITION#{position.position_id}",
        "SK": "STATE",
        "state": position.state.value,
        "document": document_text(position),
    }
