from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from botocore.exceptions import BotoCoreError, ClientError

from cip.domain.policy import ExecutionMode

if TYPE_CHECKING:
    from mypy_boto3_ssm import SSMClient


@dataclass(frozen=True)
class ExecutionFlags:
    mode: ExecutionMode
    trading_enabled: bool
    kill_switch_active: bool

    @property
    def may_place_live_orders(self) -> bool:
        return (
            self.mode is ExecutionMode.APPROVAL_REQUIRED
            and self.trading_enabled
            and not self.kill_switch_active
        )


FAIL_CLOSED = ExecutionFlags(
    mode=ExecutionMode.SHADOW, trading_enabled=False, kill_switch_active=True
)


def _parse_bool(value: str) -> bool | None:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    return None


def read_execution_flags(ssm: SSMClient, prefix: str) -> ExecutionFlags:
    mode_name = f"{prefix}/execution_mode"
    trading_name = f"{prefix}/trading_enabled"
    kill_name = f"{prefix}/kill_switch"
    try:
        response = ssm.get_parameters(Names=[mode_name, trading_name, kill_name])
    except (BotoCoreError, ClientError):
        return FAIL_CLOSED

    values = {p["Name"]: p["Value"] for p in response.get("Parameters", [])}
    if response.get("InvalidParameters") or len(values) != 3:
        return FAIL_CLOSED
    try:
        mode = ExecutionMode(values[mode_name].strip())
    except ValueError:
        return FAIL_CLOSED
    trading = _parse_bool(values[trading_name])
    kill = _parse_bool(values[kill_name])
    if trading is None or kill is None:
        return FAIL_CLOSED
    return ExecutionFlags(mode=mode, trading_enabled=trading, kill_switch_active=kill)
