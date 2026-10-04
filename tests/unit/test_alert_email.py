import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
ASSIGNMENT = re.compile(r"""alert_email\s*=\s*["'][^"']+@[^"']+["']""")
SKIP = {".git", ".venv", "build", ".terraform"}


def test_no_alert_email_address_is_committed() -> None:
    offenders: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in SKIP for part in path.parts):
            continue
        if path.suffix not in {".tf", ".tfvars", ".md", ".yml", ".yaml", ".sh", ".json"}:
            continue
        if ASSIGNMENT.search(path.read_text(errors="ignore")):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_alert_email_variables_are_sensitive() -> None:
    for relative in (
        "terraform/bootstrap/variables.tf",
        "terraform/environments/dev/variables.tf",
    ):
        text = (ROOT / relative).read_text()
        block = text.split('variable "alert_email"', 1)[1].split("\nvariable ", 1)[0]
        assert re.search(r"sensitive\s*=\s*true", block)
