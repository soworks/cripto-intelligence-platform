from typing import Any

from botocore.exceptions import ClientError
from hypothesis import given
from hypothesis import strategies as st

from cip.config.flags import FAIL_CLOSED, ExecutionFlags, read_execution_flags
from cip.domain.policy import ExecutionMode

PREFIX = "/cip/test"


def _put(ssm: Any, mode: str = "SHADOW", trading: str = "false", kill: str = "false") -> None:
    for name, value in (
        ("execution_mode", mode),
        ("trading_enabled", trading),
        ("kill_switch", kill),
    ):
        ssm.put_parameter(Name=f"{PREFIX}/{name}", Value=value, Type="String", Overwrite=True)


def test_reads_valid_flags(ssm: Any) -> None:
    _put(ssm)
    assert read_execution_flags(ssm, PREFIX) == ExecutionFlags(
        mode=ExecutionMode.SHADOW, trading_enabled=False, kill_switch_active=False
    )


def test_reads_true_values_case_insensitively(ssm: Any) -> None:
    _put(ssm, mode="APPROVAL_REQUIRED", trading=" TRUE ", kill="True")
    assert read_execution_flags(ssm, PREFIX) == ExecutionFlags(
        mode=ExecutionMode.APPROVAL_REQUIRED, trading_enabled=True, kill_switch_active=True
    )


def test_missing_parameters_fail_closed(ssm: Any) -> None:
    assert read_execution_flags(ssm, PREFIX) == FAIL_CLOSED


def test_partially_missing_parameters_fail_closed(ssm: Any) -> None:
    ssm.put_parameter(Name=f"{PREFIX}/execution_mode", Value="SHADOW", Type="String")
    assert read_execution_flags(ssm, PREFIX) == FAIL_CLOSED


def test_unknown_mode_fails_closed(ssm: Any) -> None:
    _put(ssm, mode="YOLO")
    assert read_execution_flags(ssm, PREFIX) == FAIL_CLOSED


def test_non_boolean_value_fails_closed(ssm: Any) -> None:
    _put(ssm, trading="yes")
    assert read_execution_flags(ssm, PREFIX) == FAIL_CLOSED


def test_client_error_fails_closed() -> None:
    class DeniedSsm:
        def get_parameters(self, **_: Any) -> Any:
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "GetParameters")

    assert read_execution_flags(DeniedSsm(), PREFIX) == FAIL_CLOSED  # type: ignore[arg-type]


def test_fail_closed_never_allows_live_orders() -> None:
    assert FAIL_CLOSED.may_place_live_orders is False


def test_live_orders_allowed_only_with_all_conditions() -> None:
    flags = ExecutionFlags(
        ExecutionMode.APPROVAL_REQUIRED, trading_enabled=True, kill_switch_active=False
    )
    assert flags.may_place_live_orders is True


@given(mode=st.sampled_from(ExecutionMode), trading=st.booleans(), kill=st.booleans())
def test_shadow_disabled_or_killed_never_allows_live_orders(
    mode: ExecutionMode, trading: bool, kill: bool
) -> None:
    flags = ExecutionFlags(mode, trading_enabled=trading, kill_switch_active=kill)
    if mode is not ExecutionMode.APPROVAL_REQUIRED or not trading or kill:
        assert flags.may_place_live_orders is False
