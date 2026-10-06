from pathlib import Path

import cip

ROOT = Path(__file__).parents[3]
WORKLOAD = ROOT / "terraform" / "modules" / "workload"


def test_shadow_code_does_not_name_an_execution_attempt() -> None:
    text = "\n".join(path.read_text() for path in Path(cip.__file__).parent.rglob("*.py"))
    assert "ExecutionAttempts" not in text


def test_the_execution_attempt_alarm_treats_missing_data_as_not_an_attempt() -> None:
    text = (WORKLOAD / "alerts.tf").read_text()
    start = text.index("alarm_execution_attempt")
    block = text[start : text.index("aws_cloudwatch_dashboard")]
    assert 'namespace           = "CIP/Execution"' in block
    assert 'metric_name         = "ExecutionAttempts"' in block
    assert 'comparison_operator = "GreaterThanThreshold"' in block
    assert "threshold           = 0" in block
    assert 'treat_missing_data  = "notBreaching"' in block


def test_the_workload_keeps_trading_disabled_and_has_no_order_credentials() -> None:
    joined = "\n".join(path.read_text() for path in WORKLOAD.glob("*.tf"))
    flags = (WORKLOAD / "flags.tf").read_text()
    assert 'value = "SHADOW"' in flags
    assert 'name  = "${local.flags_prefix}/trading_enabled"' in flags
    assert 'value = "false"' in flags
    assert "secretsmanager" not in joined.lower()
    assert "api.binance.com" not in joined
    assert "BINANCE_API" not in joined
