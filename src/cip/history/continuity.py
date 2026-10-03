from __future__ import annotations

import re
from collections.abc import Hashable
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

from cip.domain.errors import HistoryError


def _exact_int(value: object) -> object:
    if type(value) is not int:
        raise ValueError("must be an int")
    return value


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class ContinuityBreak(_Strict):
    symbol: str
    first_discontinuous_date: date
    reason: str


class Rename(_Strict):
    predecessor: str
    successor: str
    predecessor_last_date: date
    successor_first_date: date
    reason: str


class Scale(_Strict):
    pattern: str
    price_scale: int
    reason: str


class Continuity(_Strict):
    schema_version: Annotated[Literal[1], BeforeValidator(_exact_int)]
    breaks: Annotated[tuple[ContinuityBreak, ...], Field(strict=False)]
    renames: Annotated[tuple[Rename, ...], Field(strict=False)]
    scales: Annotated[tuple[Scale, ...], Field(strict=False)]


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys instead of keeping the last one."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Hashable, Any]:
        mapping = super().construct_mapping(node, deep=deep)
        seen: set[Hashable] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            seen.add(key)
        return mapping


def load_continuity(path: Path) -> Continuity:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise HistoryError(f"cannot read continuity file {path}") from error
    try:
        document = yaml.load(raw, Loader=_UniqueKeyLoader)  # noqa: S506 - SafeLoader subclass
        continuity = Continuity.model_validate(document)
        _validate_unique(continuity)
        for scale in continuity.scales:
            re.compile(scale.pattern)
    except (yaml.YAMLError, ValidationError, ValueError, re.error) as error:
        raise HistoryError(f"invalid continuity file {path}: {error}") from error
    return continuity


def _validate_unique(continuity: Continuity) -> None:
    break_symbols = [item.symbol for item in continuity.breaks]
    predecessors = [item.predecessor for item in continuity.renames]
    successors = [item.successor for item in continuity.renames]
    if len(break_symbols) != len(set(break_symbols)):
        raise ValueError("duplicate break symbol")
    if len(predecessors) != len(set(predecessors)):
        raise ValueError("duplicate rename predecessor")
    if len(successors) != len(set(successors)):
        raise ValueError("duplicate rename successor")
