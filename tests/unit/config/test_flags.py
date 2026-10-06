import logging
from typing import Any

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
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
    flags = read_execution_flags(ssm, PREFIX)
    assert flags == ExecutionFlags(
        mode=ExecutionMode.SHADOW, trading_enabled=False, kill_switch_active=False
    )
    assert flags.confirmed is True


def test_reads_true_values_case_insensitively(ssm: Any) -> None:
    _put(ssm, mode="APPROVAL_REQUIRED", trading=" TRUE ", kill="True")
    assert read_execution_flags(ssm, PREFIX) == ExecutionFlags(
        mode=ExecutionMode.APPROVAL_REQUIRED, trading_enabled=True, kill_switch_active=True
    )


def _fallback_reasons(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "cip.config.flags" and record.levelno == logging.WARNING
    ]


def test_valid_flags_log_no_warning(ssm: Any, caplog: pytest.LogCaptureFixture) -> None:
    _put(ssm)
    read_execution_flags(ssm, PREFIX)
    assert _fallback_reasons(caplog) == []


def test_missing_parameters_fail_closed(ssm: Any, caplog: pytest.LogCaptureFixture) -> None:
    assert read_execution_flags(ssm, PREFIX) == FAIL_CLOSED
    [reason] = _fallback_reasons(caplog)
    assert "missing parameters" in reason
    assert f"{PREFIX}/kill_switch" in reason


def test_partially_missing_parameters_fail_closed(
    ssm: Any, caplog: pytest.LogCaptureFixture
) -> None:
    ssm.put_parameter(Name=f"{PREFIX}/execution_mode", Value="SHADOW", Type="String")
    assert read_execution_flags(ssm, PREFIX) == FAIL_CLOSED
    [reason] = _fallback_reasons(caplog)
    assert f"{PREFIX}/trading_enabled" in reason
    assert f"{PREFIX}/execution_mode" not in reason


def test_unknown_mode_fails_closed(ssm: Any, caplog: pytest.LogCaptureFixture) -> None:
    _put(ssm, mode="YOLO")
    assert read_execution_flags(ssm, PREFIX) == FAIL_CLOSED
    [reason] = _fallback_reasons(caplog)
    assert "execution_mode" in reason
    assert "YOLO" in reason


def test_non_boolean_value_fails_closed(ssm: Any, caplog: pytest.LogCaptureFixture) -> None:
    _put(ssm, trading="yes")
    assert read_execution_flags(ssm, PREFIX) == FAIL_CLOSED
    [reason] = _fallback_reasons(caplog)
    assert "trading_enabled" in reason


def test_client_error_fails_closed(caplog: pytest.LogCaptureFixture) -> None:
    class DeniedSsm:
        def get_parameters(self, **_: Any) -> Any:
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "GetParameters")

    assert read_execution_flags(DeniedSsm(), PREFIX) == FAIL_CLOSED  # type: ignore[arg-type]
    [reason] = _fallback_reasons(caplog)
    assert "AccessDenied" in reason


def test_botocore_error_fails_closed(caplog: pytest.LogCaptureFixture) -> None:
    class UnreachableSsm:
        def get_parameters(self, **_: Any) -> Any:
            raise EndpointConnectionError(endpoint_url="https://ssm.us-east-1.amazonaws.com")

    assert read_execution_flags(UnreachableSsm(), PREFIX) == FAIL_CLOSED  # type: ignore[arg-type]
    [reason] = _fallback_reasons(caplog)
    assert "EndpointConnectionError" in reason


def test_fail_closed_never_allows_live_orders() -> None:
    assert FAIL_CLOSED.may_place_live_orders is False
    assert FAIL_CLOSED.confirmed is False


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
