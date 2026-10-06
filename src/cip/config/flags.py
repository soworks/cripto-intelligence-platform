from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from botocore.exceptions import BotoCoreError, ClientError

from cip.domain.policy import ExecutionMode

if TYPE_CHECKING:
    from mypy_boto3_ssm import SSMClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExecutionFlags:
    mode: ExecutionMode
    trading_enabled: bool
    kill_switch_active: bool
    # False when the parameters could not be read. That is not a confirmed SHADOW cycle.
    confirmed: bool = True

    @property
    def may_place_live_orders(self) -> bool:
        return (
            self.mode is ExecutionMode.APPROVAL_REQUIRED
            and self.trading_enabled
            and not self.kill_switch_active
        )


FAIL_CLOSED = ExecutionFlags(
    mode=ExecutionMode.SHADOW,
    trading_enabled=False,
    kill_switch_active=True,
    confirmed=False,
)


def _parse_bool(value: str) -> bool | None:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    return None


def _fail_closed(reason: str) -> ExecutionFlags:
    logger.warning("execution flags unavailable, failing closed: %s", reason)
    return FAIL_CLOSED


def read_execution_flags(ssm: SSMClient, prefix: str) -> ExecutionFlags:
    mode_name = f"{prefix}/execution_mode"
    trading_name = f"{prefix}/trading_enabled"
    kill_name = f"{prefix}/kill_switch"
    names = [mode_name, trading_name, kill_name]
    try:
        response = ssm.get_parameters(Names=names)
    except ClientError as error:
        return _fail_closed(f"SSM error {error.response.get('Error', {}).get('Code')}")
    except BotoCoreError as error:
        return _fail_closed(f"SSM error {type(error).__name__}")

    values = {p["Name"]: p["Value"] for p in response.get("Parameters", [])}
    missing = [name for name in names if name not in values]
    if missing:
        return _fail_closed(f"missing parameters {missing}")
    try:
        mode = ExecutionMode(values[mode_name].strip())
    except ValueError:
        return _fail_closed(f"invalid execution_mode {values[mode_name]!r}")
    trading = _parse_bool(values[trading_name])
    kill = _parse_bool(values[kill_name])
    if trading is None or kill is None:
        invalid = [
            name for name, flag in ((trading_name, trading), (kill_name, kill)) if flag is None
        ]
        return _fail_closed(f"non-boolean values for {invalid}")
    return ExecutionFlags(mode=mode, trading_enabled=trading, kill_switch_active=kill)
