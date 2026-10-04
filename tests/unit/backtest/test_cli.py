import json
import runpy
import sys
from pathlib import Path

import pytest

from cip.backtest.cli import main

_POLICY = Path("policies/investment-policy.yaml")


def test_missing_bars_exit_with_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["run", "--klines", str(tmp_path), "--policy", str(_POLICY)])

    assert code == 1
    assert capsys.readouterr().err.startswith("error:")


def test_a_report_is_printed_as_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "cip.backtest.cli.run_benchmarks",
        lambda _klines, _policy: {"policy_sha256": "abc"},
    )

    code = main(["run", "--klines", str(tmp_path), "--policy", str(_POLICY)])

    assert code == 0
    assert json.loads(capsys.readouterr().out) == {"policy_sha256": "abc"}


def test_module_entry_point_exits_with_the_command_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["cip.backtest", "run", "--klines", str(tmp_path), "--policy", str(_POLICY)],
    )

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("cip.backtest", run_name="__main__")

    assert exit_info.value.code == 1
