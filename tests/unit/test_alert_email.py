import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]
ASSIGNMENT = re.compile(r"""alert_email\s*=\s*["'][^"']+@[^"']+["']""")
TRACKED_SUFFIXES = {".tf", ".tfvars", ".md", ".yml", ".yaml", ".sh", ".json"}


def _tracked_files() -> list[Path]:
    raw = subprocess.check_output(
        ["git", "ls-files", "-z"],  # noqa: S607
        cwd=ROOT,
    )
    return [ROOT / name.decode() for name in raw.split(b"\0") if name]


def test_no_alert_email_address_is_committed() -> None:
    offenders = [
        str(path.relative_to(ROOT))
        for path in _tracked_files()
        if path.suffix in TRACKED_SUFFIXES and ASSIGNMENT.search(path.read_text(errors="ignore"))
    ]
    assert offenders == []
    gitignore = (ROOT / ".gitignore").read_text()
    assert "*.auto.tfvars\n" in gitignore
    assert "*.auto.tfvars.json\n" in gitignore


def test_alert_email_variables_are_sensitive() -> None:
    for relative in (
        "terraform/bootstrap/variables.tf",
        "terraform/environments/dev/variables.tf",
    ):
        text = (ROOT / relative).read_text()
        block = text.split('variable "alert_email"', 1)[1].split("\nvariable ", 1)[0]
        assert re.search(r"sensitive\s*=\s*true", block)
        assert 'can(regex("^[^[:space:]@]+@[^[:space:]@]+[.][^[:space:]@]+$"' in block
        message = block.split("error_message", 1)[1]
        assert "var.alert_email" not in message


def test_workflows_reject_an_empty_alert_email() -> None:
    for relative in (
        ".github/workflows/pull-request.yml",
        ".github/workflows/deploy-dev.yml",
    ):
        text = (ROOT / relative).read_text()
        assert "TF_VAR_alert_email: ${{ secrets.ALERT_EMAIL }}" in text
        assert '[ -z "${TF_VAR_alert_email}" ]' in text
        assert "error: ALERT_EMAIL is empty" in text
