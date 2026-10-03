# Phase 1 - Foundation (M0 + M1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a production-shaped but strategy-free platform skeleton: a Python repository with quality gates, a versioned fail-closed investment policy, an append-only ledger, Terraform-managed AWS dev infrastructure in `us-east-1`, GitHub Actions with OIDC, and an hourly Step Functions pipeline that records `SCAN_STARTED` / `SCAN_COMPLETED` events. Binance reachability from AWS is verified before anything else.

**Architecture:** Pure-Python domain code in `src/cip/` with thin Lambda handlers. Terraform is split into a once-off `bootstrap` stack (applied locally) and per-environment stacks (applied by CI). The scan pipeline is a Step Functions Standard state machine invoking small Lambdas. State is split between an append-only DynamoDB ledger, a mutable state table, and an atomic counters table. Runtime safety flags live in SSM and are read fail-closed.

**Tech Stack:** Python 3.13, uv, Pydantic 2, AWS Lambda Powertools 3, boto3, moto, pytest, hypothesis, ruff, mypy; Terraform 1.16.5, AWS provider ~> 6.67; GitHub Actions with AWS OIDC.

**Related documents:**
- Reference architecture: `docs/architecture/reference-architecture-v1.0.md`
- Gap analysis: `docs/reviews/2026-10-03-gap-analysis.md`
- Roadmap (M0-M7): `docs/plans/2026-10-03-roadmap.md`

## Global Constraints

- AWS account `258485600712`, region `us-east-1`, local CLI profile `soworks`.
- Single AWS account; environments separated by the `cip-<env>-` resource prefix, the `/cip/<env>/` SSM path, the `env` tag, separate Terraform state keys, and per-env IAM permission boundaries.
- Python `>=3.13,<3.14`; Lambda runtime `python3.13`, architecture `arm64`.
- Terraform `1.16.5`; provider `hashicorp/aws ~> 6.67`; S3 backend with `use_lockfile = true` (no DynamoDB lock table).
- GitHub repository `soworks/cripto-intelligence-platform`; CI authenticates with OIDC only. No AWS access keys are stored in GitHub.
- `EXECUTION_MODE=SHADOW` and `TRADING_ENABLED=false` everywhere in Phase 1. No Binance API keys or trading secrets are created.
- No Bedrock calls and no trading or order code in Phase 1.
- Fail closed: any missing or invalid policy or flag means no trading.
- Python package name: `cip` (src layout `src/cip/...`). The reference doc's `src/adapters` etc. become `src/cip/adapters` etc. (ADR-0007).
- Percentages in policy are fractions (`0.05` = 5%). Spreads are in basis points (`maximum_spread_bps: 50` = 0.50%).
- Policy numeric values in Phase 1 are the reference doc's placeholders. M2 replaces them with policy schema v2 per gap analysis Part B; Phase 1 only proves loading, validation, and versioning.

## File Structure

```text
cripto-intelligence-platform/
├── .github/workflows/
│   ├── pull-request.yml            # lint/type/test/audit + terraform checks + dev plan
│   └── deploy-dev.yml              # build, apply dev, integration tests
├── .checkov.yaml                   # justified skips for checkov
├── .pre-commit-config.yaml
├── .python-version
├── Makefile
├── pyproject.toml
├── uv.lock
├── README.md
├── docs/
│   ├── adr/0001..0007-*.md
│   ├── architecture/reference-architecture-v1.0.md
│   ├── plans/
│   ├── reviews/
│   └── runbooks/bootstrap.md
├── policies/investment-policy.yaml
├── iam/github/                     # CI role policy templates (plan, deploy)
├── scripts/build_lambda.sh         # deterministic arm64 Lambda ZIP
├── scripts/bootstrap_github_oidc.sh  # idempotent OIDC provider + cip-gha-* roles (AWS CLI)
├── scripts/verify_github_oidc.sh   # IAM simulation spot checks for the CI roles
├── src/cip/
│   ├── __init__.py
│   ├── config/{__init__,flags}.py          # SSM execution flags, fail-closed
│   ├── domain/{__init__,errors,events,policy}.py
│   ├── handlers/{__init__,pipeline}.py     # Lambda entry points
│   └── persistence/{__init__,ledger}.py    # append-only ledger repository
├── terraform/
│   ├── bootstrap/                  # state bucket, boundaries, budgets, CloudTrail (OIDC roles: scripts/bootstrap_github_oidc.sh)
│   ├── modules/                    # each module has its own versions.tf
│   │   ├── alerts/
│   │   ├── platform-data/          # S3 data bucket, ledger/state/counters tables
│   │   ├── runtime-flags/          # SSM execution flags
│   │   └── scan-pipeline/          # Lambdas, Step Functions, scheduler, alarms
│   └── environments/dev/
└── tests/
    ├── unit/
    │   ├── conftest.py             # fake AWS creds + moto tables
    │   ├── config/test_flags.py
    │   ├── domain/test_policy.py
    │   ├── domain/test_events.py
    │   ├── handlers/test_pipeline.py
    │   ├── persistence/test_ledger.py
    │   └── test_package.py
    └── integration/
        ├── conftest.py
        ├── test_iam_boundaries.py
        └── test_scan_pipeline.py
```

Work in `/Users/albertosolano/Documents/Projects/cripto-intelligence-platform/cripto-intelligence-platform` (the GitHub clone). All paths below are relative to it.

---

### Task 1: Toolchain and Python project skeleton

**Files:**
- Create: `pyproject.toml`, `.python-version`, `Makefile`, `.pre-commit-config.yaml`, `src/cip/__init__.py`, `tests/unit/test_package.py`
- Modify: `.gitignore`, `README.md`

**Interfaces:**
- Produces: importable package `cip` with `cip.__version__: str`; `make check` runs lint + type + tests.

- [ ] **Step 1: Install local tools**

```bash
brew install uv pre-commit tflint
tfenv install 1.16.5 && tfenv use 1.16.5
terraform version   # Expected: Terraform v1.16.5
uv --version        # Expected: uv 0.12.x
```

- [ ] **Step 2: Create `.python-version`**

```text
3.13
```

- [ ] **Step 3: Create `pyproject.toml`**

```toml
[project]
name = "cip"
version = "0.1.0"
description = "Crypto Intelligence Platform"
readme = "README.md"
requires-python = ">=3.13,<3.14"
dependencies = [
  "aws-lambda-powertools>=3.35",
  "boto3>=1.43",
  "httpx>=0.28",
  "pydantic>=2.13",
  "pyyaml>=6.0",
]

[dependency-groups]
dev = [
  "boto3-stubs[dynamodb,iam,ssm,stepfunctions]>=1.43",
  "hypothesis>=6.0",
  "moto[dynamodb,iam,ssm,stepfunctions]>=5.2",
  "mypy>=1.15",
  "pip-audit>=2.9",
  "pytest>=8.0",
  "pytest-cov>=6.0",
  "ruff>=0.12",
  "types-PyYAML>=6.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/cip"]

[tool.ruff]
line-length = 100
target-version = "py313"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "S", "SIM", "RUF", "DTZ", "PT"]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101"]

[tool.mypy]
python_version = "3.13"
strict = true
mypy_path = "src"
packages = ["cip"]
plugins = ["pydantic.mypy"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --strict-markers -m 'not integration'"
markers = ["integration: requires deployed AWS dev resources"]

[tool.coverage.run]
branch = true
source = ["cip"]

[tool.coverage.report]
fail_under = 85
show_missing = true
```

- [ ] **Step 4: Write the failing test** `tests/unit/test_package.py`

```python
import cip


def test_package_exposes_version() -> None:
    assert cip.__version__ == "0.1.0"
```

- [ ] **Step 5: Sync and run the test to verify it fails**

Run: `uv sync --all-groups && uv run pytest tests/unit/test_package.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cip'` (or an import error).

- [ ] **Step 6: Create `src/cip/__init__.py`**

```python
__version__ = "0.1.0"
```

- [ ] **Step 7: Run the test to verify it passes**

Run: `uv sync --all-groups && uv run pytest tests/unit/test_package.py -v`
Expected: PASS.

- [ ] **Step 8: Create `Makefile`**

```make
.PHONY: install fmt lint type test audit check build tf-fmt

install:
	uv sync --all-groups
	uv run pre-commit install

fmt:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff format --check .
	uv run ruff check .

type:
	uv run mypy

test:
	uv run pytest --cov --cov-report=term-missing

audit:
	uv run pip-audit --skip-editable

check: lint type test

build:
	./scripts/build_lambda.sh

tf-fmt:
	terraform fmt -recursive terraform
```

- [ ] **Step 9: Create `.pre-commit-config.yaml`, then pin hook versions**

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.12.0
    hooks:
      - id: ruff-check
        args: [--fix]
      - id: ruff-format
  - repo: local
    hooks:
      - id: terraform-fmt
        name: terraform fmt
        entry: terraform fmt -recursive terraform
        language: system
        pass_filenames: false
        files: \.tf$
```

Run: `uv run pre-commit autoupdate && make install`
Expected: `pre-commit installed at .git/hooks/pre-commit`.

- [ ] **Step 10: Append to `.gitignore`**

```gitignore
# Build and infrastructure artifacts
build/
*.zip
.terraform/
*.tfstate
*.tfstate.*
*.tfplan
tfplan
plan.txt
.terraform.tfstate.lock.info
```

- [ ] **Step 11: Replace `README.md`**

```markdown
# Crypto Intelligence Platform (cip)

AWS-native research and decision platform that scans the Binance Spot universe,
filters deterministically, invokes an LLM only for high-value analysis, and keeps
execution behind deterministic policy validation and human approval.

**Operating mode: SHADOW.** No real orders are placed.

- Architecture: `docs/architecture/reference-architecture-v1.0.md`
- Gap analysis: `docs/reviews/2026-10-03-gap-analysis.md`
- Roadmap: `docs/plans/2026-10-03-roadmap.md`
- ADRs: `docs/adr/`

## Develop

    make install   # uv sync + pre-commit
    make check     # ruff, mypy, pytest
    make build     # Lambda artifact in build/cip-lambda.zip
```

- [ ] **Step 12: Run all checks and commit**

Run: `make check`
Expected: ruff clean, mypy `Success: no issues found`, pytest 1 passed.

```bash
git add .
git commit -m "chore: bootstrap python project with uv, ruff, mypy, pytest"
```

---

### Task 2: Binance and Bedrock reachability spike

Confirms whether Lambda in `us-east-1` can reach Binance.com (expected: HTTP 451) and whether a Binance-permitted region (`sa-east-1`) can, and confirms Bedrock model access. The result decides ADR-0003. All spike resources are destroyed at the end.

**Files:**
- Create (temporary, deleted in Step 7): `terraform/spikes/binance-reachability/main.tf`, `terraform/spikes/binance-reachability/probe.py`

**Interfaces:**
- Produces: a recorded decision in ADR-0003 (Task 3): `EXCHANGE_GATEWAY_REGION` = `none` (direct from `us-east-1`) or `sa-east-1`.

- [ ] **Step 1: Create `terraform/spikes/binance-reachability/probe.py`**

```python
import urllib.error
import urllib.request

ENDPOINTS = {
    "api.binance.com": "https://api.binance.com/api/v3/exchangeInfo?symbol=BTCUSDT",
    "data-api.binance.vision": "https://data-api.binance.vision/api/v3/exchangeInfo?symbol=BTCUSDT",
    "api.binance.com/klines": "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1h&limit=1",
}


def handler(event, context):
    results = {}
    for name, url in ENDPOINTS.items():
        try:
            with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310
                results[name] = response.status
        except urllib.error.HTTPError as error:
            results[name] = error.code
        except urllib.error.URLError as error:
            results[name] = f"error: {error.reason}"
    return results
```

- [ ] **Step 2: Create `terraform/spikes/binance-reachability/main.tf`**

```hcl
terraform {
  required_version = "~> 1.16"
  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 6.67" }
    archive = { source = "hashicorp/archive", version = "~> 2.7" }
  }
}

provider "aws" {
  alias   = "use1"
  region  = "us-east-1"
  profile = "soworks"
}

provider "aws" {
  alias   = "sae1"
  region  = "sa-east-1"
  profile = "soworks"
}

data "archive_file" "probe" {
  type        = "zip"
  source_file = "${path.module}/probe.py"
  output_path = "${path.module}/build/probe.zip"
}

data "aws_iam_policy_document" "assume" {
  provider = aws.use1
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "probe" {
  provider           = aws.use1
  name               = "cip-spike-binance-probe"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy_attachment" "logs" {
  provider   = aws.use1
  role       = aws_iam_role.probe.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_lambda_function" "probe_use1" {
  provider         = aws.use1
  function_name    = "cip-spike-binance-probe"
  role             = aws_iam_role.probe.arn
  handler          = "probe.handler"
  runtime          = "python3.13"
  architectures    = ["arm64"]
  filename         = data.archive_file.probe.output_path
  source_code_hash = data.archive_file.probe.output_base64sha256
  timeout          = 40
  depends_on       = [aws_iam_role_policy_attachment.logs]
}

resource "aws_lambda_function" "probe_sae1" {
  provider         = aws.sae1
  function_name    = "cip-spike-binance-probe"
  role             = aws_iam_role.probe.arn
  handler          = "probe.handler"
  runtime          = "python3.13"
  architectures    = ["arm64"]
  filename         = data.archive_file.probe.output_path
  source_code_hash = data.archive_file.probe.output_base64sha256
  timeout          = 40
  depends_on       = [aws_iam_role_policy_attachment.logs]
}
```

- [ ] **Step 3: Apply** (if the first apply fails with a role-propagation error, run `apply` again)

```bash
cd terraform/spikes/binance-reachability
terraform init && terraform apply -auto-approve
```

Expected: `Apply complete! Resources: 4 added`.

- [ ] **Step 4: Invoke both probes and record the output**

```bash
for region in us-east-1 sa-east-1; do
  echo "== $region"
  aws lambda invoke --profile soworks --region "$region" \
    --function-name cip-spike-binance-probe /dev/stdout
done
```

Expected (hypothesis): `us-east-1` shows `451` for `api.binance.com`; `sa-east-1` shows `200` for all endpoints. Save the exact output for ADR-0003.

- [ ] **Step 5: Confirm Bedrock access with the intended default model**

```bash
aws bedrock-runtime converse --profile soworks --region us-east-1 \
  --model-id us.anthropic.claude-haiku-4-5-20251001-v1:0 \
  --messages '[{"role":"user","content":[{"text":"Reply with the single word OK."}]}]' \
  --query 'output.message.content[0].text' --output text
```

Expected: `OK`. If you get `AccessDeniedException`, enable model access for Claude Haiku 4.5 in the Bedrock console (us-east-1) and retry.

- [ ] **Step 6: Destroy the spike**

```bash
terraform destroy -auto-approve && rm -rf .terraform build terraform.tfstate* .terraform.lock.hcl
```

Expected: `Destroy complete! Resources: 4 destroyed`.

- [ ] **Step 7: Remove the spike directory**

The result is preserved in ADR-0003 (Task 3). The spike is not committed, so it never enters CI's tflint/checkov scope.

```bash
cd ../../..
rm -rf terraform/spikes
git status --short   # Expected: no terraform/spikes entries
```

---

### Task 3: Architecture decision records

**Files:**
- Create: `docs/adr/0001-deterministic-risk-authority.md`, `docs/adr/0002-dynamic-universe-narrow-execution.md`, `docs/adr/0003-region-and-exchange-connectivity.md`, `docs/adr/0004-single-account-isolation.md`, `docs/adr/0005-storage-split.md`, `docs/adr/0006-step-functions-orchestration.md`, `docs/adr/0007-package-layout.md`

**Interfaces:**
- Consumes: Task 2 probe output.
- Produces: decisions referenced by later tasks and milestones.

- [ ] **Step 1: Write ADR-0001**

```markdown
# ADR-0001: Deterministic risk authority; LLM recommends only

Status: Accepted (2026-10-03)

## Context
LLM outputs are probabilistic and can be wrong, manipulated by injected text, or
schema-valid but unsafe.

## Decision
Code computes features, filters, scores, sizes, and validates. The LLM returns a
schema-constrained recommendation that the deterministic policy validator may
reject. No LLM has tools that can place orders. The validator never reads LLM
free text as an instruction.

## Consequences
Every proposal is reproducible from stored inputs, policy version, and prompt
version. LLM value is measured against a deterministic-only baseline (ablation).
```

- [ ] **Step 2: Write ADR-0002**

```markdown
# ADR-0002: Dynamic research universe; narrow execution policy

Status: Accepted (2026-10-03)

## Decision
Research discovers the Binance Spot universe dynamically and narrows it through
deterministic filters. Execution eligibility is narrower and governed by policy:
core assets, discovery caps, and a separate high-risk lifecycle for new listings.
A listing event alone never creates a BUY proposal.

## Consequences
Universe snapshots are stored per scan, including delisted symbols, so backtests
avoid survivorship bias.
```

- [ ] **Step 3: Write ADR-0003 using the Task 2 result**

Use the first "Decision" block if `us-east-1` returned `200` for `api.binance.com`; otherwise use the second.

```markdown
# ADR-0003: Region and exchange connectivity

Status: Accepted (2026-10-03)

## Context
The platform runs in us-east-1 (owner decision). Binance.com geo-restricts US IP
ranges. Spike result (2026-10-03, Lambda arm64):

    <paste the probe output for us-east-1 and sa-east-1 here>

## Decision (if us-east-1 is reachable)
All components, including Binance adapters, run in us-east-1.
EXCHANGE_GATEWAY_REGION = none.

## Decision (if us-east-1 is blocked)
The core platform (Step Functions, DynamoDB, S3, Bedrock, approvals, observability)
stays in us-east-1. Binance HTTP calls run in a single `exchange-gateway` Lambda in
sa-east-1, invoked synchronously cross-region by core Lambdas with
`lambda:InvokeFunction` on that one ARN. The gateway exposes
`get_exchange_info`, `get_ticker_24h`, `get_klines` (M2), and, from M6, signed
order endpoints behind a separate function and role.
EXCHANGE_GATEWAY_REGION = sa-east-1.

## Consequences
`BinanceMarketAdapter` is location-agnostic: one implementation calls Binance over
HTTP (used inside the gateway and in local tests), and one calls the gateway. Static
egress for M6 IP-whitelisting is solved in the gateway region only.
Binance.US is not used (US persons only; owner is in Colombia).
```

- [ ] **Step 4: Write ADR-0004**

```markdown
# ADR-0004: Single AWS account with environment isolation

Status: Accepted (2026-10-03)

## Decision
dev and prod share account 258485600712. Isolation controls:
- Resource prefix `cip-<env>-`, SSM path `/cip/<env>/`, secret path `cip/<env>/`, tag `env`.
- Separate Terraform state keys `env/<env>/terraform.tfstate`.
- CI roles `cip-gha-plan` (PRs, read-only), `cip-gha-dev` (GitHub environment `dev`),
  `cip-gha-prod` (GitHub environment `prod` with required reviewer).
- Each deploy role may create workload roles only with its env's permission
  boundary (`cip-<env>-workload-boundary`). The dev boundary denies Secrets Manager
  entirely and denies all prod resources. Both boundaries deny ledger mutation.

## Consequences
Lower setup cost than AWS Organizations. Blast radius is limited by IAM, not by an
account boundary. Revisit before M6 if the prod trading secret's risk profile
requires a dedicated account.
```

- [ ] **Step 5: Write ADR-0005**

```markdown
# ADR-0005: Storage split - S3 snapshots, DynamoDB ledger/state/counters

Status: Accepted (2026-10-03)

## Decision
- S3 `cip-<env>-data-<account>`: raw exchange responses and per-scan universe/feature
  snapshots under `snapshots/dt=YYYY-MM-DD/scan=<correlation_id>/`, plus historical
  datasets. Versioned.
- DynamoDB `cip-<env>-ledger`: append-only decision events. PK `ASSET#<symbol>`,
  SK `EVENT#<ts>#<id>`; GSI1 by correlation ID; GSI2 by event type and day.
  UpdateItem/DeleteItem/BatchWriteItem/PartiQL mutations denied by permission boundary.
- DynamoDB `cip-<env>-state`: mutable materialized state (positions, shadow
  portfolio, approvals, watchlist, listing lifecycle, dossier dedupe).
- DynamoDB `cip-<env>-counters`: atomic budget counters (LLM calls, trade USD)
  with conditional ceilings - the hard guardrail.

## Consequences
Ledger events reference snapshot S3 keys with SHA-256 hashes, keeping items small.
```

- [ ] **Step 6: Write ADR-0006**

```markdown
# ADR-0006: Step Functions Standard orchestrates the scan pipeline

Status: Accepted (2026-10-03)

## Decision
EventBridge Scheduler starts a Step Functions Standard state machine. Each stage is
a small Lambda that calls pure domain code. Failures are caught and recorded as a
`PIPELINE_FAILED` ledger event, and CloudWatch alarms fire on failures or a missed
schedule. Callers may supply `correlation_id`; otherwise the state machine
generates one with `States.UUID()`.

## Consequences
Per-run visual audit trail and retries without custom code. Hourly cadence costs
cents per month.
```

- [ ] **Step 7: Write ADR-0007**

```markdown
# ADR-0007: Package layout `src/cip`

Status: Accepted (2026-10-03)

## Decision
The reference doc's `src/adapters`, `src/domain`, ... become `src/cip/adapters`,
`src/cip/domain`, ... so the code is an importable, typed package
(`import cip.domain.policy`). Lambda handlers are referenced as
`cip.handlers.<module>.<function>`.
```

- [ ] **Step 8: Commit**

```bash
git add docs/adr
git commit -m "docs: add ADRs 0001-0007"
```

---

### Task 4: Investment policy model and fail-closed loader

**Files:**
- Create: `policies/investment-policy.yaml`, `src/cip/domain/__init__.py`, `src/cip/domain/errors.py`, `src/cip/domain/policy.py`, `tests/__init__.py`, `tests/unit/__init__.py`, `tests/unit/domain/__init__.py`, `tests/unit/domain/test_policy.py`

**Interfaces:**
- Produces:
  - `cip.domain.errors.CipError`, `PolicyError(CipError)`, `DuplicateEventError(CipError)`
  - `cip.domain.policy.ExecutionMode(StrEnum)`: `SHADOW`, `APPROVAL_REQUIRED`, `LIVE_DISABLED`
  - `cip.domain.policy.InvestmentPolicy` (frozen Pydantic model; sections `universe`, `discovery`, `portfolio`, `trading`, `risk`, `ai`, `execution`)
  - `cip.domain.policy.LoadedPolicy` dataclass: `policy: InvestmentPolicy`, `version: str` (SHA-256 hex of the file bytes)
  - `cip.domain.policy.load_policy(path: Path) -> LoadedPolicy` (raises `PolicyError`)

- [ ] **Step 1: Create `policies/investment-policy.yaml`**

```yaml
schema_version: 1

universe:
  quote_assets: [USDT]
  exclude_stablecoins: true
  exclude_leveraged_tokens: true
  minimum_trading_history_days: 90
  minimum_daily_quote_volume_usd: 5000000
  minimum_market_cap_usd: 50000000
  maximum_spread_bps: 50

discovery:
  max_quant_candidates: 20
  max_enriched_candidates: 10
  max_llm_candidates_per_scan: 5
  new_listing_observation_days: 7

portfolio:
  core_assets: [BTCUSDT, ETHUSDT]
  discovery_max_portfolio_pct: 0.10

trading:
  spot_only: true
  margin_enabled: false
  futures_enabled: false
  leverage_enabled: false
  withdrawals_enabled: false

risk:
  max_trade_usd: 75
  max_trade_portfolio_pct: 0.05
  minimum_cash_reserve_pct: 0.20
  max_discovery_asset_pct: 0.01
  max_daily_trade_usd: 150
  max_monthly_trade_usd: 750

ai:
  max_calls_per_day: 10
  max_calls_per_month: 150

execution:
  mode: SHADOW
  human_approval_required: true
```

- [ ] **Step 2: Write the failing tests** `tests/unit/domain/test_policy.py` (also create empty `tests/__init__.py`, `tests/unit/__init__.py` and `tests/unit/domain/__init__.py`; the package markers let integration tests import `tests.integration.conftest`)

```python
import hashlib
from pathlib import Path
from typing import Any

import pytest
import yaml

from cip.domain.errors import PolicyError
from cip.domain.policy import ExecutionMode, load_policy

REPO_POLICY = Path(__file__).parents[3] / "policies" / "investment-policy.yaml"


@pytest.fixture
def policy_data() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(REPO_POLICY.read_text())
    return data


def _write(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_repository_policy_loads_in_shadow_mode() -> None:
    loaded = load_policy(REPO_POLICY)
    assert loaded.policy.execution.mode is ExecutionMode.SHADOW
    assert loaded.policy.trading.withdrawals_enabled is False


def test_version_is_sha256_of_file_bytes() -> None:
    loaded = load_policy(REPO_POLICY)
    assert loaded.version == hashlib.sha256(REPO_POLICY.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    "flag", ["margin_enabled", "futures_enabled", "leverage_enabled", "withdrawals_enabled"]
)
def test_enabling_leverage_or_withdrawals_is_rejected(
    tmp_path: Path, policy_data: dict[str, Any], flag: str
) -> None:
    policy_data["trading"][flag] = True
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, policy_data))


def test_spot_only_false_is_rejected(tmp_path: Path, policy_data: dict[str, Any]) -> None:
    policy_data["trading"]["spot_only"] = False
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, policy_data))


def test_unknown_key_is_rejected(tmp_path: Path, policy_data: dict[str, Any]) -> None:
    policy_data["risk"]["max_leverage"] = 3
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, policy_data))


def test_trade_limits_must_nest(tmp_path: Path, policy_data: dict[str, Any]) -> None:
    policy_data["risk"]["max_daily_trade_usd"] = 50
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, policy_data))


def test_discovery_funnel_must_narrow(tmp_path: Path, policy_data: dict[str, Any]) -> None:
    policy_data["discovery"]["max_llm_candidates_per_scan"] = 50
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, policy_data))


def test_ai_daily_budget_cannot_exceed_monthly(
    tmp_path: Path, policy_data: dict[str, Any]
) -> None:
    policy_data["ai"]["max_calls_per_day"] = 500
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, policy_data))


def test_human_approval_cannot_be_disabled(tmp_path: Path, policy_data: dict[str, Any]) -> None:
    policy_data["execution"]["human_approval_required"] = False
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, policy_data))


def test_fraction_above_one_is_rejected(tmp_path: Path, policy_data: dict[str, Any]) -> None:
    policy_data["risk"]["minimum_cash_reserve_pct"] = 20
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, policy_data))


def test_missing_file_raises_policy_error(tmp_path: Path) -> None:
    with pytest.raises(PolicyError):
        load_policy(tmp_path / "absent.yaml")


def test_malformed_yaml_raises_policy_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("risk: [unclosed")
    with pytest.raises(PolicyError):
        load_policy(path)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/domain/test_policy.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cip.domain'`.

- [ ] **Step 4: Create `src/cip/domain/__init__.py` (empty) and `src/cip/domain/errors.py`**

```python
class CipError(Exception):
    """Base class for platform errors."""


class PolicyError(CipError):
    """Investment policy is missing or invalid; callers must fail closed."""


class DuplicateEventError(CipError):
    """A ledger event with the same key already exists."""
```

- [ ] **Step 5: Create `src/cip/domain/policy.py`**

```python
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from cip.domain.errors import PolicyError

Fraction = Annotated[float, Field(gt=0, le=1)]
PositiveUsd = Annotated[float, Field(gt=0)]


class ExecutionMode(StrEnum):
    SHADOW = "SHADOW"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    LIVE_DISABLED = "LIVE_DISABLED"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class UniversePolicy(_Strict):
    quote_assets: tuple[str, ...] = Field(min_length=1)
    exclude_stablecoins: bool
    exclude_leveraged_tokens: bool
    minimum_trading_history_days: int = Field(ge=0)
    minimum_daily_quote_volume_usd: PositiveUsd
    minimum_market_cap_usd: PositiveUsd
    maximum_spread_bps: float = Field(gt=0, le=1000)


class DiscoveryPolicy(_Strict):
    max_quant_candidates: int = Field(ge=1)
    max_enriched_candidates: int = Field(ge=1)
    max_llm_candidates_per_scan: int = Field(ge=0)
    new_listing_observation_days: int = Field(ge=0)

    @model_validator(mode="after")
    def _funnel_narrows(self) -> Self:
        if not (
            self.max_quant_candidates
            >= self.max_enriched_candidates
            >= self.max_llm_candidates_per_scan
        ):
            raise ValueError("discovery funnel must narrow: quant >= enriched >= llm")
        return self


class PortfolioPolicy(_Strict):
    core_assets: tuple[str, ...]
    discovery_max_portfolio_pct: Fraction


class TradingPolicy(_Strict):
    spot_only: Literal[True]
    margin_enabled: Literal[False]
    futures_enabled: Literal[False]
    leverage_enabled: Literal[False]
    withdrawals_enabled: Literal[False]


class RiskPolicy(_Strict):
    max_trade_usd: PositiveUsd
    max_trade_portfolio_pct: Fraction
    minimum_cash_reserve_pct: Fraction
    max_discovery_asset_pct: Fraction
    max_daily_trade_usd: PositiveUsd
    max_monthly_trade_usd: PositiveUsd

    @model_validator(mode="after")
    def _limits_nest(self) -> Self:
        if not (self.max_trade_usd <= self.max_daily_trade_usd <= self.max_monthly_trade_usd):
            raise ValueError("trade limits must nest: per-trade <= daily <= monthly")
        return self


class AiPolicy(_Strict):
    max_calls_per_day: int = Field(ge=0)
    max_calls_per_month: int = Field(ge=0)

    @model_validator(mode="after")
    def _daily_within_monthly(self) -> Self:
        if self.max_calls_per_day > self.max_calls_per_month:
            raise ValueError("max_calls_per_day cannot exceed max_calls_per_month")
        return self


class ExecutionPolicy(_Strict):
    mode: ExecutionMode
    human_approval_required: Literal[True]


class InvestmentPolicy(_Strict):
    schema_version: Literal[1]
    universe: UniversePolicy
    discovery: DiscoveryPolicy
    portfolio: PortfolioPolicy
    trading: TradingPolicy
    risk: RiskPolicy
    ai: AiPolicy
    execution: ExecutionPolicy


@dataclass(frozen=True)
class LoadedPolicy:
    policy: InvestmentPolicy
    version: str


def load_policy(path: Path) -> LoadedPolicy:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise PolicyError(f"cannot read policy file {path}") from error
    try:
        policy = InvestmentPolicy.model_validate(yaml.safe_load(raw))
    except (yaml.YAMLError, ValidationError) as error:
        raise PolicyError(f"invalid policy file {path}: {error}") from error
    return LoadedPolicy(policy=policy, version=hashlib.sha256(raw).hexdigest())
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/domain/test_policy.py -v`
Expected: 15 passed.

- [ ] **Step 7: Run all checks and commit**

Run: `make check`
Expected: all green; coverage of `cip.domain.policy` 100%.

```bash
git add policies src/cip/domain tests/unit
git commit -m "feat: add versioned fail-closed investment policy loader"
```

---

### Task 5: Runtime execution flags (fail closed)

**Files:**
- Create: `src/cip/config/__init__.py`, `src/cip/config/flags.py`, `tests/unit/conftest.py`, `tests/unit/config/__init__.py`, `tests/unit/config/test_flags.py`

**Interfaces:**
- Consumes: `cip.domain.policy.ExecutionMode`
- Produces:
  - `cip.config.flags.ExecutionFlags(mode: ExecutionMode, trading_enabled: bool, kill_switch_active: bool)` with property `may_place_live_orders -> bool`
  - `cip.config.flags.FAIL_CLOSED: ExecutionFlags` = `(SHADOW, False, True)`
  - `cip.config.flags.read_execution_flags(ssm: SSMClient, prefix: str) -> ExecutionFlags`. Reads `<prefix>/execution_mode`, `<prefix>/trading_enabled`, `<prefix>/kill_switch`.

- [ ] **Step 1: Create `tests/unit/conftest.py`** (fake credentials so unit tests can never reach real AWS)

```python
from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from moto import mock_aws


@pytest.fixture(autouse=True)
def _fake_aws_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.delenv("AWS_PROFILE", raising=False)


@pytest.fixture
def ssm() -> Iterator[Any]:
    with mock_aws():
        yield boto3.client("ssm", region_name="us-east-1")


@pytest.fixture
def ledger_table() -> Iterator[Any]:
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        yield dynamodb.create_table(
            TableName="cip-test-ledger",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": name, "AttributeType": "S"}
                for name in ("PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK")
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": index,
                    "KeySchema": [
                        {"AttributeName": f"{index}PK", "KeyType": "HASH"},
                        {"AttributeName": f"{index}SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
                for index in ("GSI1", "GSI2")
            ],
        )
```

- [ ] **Step 2: Write the failing tests** `tests/unit/config/test_flags.py` (also create empty `tests/unit/config/__init__.py`)

```python
from typing import Any

from botocore.exceptions import ClientError
from hypothesis import given
from hypothesis import strategies as st

from cip.config.flags import FAIL_CLOSED, ExecutionFlags, read_execution_flags
from cip.domain.policy import ExecutionMode

PREFIX = "/cip/test"


def _put(ssm: Any, mode: str = "SHADOW", trading: str = "false", kill: str = "false") -> None:
    for name, value in (("execution_mode", mode), ("trading_enabled", trading), ("kill_switch", kill)):
        ssm.put_parameter(Name=f"{PREFIX}/{name}", Value=value, Type="String", Overwrite=True)


def test_reads_valid_flags(ssm: Any) -> None:
    _put(ssm)
    assert read_execution_flags(ssm, PREFIX) == ExecutionFlags(
        mode=ExecutionMode.SHADOW, trading_enabled=False, kill_switch_active=False
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
    flags = ExecutionFlags(ExecutionMode.APPROVAL_REQUIRED, trading_enabled=True, kill_switch_active=False)
    assert flags.may_place_live_orders is True


@given(mode=st.sampled_from(ExecutionMode), trading=st.booleans(), kill=st.booleans())
def test_shadow_disabled_or_killed_never_allows_live_orders(
    mode: ExecutionMode, trading: bool, kill: bool
) -> None:
    flags = ExecutionFlags(mode, trading_enabled=trading, kill_switch_active=kill)
    if mode is not ExecutionMode.APPROVAL_REQUIRED or not trading or kill:
        assert flags.may_place_live_orders is False
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/config -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cip.config'`.

- [ ] **Step 4: Create `src/cip/config/__init__.py` (empty) and `src/cip/config/flags.py`**

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/config -v`
Expected: 9 passed.

- [ ] **Step 6: Run all checks and commit**

Run: `make check`

```bash
git add src/cip/config tests/unit/conftest.py tests/unit/config
git commit -m "feat: read execution flags from SSM, failing closed"
```

---

### Task 6: Ledger event model and append-only repository

**Files:**
- Create: `src/cip/domain/events.py`, `src/cip/persistence/__init__.py`, `src/cip/persistence/ledger.py`, `tests/unit/domain/test_events.py`, `tests/unit/persistence/__init__.py`, `tests/unit/persistence/test_ledger.py`

**Interfaces:**
- Consumes: `cip.domain.errors.DuplicateEventError`; `ledger_table` fixture (Task 5).
- Produces:
  - `cip.domain.events.EventType(StrEnum)`: `SCAN_STARTED`, `SCAN_COMPLETED`, `PIPELINE_FAILED`
  - `cip.domain.events.GLOBAL_ASSET = "GLOBAL"`
  - `cip.domain.events.LedgerEvent` (frozen): `event_type`, `correlation_id`, `policy_version`, `asset="GLOBAL"`, `created_at` (UTC), `event_id` (uuid4 hex), `payload: dict[str, Any]`; methods `to_item() -> dict[str, Any]`, `LedgerEvent.from_item(item) -> LedgerEvent`
  - `cip.persistence.ledger.LedgerRepository(table)` with `append(event: LedgerEvent) -> None` (raises `DuplicateEventError`) and `list_by_correlation(correlation_id: str) -> list[LedgerEvent]` (time-ordered)
  - Item keys: `PK=ASSET#<asset>`, `SK=EVENT#<ts>#<event_id>`, `GSI1PK=CORR#<correlation_id>`, `GSI1SK=<ts>#<event_id>`, `GSI2PK=TYPE#<event_type>#<yyyy-mm-dd>`, `GSI2SK=<ts>#<event_id>`; `<ts>` = `%Y-%m-%dT%H:%M:%S.%fZ`

- [ ] **Step 1: Write the failing event tests** `tests/unit/domain/test_events.py`

```python
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from cip.domain.events import EventType, LedgerEvent

FIXED = datetime(2026, 10, 3, 16, 0, 0, 123456, tzinfo=UTC)


def _event(**overrides: object) -> LedgerEvent:
    fields: dict[str, object] = {
        "event_type": EventType.SCAN_STARTED,
        "correlation_id": "corr-1",
        "policy_version": "abc123",
        "created_at": FIXED,
        "event_id": "e1",
    }
    fields.update(overrides)
    return LedgerEvent.model_validate(fields)


def test_item_keys_follow_ledger_schema() -> None:
    item = _event(asset="BTCUSDT").to_item()
    assert item["PK"] == "ASSET#BTCUSDT"
    assert item["SK"] == "EVENT#2026-10-03T16:00:00.123456Z#e1"
    assert item["GSI1PK"] == "CORR#corr-1"
    assert item["GSI1SK"] == "2026-10-03T16:00:00.123456Z#e1"
    assert item["GSI2PK"] == "TYPE#SCAN_STARTED#2026-10-03"


def test_asset_defaults_to_global() -> None:
    assert _event().to_item()["PK"] == "ASSET#GLOBAL"


def test_round_trip_preserves_event_including_floats() -> None:
    event = _event(payload={"score": 0.25, "count": 3, "nested": {"ok": True, "xs": [1.5]}})
    assert LedgerEvent.from_item(event.to_item()) == event


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _event(created_at=datetime(2026, 10, 3, 16, 0, 0))  # noqa: DTZ001


def test_non_utc_offset_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _event(created_at=datetime(2026, 10, 3, 11, 0, tzinfo=timezone(timedelta(hours=-5))))


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _event(unexpected="x")
```

- [ ] **Step 2: Write the failing repository tests** `tests/unit/persistence/test_ledger.py` (also create empty `tests/unit/persistence/__init__.py`)

```python
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from cip.domain.errors import DuplicateEventError
from cip.domain.events import EventType, LedgerEvent
from cip.persistence.ledger import LedgerRepository

T0 = datetime(2026, 10, 3, 16, 0, tzinfo=UTC)


def _event(event_type: EventType, created_at: datetime, correlation_id: str = "corr-1") -> LedgerEvent:
    return LedgerEvent(
        event_type=event_type,
        correlation_id=correlation_id,
        policy_version="v1",
        created_at=created_at,
    )


def test_append_then_list_by_correlation(ledger_table: Any) -> None:
    repo = LedgerRepository(ledger_table)
    event = _event(EventType.SCAN_STARTED, T0)
    repo.append(event)
    assert repo.list_by_correlation("corr-1") == [event]


def test_duplicate_append_raises(ledger_table: Any) -> None:
    repo = LedgerRepository(ledger_table)
    event = _event(EventType.SCAN_STARTED, T0)
    repo.append(event)
    with pytest.raises(DuplicateEventError):
        repo.append(event)


def test_events_are_time_ordered_and_scoped_to_correlation(ledger_table: Any) -> None:
    repo = LedgerRepository(ledger_table)
    completed = _event(EventType.SCAN_COMPLETED, T0 + timedelta(seconds=5))
    started = _event(EventType.SCAN_STARTED, T0)
    other = _event(EventType.SCAN_STARTED, T0, correlation_id="corr-2")
    for event in (completed, started, other):
        repo.append(event)
    assert repo.list_by_correlation("corr-1") == [started, completed]


def test_unknown_correlation_returns_empty(ledger_table: Any) -> None:
    assert LedgerRepository(ledger_table).list_by_correlation("missing") == []
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/domain/test_events.py tests/unit/persistence -v`
Expected: FAIL with `ModuleNotFoundError` for `cip.domain.events` / `cip.persistence`.

- [ ] **Step 4: Create `src/cip/domain/events.py`**

```python
from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

GLOBAL_ASSET = "GLOBAL"
_TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


class EventType(StrEnum):
    SCAN_STARTED = "SCAN_STARTED"
    SCAN_COMPLETED = "SCAN_COMPLETED"
    PIPELINE_FAILED = "PIPELINE_FAILED"


def _from_dynamo(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: _from_dynamo(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_from_dynamo(item) for item in value]
    return value


class LedgerEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_type: EventType
    correlation_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    asset: str = GLOBAL_ASSET
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("created_at")
    @classmethod
    def _must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("created_at must be timezone-aware UTC")
        return value.astimezone(UTC)

    @property
    def timestamp(self) -> str:
        return self.created_at.strftime(_TS_FORMAT)

    def to_item(self) -> dict[str, Any]:
        ts = self.timestamp
        return {
            "PK": f"ASSET#{self.asset}",
            "SK": f"EVENT#{ts}#{self.event_id}",
            "GSI1PK": f"CORR#{self.correlation_id}",
            "GSI1SK": f"{ts}#{self.event_id}",
            "GSI2PK": f"TYPE#{self.event_type}#{ts[:10]}",
            "GSI2SK": f"{ts}#{self.event_id}",
            "event_type": str(self.event_type),
            "correlation_id": self.correlation_id,
            "policy_version": self.policy_version,
            "asset": self.asset,
            "created_at": ts,
            "event_id": self.event_id,
            "payload": json.loads(json.dumps(self.payload), parse_float=Decimal),
        }

    @classmethod
    def from_item(cls, item: Mapping[str, Any]) -> LedgerEvent:
        return cls(
            event_type=EventType(item["event_type"]),
            correlation_id=item["correlation_id"],
            policy_version=item["policy_version"],
            asset=item["asset"],
            created_at=datetime.strptime(item["created_at"], _TS_FORMAT).replace(tzinfo=UTC),
            event_id=item["event_id"],
            payload=_from_dynamo(item["payload"]),
        )
```

- [ ] **Step 5: Create `src/cip/persistence/__init__.py` (empty) and `src/cip/persistence/ledger.py`**

```python
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from cip.domain.errors import DuplicateEventError
from cip.domain.events import LedgerEvent

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import Table


class LedgerRepository:
    def __init__(self, table: Table) -> None:
        self._table = table

    def append(self, event: LedgerEvent) -> None:
        try:
            self._table.put_item(
                Item=event.to_item(), ConditionExpression="attribute_not_exists(PK)"
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise DuplicateEventError(event.event_id) from error
            raise

    def list_by_correlation(self, correlation_id: str) -> list[LedgerEvent]:
        condition = Key("GSI1PK").eq(f"CORR#{correlation_id}")
        events: list[LedgerEvent] = []
        start_key: dict[str, Any] | None = None
        while True:
            if start_key is None:
                response = self._table.query(IndexName="GSI1", KeyConditionExpression=condition)
            else:
                response = self._table.query(
                    IndexName="GSI1",
                    KeyConditionExpression=condition,
                    ExclusiveStartKey=start_key,
                )
            events.extend(LedgerEvent.from_item(item) for item in response["Items"])
            start_key = response.get("LastEvaluatedKey")
            if start_key is None:
                return events
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/domain/test_events.py tests/unit/persistence -v`
Expected: 10 passed.

- [ ] **Step 7: Run all checks and commit**

Run: `make check`

```bash
git add src/cip/domain/events.py src/cip/persistence tests/unit/domain/test_events.py tests/unit/persistence
git commit -m "feat: add append-only ledger event model and repository"
```

---

### Task 7: Scan pipeline Lambda handlers (skeleton)

**Files:**
- Create: `src/cip/handlers/__init__.py`, `src/cip/handlers/pipeline.py`, `tests/unit/handlers/__init__.py`, `tests/unit/handlers/test_pipeline.py`

**Interfaces:**
- Consumes: `LedgerRepository`, `ExecutionFlags`, `read_execution_flags`, `LoadedPolicy`, `load_policy`, `LedgerEvent`, `EventType`.
- Produces (Lambda handler paths used by Terraform in Task 11):
  - `cip.handlers.pipeline.start_scan` -> returns `{"correlation_id": str, "policy_version": str}`
  - `cip.handlers.pipeline.complete_scan` -> input `{"correlation_id", "policy_version"}`, returns `{"correlation_id", "status": "COMPLETED"}`
  - `cip.handlers.pipeline.record_failure` -> input includes optional `correlation_id`, `policy_version`, and `error: {"Error", "Cause"}`; returns `{"correlation_id", "status": "FAILED"}`
  - Pure functions `run_start_scan(request, ledger, flags, policy)`, `run_complete_scan(request, ledger)`, `run_record_failure(request, ledger)`
  - Environment variables: `LEDGER_TABLE`, `FLAGS_PREFIX`, `POLICY_PATH`

- [ ] **Step 1: Write the failing tests** `tests/unit/handlers/test_pipeline.py` (also create empty `tests/unit/handlers/__init__.py`)

```python
from pathlib import Path
from typing import Any

import pytest

from cip.config.flags import ExecutionFlags
from cip.domain.events import EventType
from cip.domain.policy import ExecutionMode, LoadedPolicy, load_policy
from cip.handlers.pipeline import run_complete_scan, run_record_failure, run_start_scan
from cip.persistence.ledger import LedgerRepository

REPO_POLICY = Path(__file__).parents[3] / "policies" / "investment-policy.yaml"
SHADOW = ExecutionFlags(ExecutionMode.SHADOW, trading_enabled=False, kill_switch_active=False)


@pytest.fixture
def policy() -> LoadedPolicy:
    return load_policy(REPO_POLICY)


def test_start_scan_records_flags_and_uses_supplied_correlation_id(
    ledger_table: Any, policy: LoadedPolicy
) -> None:
    ledger = LedgerRepository(ledger_table)
    result = run_start_scan({"correlation_id": "corr-9"}, ledger, SHADOW, policy)

    assert result == {"correlation_id": "corr-9", "policy_version": policy.version}
    [event] = ledger.list_by_correlation("corr-9")
    assert event.event_type is EventType.SCAN_STARTED
    assert event.policy_version == policy.version
    assert event.payload == {
        "execution_mode": "SHADOW",
        "trading_enabled": False,
        "kill_switch_active": False,
        "policy_mode": "SHADOW",
    }


def test_start_scan_generates_correlation_id_when_absent(
    ledger_table: Any, policy: LoadedPolicy
) -> None:
    result = run_start_scan({}, LedgerRepository(ledger_table), SHADOW, policy)
    assert len(result["correlation_id"]) == 32


def test_complete_scan_records_completion(ledger_table: Any) -> None:
    ledger = LedgerRepository(ledger_table)
    result = run_complete_scan({"correlation_id": "corr-9", "policy_version": "v1"}, ledger)

    assert result == {"correlation_id": "corr-9", "status": "COMPLETED"}
    [event] = ledger.list_by_correlation("corr-9")
    assert event.event_type is EventType.SCAN_COMPLETED


def test_record_failure_truncates_cause(ledger_table: Any) -> None:
    ledger = LedgerRepository(ledger_table)
    request = {
        "correlation_id": "corr-9",
        "error": {"Error": "Lambda.Unknown", "Cause": "x" * 5000},
    }
    result = run_record_failure(request, ledger)

    assert result == {"correlation_id": "corr-9", "status": "FAILED"}
    [event] = ledger.list_by_correlation("corr-9")
    assert event.event_type is EventType.PIPELINE_FAILED
    assert event.policy_version == "unknown"
    assert event.payload["error"] == "Lambda.Unknown"
    assert len(event.payload["cause"]) == 1000
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/handlers -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cip.handlers'`.

- [ ] **Step 3: Create `src/cip/handlers/__init__.py` (empty) and `src/cip/handlers/pipeline.py`**

```python
from __future__ import annotations

import os
import uuid
from functools import cache
from pathlib import Path
from typing import Any

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext

from cip.config.flags import ExecutionFlags, read_execution_flags
from cip.domain.events import EventType, LedgerEvent
from cip.domain.policy import LoadedPolicy, load_policy
from cip.persistence.ledger import LedgerRepository

logger = Logger(service="cip-pipeline")

_MAX_CAUSE_CHARS = 1000


def run_start_scan(
    request: dict[str, Any],
    ledger: LedgerRepository,
    flags: ExecutionFlags,
    policy: LoadedPolicy,
) -> dict[str, str]:
    correlation_id = str(request.get("correlation_id") or uuid.uuid4().hex)
    logger.append_keys(correlation_id=correlation_id)
    ledger.append(
        LedgerEvent(
            event_type=EventType.SCAN_STARTED,
            correlation_id=correlation_id,
            policy_version=policy.version,
            payload={
                "execution_mode": str(flags.mode),
                "trading_enabled": flags.trading_enabled,
                "kill_switch_active": flags.kill_switch_active,
                "policy_mode": str(policy.policy.execution.mode),
            },
        )
    )
    logger.info("scan started")
    return {"correlation_id": correlation_id, "policy_version": policy.version}


def run_complete_scan(request: dict[str, Any], ledger: LedgerRepository) -> dict[str, str]:
    correlation_id = str(request["correlation_id"])
    logger.append_keys(correlation_id=correlation_id)
    ledger.append(
        LedgerEvent(
            event_type=EventType.SCAN_COMPLETED,
            correlation_id=correlation_id,
            policy_version=str(request["policy_version"]),
        )
    )
    logger.info("scan completed")
    return {"correlation_id": correlation_id, "status": "COMPLETED"}


def run_record_failure(request: dict[str, Any], ledger: LedgerRepository) -> dict[str, str]:
    correlation_id = str(request.get("correlation_id") or "unknown")
    error = request.get("error") or {}
    logger.append_keys(correlation_id=correlation_id)
    ledger.append(
        LedgerEvent(
            event_type=EventType.PIPELINE_FAILED,
            correlation_id=correlation_id,
            policy_version=str(request.get("policy_version") or "unknown"),
            payload={
                "error": str(error.get("Error", "")),
                "cause": str(error.get("Cause", ""))[:_MAX_CAUSE_CHARS],
            },
        )
    )
    logger.error("scan pipeline failed")
    return {"correlation_id": correlation_id, "status": "FAILED"}


@cache
def _ledger() -> LedgerRepository:
    return LedgerRepository(boto3.resource("dynamodb").Table(os.environ["LEDGER_TABLE"]))


@cache
def _policy() -> LoadedPolicy:
    return load_policy(Path(os.environ["POLICY_PATH"]))


def _flags() -> ExecutionFlags:
    return read_execution_flags(boto3.client("ssm"), os.environ["FLAGS_PREFIX"])


@logger.inject_lambda_context
def start_scan(event: dict[str, Any], context: LambdaContext) -> dict[str, str]:
    return run_start_scan(event, _ledger(), _flags(), _policy())


@logger.inject_lambda_context
def complete_scan(event: dict[str, Any], context: LambdaContext) -> dict[str, str]:
    return run_complete_scan(event, _ledger())


@logger.inject_lambda_context
def record_failure(event: dict[str, Any], context: LambdaContext) -> dict[str, str]:
    return run_record_failure(event, _ledger())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/handlers -v`
Expected: 4 passed.

- [ ] **Step 5: Run all checks and commit**

Run: `make check`
Expected: all green. Total coverage >= 85% (the three `@logger.inject_lambda_context` wrappers are covered by integration tests in Task 13).

```bash
git add src/cip/handlers tests/unit/handlers
git commit -m "feat: add scan pipeline handler skeleton"
```

---

### Task 8: Deterministic Lambda artifact build

**Files:**
- Create: `scripts/build_lambda.sh`

**Interfaces:**
- Produces: `build/cip-lambda.zip` containing `cip/`, `policies/investment-policy.yaml`, and Linux arm64 dependencies. Terraform (Task 11) consumes it via `var.artifact_path`.

- [ ] **Step 1: Create `scripts/build_lambda.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/build"
STAGE="$BUILD/lambda"
ARTIFACT="$BUILD/cip-lambda.zip"

rm -rf "$STAGE" "$ARTIFACT"
mkdir -p "$STAGE"

uv export --frozen --no-dev --no-emit-project --format requirements-txt \
  --output-file "$BUILD/requirements.txt"

uv pip install \
  --requirement "$BUILD/requirements.txt" \
  --target "$STAGE" \
  --python-platform aarch64-manylinux2014 \
  --python-version 3.13 \
  --only-binary :all:

cp -R "$ROOT/src/cip" "$STAGE/cip"
mkdir -p "$STAGE/policies"
cp "$ROOT/policies/investment-policy.yaml" "$STAGE/policies/"

find "$STAGE" -name "__pycache__" -type d -prune -exec rm -rf {} +
find "$STAGE" -exec touch -h -t 202001010000 {} +
(cd "$STAGE" && find . -type f | LC_ALL=C sort | zip -X -q "$ARTIFACT" -@)

echo "Built $ARTIFACT ($(du -h "$ARTIFACT" | cut -f1))"
```

- [ ] **Step 2: Build and verify contents**

```bash
chmod +x scripts/build_lambda.sh
make build
unzip -l build/cip-lambda.zip | grep -E "cip/handlers/pipeline.py|policies/investment-policy.yaml|_pydantic_core.*aarch64"
```

Expected: three matching lines, including `pydantic_core/_pydantic_core.cpython-313-aarch64-linux-gnu.so`.

- [ ] **Step 3: Verify the build is reproducible**

```bash
shasum -a 256 build/cip-lambda.zip > /tmp/first.sha && make build && shasum -a 256 -c /tmp/first.sha
```

Expected: `build/cip-lambda.zip: OK`.

- [ ] **Step 4: Commit**

```bash
git add scripts/build_lambda.sh
git commit -m "build: add deterministic arm64 lambda artifact script"
```

---

### Task 9: Terraform bootstrap (applied locally once)

Creates the Terraform state bucket, per-env workload permission boundaries, a monthly budget, and a CloudTrail trail with Terraform; then the GitHub OIDC provider and CI roles with an AWS CLI script (owner choice, ADR-0004).

**Files:**
- Create: `terraform/bootstrap/versions.tf`, `terraform/bootstrap/variables.tf`, `terraform/bootstrap/state.tf`, `terraform/bootstrap/boundaries.tf`, `terraform/bootstrap/budget.tf`, `terraform/bootstrap/cloudtrail.tf`, `terraform/bootstrap/outputs.tf`, `terraform/bootstrap/terraform.tfvars`, `terraform/bootstrap/backend.tf`, `iam/github/plan-policy.json.tpl`, `iam/github/deploy-policy.json.tpl`, `scripts/bootstrap_github_oidc.sh`, `scripts/verify_github_oidc.sh`, `docs/runbooks/bootstrap.md`

**Interfaces:**
- Produces: Terraform outputs `state_bucket` = `cip-tfstate-258485600712` and `workload_boundary_arns` (`cip-dev-workload-boundary`, `cip-prod-workload-boundary`); script output `AWS_ROLE_PLAN|DEV|PROD` for roles `cip-gha-plan|dev|prod`.

- [ ] **Step 1: Check for an existing CloudTrail trail**

Run: `aws cloudtrail describe-trails --profile soworks --query 'trailList[].Name'`
Expected: `[]`. If a multi-region trail already exists, set `create_cloudtrail = false` in Step 10.

- [ ] **Step 2: Create `terraform/bootstrap/versions.tf`**

```hcl
terraform {
  required_version = "~> 1.16"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.67" }
  }
}

provider "aws" {
  region  = var.region
  profile = "soworks"
  default_tags {
    tags = { project = "cip", managed_by = "terraform", stack = "bootstrap" }
  }
}

data "aws_caller_identity" "current" {}

locals {
  account_id   = data.aws_caller_identity.current.account_id
  environments = toset(["dev", "prod"])
}
```

- [ ] **Step 3: Create `terraform/bootstrap/variables.tf`**

```hcl
variable "region" {
  type    = string
  default = "us-east-1"
}

variable "github_repository" {
  type    = string
  default = "soworks/cripto-intelligence-platform"
}

variable "alert_email" {
  type = string
}

variable "monthly_budget_usd" {
  type    = number
  default = 50
}

variable "create_cloudtrail" {
  type    = bool
  default = true
}
```

- [ ] **Step 4: Create `terraform/bootstrap/state.tf`**

```hcl
resource "aws_s3_bucket" "tf_state" {
  #checkov:skip=CKV2_AWS_61:Terraform state keeps all versions intentionally
  bucket = "cip-tfstate-${local.account_id}"
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tf_state" {
  bucket                  = aws_s3_bucket.tf_state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "tf_state_tls" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.tf_state.arn,
      "${aws_s3_bucket.tf_state.arn}/*",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id
  policy = data.aws_iam_policy_document.tf_state_tls.json
}
```

- [ ] **Step 5: Create `terraform/bootstrap/boundaries.tf`**

```hcl
locals {
  other_env = { dev = "prod", prod = "dev" }
}

data "aws_iam_policy_document" "workload_boundary" {
  #checkov:skip=CKV_AWS_355:Permission boundary is a ceiling; grants are scoped in role policies
  #checkov:skip=CKV_AWS_356:Permission boundary is a ceiling; grants are scoped in role policies
  #checkov:skip=CKV_AWS_288:Permission boundary is a ceiling; grants are scoped in role policies
  #checkov:skip=CKV_AWS_289:Permission boundary is a ceiling; grants are scoped in role policies
  #checkov:skip=CKV_AWS_290:Permission boundary is a ceiling; grants are scoped in role policies
  for_each = local.environments

  statement {
    sid = "AllowWorkloadServices"
    actions = [
      "dynamodb:*",
      "s3:*",
      "ssm:GetParameter",
      "ssm:GetParameters",
      "ssm:PutParameter",
      "logs:*",
      "cloudwatch:PutMetricData",
      "xray:PutTraceSegments",
      "xray:PutTelemetryRecords",
      "lambda:InvokeFunction",
      "states:StartExecution",
      "sns:Publish",
      "bedrock:InvokeModel",
      "kms:Decrypt",
    ]
    resources = ["*"]
  }

  dynamic "statement" {
    for_each = each.key == "prod" ? [1] : []
    content {
      sid       = "AllowProdSecrets"
      actions   = ["secretsmanager:GetSecretValue"]
      resources = ["arn:aws:secretsmanager:*:${local.account_id}:secret:cip/prod/*"]
    }
  }

  statement {
    sid     = "DenyOtherEnvironment"
    effect  = "Deny"
    actions = ["*"]
    resources = [
      "arn:aws:dynamodb:*:${local.account_id}:table/cip-${local.other_env[each.key]}-*",
      "arn:aws:s3:::cip-${local.other_env[each.key]}-*",
      "arn:aws:ssm:*:${local.account_id}:parameter/cip/${local.other_env[each.key]}/*",
      "arn:aws:secretsmanager:*:${local.account_id}:secret:cip/${local.other_env[each.key]}/*",
      "arn:aws:lambda:*:${local.account_id}:function:cip-${local.other_env[each.key]}-*",
      "arn:aws:states:*:${local.account_id}:stateMachine:cip-${local.other_env[each.key]}-*",
    ]
  }

  statement {
    sid    = "DenyLedgerMutation"
    effect = "Deny"
    actions = [
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:BatchWriteItem",
      "dynamodb:PartiQLUpdate",
      "dynamodb:PartiQLDelete",
    ]
    resources = ["arn:aws:dynamodb:*:${local.account_id}:table/cip-${each.key}-ledger"]
  }
}

resource "aws_iam_policy" "workload_boundary" {
  for_each = local.environments
  name     = "cip-${each.key}-workload-boundary"
  policy   = data.aws_iam_policy_document.workload_boundary[each.key].json
}
```

- [ ] **Step 6: GitHub OIDC provider and CI roles via AWS CLI (owner choice; not Terraform)**

The OIDC provider and the `cip-gha-plan|dev|prod` roles are managed by an idempotent,
versioned script instead of Terraform (see ADR-0004 amendment). Files:
- `iam/github/plan-policy.json.tpl` - scoped read-only policy for PR plans: Describe/Get/List
  on `cip-dev-*` / `cip-prod-*` resources and `/cip/*` parameters; `s3:GetObject` and
  `s3:ListBucket` (prefix-conditioned) only on the state bucket's `env/dev/` and `env/prod/`
  prefixes (no bootstrap state, no CloudTrail bucket); `sts:GetCallerIdentity`; and the
  list-only `ssm:DescribeParameters` / `logs:DescribeLogGroups`, which have no resource scope.
  No `ReadOnlyAccess`.
- `iam/github/deploy-policy.json.tpl` - per-env deploy policy (`${ENV}` = dev|prod): state
  objects under `env/${ENV}/`, read-for-planning, full control only of `cip-${ENV}-*`
  resources, `iam:CreateRole`/`PutRolePolicy` only with `cip-${ENV}-workload-boundary`,
  `iam:PassRole` only to lambda/states/scheduler, explicit deny on boundary tampering.
- `scripts/bootstrap_github_oidc.sh` - creates the provider if missing; creates or updates
  each role (trust policy, max session 3600, tags), detaches all managed policies, deletes
  unexpected inline policies, and puts the expected one. Trust conditions (`StringEquals`):
  `aud = sts.amazonaws.com`; exact `sub` = `repo:soworks/cripto-intelligence-platform:pull_request`,
  `:environment:dev`, `:environment:prod`; plus `repository_id` and `repository_owner_id`
  from `gh api repos/soworks/cripto-intelligence-platform` when `gh` is authenticated
  (otherwise it prints a WARNING and pinning is a follow-up rerun). `--dry-run` prints the
  rendered documents.
- `scripts/verify_github_oidc.sh` - `iam:SimulatePrincipalPolicy` spot checks with expected
  decisions; exits non-zero on mismatch.

Run after Step 12 (any time; the script only references the boundary ARNs by name):

```bash
scripts/bootstrap_github_oidc.sh --profile soworks
scripts/verify_github_oidc.sh --profile soworks
scripts/bootstrap_github_oidc.sh --profile soworks   # rerun: no detach/delete/untag lines
```

Expected: three `AWS_ROLE_*=arn:aws:iam::258485600712:role/cip-gha-*` lines and
`all checks matched`.

- [ ] **Step 7: Create `terraform/bootstrap/budget.tf`**

```hcl
resource "aws_budgets_budget" "monthly" {
  name         = "cip-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = [20, 50, 100]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [var.alert_email]
    }
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}
```

- [ ] **Step 8: Create `terraform/bootstrap/cloudtrail.tf`**

```hcl
locals {
  trail_name = "cip-management"
  trail_arn  = "arn:aws:cloudtrail:${var.region}:${local.account_id}:trail/${local.trail_name}"
}

resource "aws_s3_bucket" "cloudtrail" {
  #checkov:skip=CKV_AWS_21:CloudTrail log file validation provides integrity; versioning adds cost only
  #checkov:skip=CKV2_AWS_61:Lifecycle is defined in aws_s3_bucket_lifecycle_configuration.cloudtrail
  count  = var.create_cloudtrail ? 1 : 0
  bucket = "cip-cloudtrail-${local.account_id}"
}

resource "aws_s3_bucket_public_access_block" "cloudtrail" {
  count                   = var.create_cloudtrail ? 1 : 0
  bucket                  = aws_s3_bucket.cloudtrail[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "cloudtrail" {
  count  = var.create_cloudtrail ? 1 : 0
  bucket = aws_s3_bucket.cloudtrail[0].id
  rule {
    id     = "expire-after-1-year"
    status = "Enabled"
    filter {}
    expiration {
      days = 365
    }
  }
}

data "aws_iam_policy_document" "cloudtrail_bucket" {
  count = var.create_cloudtrail ? 1 : 0

  statement {
    sid       = "AclCheck"
    actions   = ["s3:GetBucketAcl"]
    resources = [aws_s3_bucket.cloudtrail[0].arn]
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values   = [local.trail_arn]
    }
  }

  statement {
    sid       = "Write"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.cloudtrail[0].arn}/AWSLogs/${local.account_id}/*"]
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "s3:x-amz-acl"
      values   = ["bucket-owner-full-control"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values   = [local.trail_arn]
    }
  }
}

resource "aws_s3_bucket_policy" "cloudtrail" {
  count  = var.create_cloudtrail ? 1 : 0
  bucket = aws_s3_bucket.cloudtrail[0].id
  policy = data.aws_iam_policy_document.cloudtrail_bucket[0].json
}

resource "aws_cloudtrail" "management" {
  count                         = var.create_cloudtrail ? 1 : 0
  name                          = local.trail_name
  s3_bucket_name                = aws_s3_bucket.cloudtrail[0].id
  is_multi_region_trail         = true
  include_global_service_events = true
  enable_log_file_validation    = true
  depends_on                    = [aws_s3_bucket_policy.cloudtrail]
}
```

- [ ] **Step 9: Create `terraform/bootstrap/outputs.tf`**

```hcl
output "state_bucket" {
  value = aws_s3_bucket.tf_state.id
}

output "workload_boundary_arns" {
  value = { for env, policy in aws_iam_policy.workload_boundary : env => policy.arn }
}
```

- [ ] **Step 10: Create `terraform/bootstrap/terraform.tfvars`**

```hcl
alert_email       = "soworks86@gmail.com"
create_cloudtrail = true
```

- [ ] **Step 11: Validate, then apply with local state**

```bash
cd terraform/bootstrap
terraform init
terraform fmt -check && terraform validate
terraform plan -out=tfplan
terraform apply tfplan
```

Expected: `Apply complete!` with outputs `state_bucket = "cip-tfstate-258485600712"` and two boundary ARNs. Confirm the AWS Budgets email subscription if prompted.

- [ ] **Step 12: Migrate bootstrap state into the bucket** - create `terraform/bootstrap/backend.tf`

```hcl
terraform {
  backend "s3" {
    bucket       = "cip-tfstate-258485600712"
    key          = "bootstrap/terraform.tfstate"
    region       = "us-east-1"
    profile      = "soworks"
    use_lockfile = true
    encrypt      = true
  }
}
```

Run: `terraform init -migrate-state` (answer `yes`), then `rm -f terraform.tfstate terraform.tfstate.backup tfplan`.
Expected: `Successfully configured the backend "s3"!`, and `terraform plan` reports `No changes.`

- [ ] **Step 13: Write `docs/runbooks/bootstrap.md`**

Document both halves: (1) the Terraform bootstrap apply/change procedure and what it
creates (state bucket, boundaries, budget, CloudTrail; state at
`s3://cip-tfstate-258485600712/bootstrap/terraform.tfstate`); (2) the OIDC script:
`gh auth login`, `scripts/bootstrap_github_oidc.sh --profile soworks`,
`scripts/verify_github_oidc.sh --profile soworks`, what each run converges (provider,
trust conditions, inline-only policies, max session 3600), `--dry-run`, and that it is
rerun after any edit under `iam/github/`. After bootstrap: confirm the Budgets email and
replace the `asolano` access key with short-lived credentials.

- [ ] **Step 14: Commit**

```bash
cd ../..
git add terraform/bootstrap iam/github scripts/bootstrap_github_oidc.sh scripts/verify_github_oidc.sh docs/runbooks/bootstrap.md
git commit -m "infra: add terraform bootstrap and CLI-managed github oidc roles"
```

---

### Task 10: GitHub repository configuration

**Files:** none (repository settings via `gh`).

**Interfaces:**
- Consumes: Task 9 outputs.
- Produces: GitHub environments `dev` and `prod`; repository variables `AWS_REGION`, `AWS_ROLE_PLAN`, `AWS_ROLE_DEV`, `AWS_ROLE_PROD`, `TF_STATE_BUCKET`; branch protection on `main` requiring the `python` and `terraform` checks.

- [ ] **Step 1: Confirm authentication and repository visibility**

Run: `gh auth status && gh repo view soworks/cripto-intelligence-platform --json visibility,owner --jq '.visibility, .owner.login'`
Expected: logged in; visibility `PRIVATE` or `PUBLIC`. Environment required reviewers and branch protection on **private** repos require GitHub Pro/Team. If the repo is private on a free plan, use the fallback in Step 4.

- [ ] **Step 2: Set repository variables**

```bash
REPO=soworks/cripto-intelligence-platform
gh variable set AWS_REGION --repo $REPO --body us-east-1
gh variable set TF_STATE_BUCKET --repo $REPO \
  --body "$(terraform -chdir=terraform/bootstrap output -raw state_bucket)"
scripts/bootstrap_github_oidc.sh --profile soworks | grep '^AWS_ROLE_' | while IFS='=' read -r name arn; do
  gh variable set "$name" --repo $REPO --body "$arn"
done
gh variable list --repo $REPO
```

Expected: five variables listed.

- [ ] **Step 3: Create environments (prod requires your approval)**

```bash
REPO=soworks/cripto-intelligence-platform
USER_ID=$(gh api user --jq .id)
gh api -X PUT repos/$REPO/environments/dev
gh api -X PUT repos/$REPO/environments/prod --input - <<EOF
{"reviewers":[{"type":"User","id":$USER_ID}],
 "deployment_branch_policy":{"protected_branches":true,"custom_branch_policies":false}}
EOF
```

Expected: JSON responses with `"name":"dev"` and `"name":"prod"`.

- [ ] **Step 4: Protect `main`**

```bash
gh api -X PUT repos/soworks/cripto-intelligence-platform/branches/main/protection --input - <<'EOF'
{"required_status_checks":{"strict":true,"contexts":["python","terraform"]},
 "enforce_admins":false,
 "required_pull_request_reviews":null,
 "restrictions":null,
 "required_linear_history":true,
 "allow_force_pushes":false,
 "allow_deletions":false}
EOF
```

Expected: JSON with `"required_status_checks"`. If the API returns `403 Upgrade to GitHub Pro`, either make the repository public (no secrets are ever committed) or upgrade, and record the choice in ADR-0004.

---

### Task 11: Terraform dev environment

**Files:**
- Create: `terraform/modules/platform-data/{versions,main,variables,outputs}.tf`, `terraform/modules/runtime-flags/{versions,main,variables,outputs}.tf`, `terraform/modules/alerts/{versions,main,variables,outputs}.tf`, `terraform/modules/scan-pipeline/{versions,main,variables,outputs}.tf`, `terraform/modules/scan-pipeline/scan-pipeline.asl.json`, `terraform/environments/dev/{main,variables,outputs}.tf`, `terraform/environments/dev/terraform.tfvars`

**Interfaces:**
- Consumes: `build/cip-lambda.zip` (Task 8); handler paths (Task 7); boundary `cip-dev-workload-boundary` (Task 9).
- Produces (dev outputs used by Task 13): `state_machine_arn`, `ledger_table_name`, `ledger_table_arn`, `pipeline_lambda_role_arn`.

- [ ] **Step 1: Create `versions.tf` in each of the four modules** (`platform-data`, `runtime-flags`, `alerts`, `scan-pipeline`). tflint's recommended preset requires it.

```hcl
terraform {
  required_version = "~> 1.16"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.67" }
  }
}
```

Then create `terraform/modules/platform-data/variables.tf`:

```hcl
variable "env" {
  type = string
}

variable "deletion_protection" {
  type = bool
}
```

- [ ] **Step 2: Create `terraform/modules/platform-data/main.tf`**

```hcl
data "aws_caller_identity" "current" {}

locals {
  prefix = "cip-${var.env}"
}

resource "aws_s3_bucket" "data" {
  bucket        = "${local.prefix}-data-${data.aws_caller_identity.current.account_id}"
  force_destroy = !var.deletion_protection
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    id     = "snapshots-to-glacier-ir"
    status = "Enabled"
    filter {
      prefix = "snapshots/"
    }
    transition {
      days          = 90
      storage_class = "GLACIER_IR"
    }
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

resource "aws_dynamodb_table" "ledger" {
  name                        = "${local.prefix}-ledger"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "PK"
  range_key                   = "SK"
  deletion_protection_enabled = var.deletion_protection

  dynamic "attribute" {
    for_each = ["PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK"]
    content {
      name = attribute.value
      type = "S"
    }
  }

  global_secondary_index {
    name            = "GSI1"
    hash_key        = "GSI1PK"
    range_key       = "GSI1SK"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "GSI2"
    hash_key        = "GSI2PK"
    range_key       = "GSI2SK"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }
}

resource "aws_dynamodb_table" "state" {
  name                        = "${local.prefix}-state"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "PK"
  range_key                   = "SK"
  deletion_protection_enabled = var.deletion_protection

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }
}

resource "aws_dynamodb_table" "counters" {
  name                        = "${local.prefix}-counters"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "PK"
  deletion_protection_enabled = var.deletion_protection

  attribute {
    name = "PK"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }
}
```

If `terraform validate` warns that `hash_key`/`range_key` inside `global_secondary_index` are deprecated in the installed provider, switch those two blocks to the provider's `key_schema` syntax shown in the warning; table behavior is unchanged.

- [ ] **Step 3: Create `terraform/modules/platform-data/outputs.tf`**

```hcl
output "data_bucket_name" {
  value = aws_s3_bucket.data.id
}

output "ledger_table_name" {
  value = aws_dynamodb_table.ledger.name
}

output "ledger_table_arn" {
  value = aws_dynamodb_table.ledger.arn
}

output "state_table_arn" {
  value = aws_dynamodb_table.state.arn
}

output "counters_table_arn" {
  value = aws_dynamodb_table.counters.arn
}
```

- [ ] **Step 4: Create the `runtime-flags` module**

`terraform/modules/runtime-flags/variables.tf`:

```hcl
variable "env" {
  type = string
}
```

`terraform/modules/runtime-flags/main.tf` (mode and trading flag are Terraform-managed, so turning trading on requires a reviewed PR; the kill switch is left to runtime control):

```hcl
locals {
  prefix = "/cip/${var.env}"
}

resource "aws_ssm_parameter" "execution_mode" {
  name  = "${local.prefix}/execution_mode"
  type  = "String"
  value = "SHADOW"
}

resource "aws_ssm_parameter" "trading_enabled" {
  name  = "${local.prefix}/trading_enabled"
  type  = "String"
  value = "false"
}

resource "aws_ssm_parameter" "kill_switch" {
  name  = "${local.prefix}/kill_switch"
  type  = "String"
  value = "false"
  lifecycle {
    ignore_changes = [value]
  }
}
```

`terraform/modules/runtime-flags/outputs.tf`:

```hcl
output "prefix" {
  value = local.prefix
}
```

- [ ] **Step 5: Create the `alerts` module**

`terraform/modules/alerts/variables.tf`:

```hcl
variable "env" {
  type = string
}

variable "alert_email" {
  type = string
}
```

`terraform/modules/alerts/main.tf`:

```hcl
resource "aws_sns_topic" "alerts" {
  name = "cip-${var.env}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}
```

`terraform/modules/alerts/outputs.tf`:

```hcl
output "topic_arn" {
  value = aws_sns_topic.alerts.arn
}
```

- [ ] **Step 6: Create `terraform/modules/scan-pipeline/scan-pipeline.asl.json`**

```json
{
  "Comment": "CIP scan pipeline - Phase 1 skeleton",
  "StartAt": "HasCorrelationId",
  "States": {
    "HasCorrelationId": {
      "Type": "Choice",
      "Choices": [{ "Variable": "$.correlation_id", "IsPresent": true, "Next": "StartScan" }],
      "Default": "AssignCorrelationId"
    },
    "AssignCorrelationId": {
      "Type": "Pass",
      "Parameters": { "correlation_id.$": "States.UUID()" },
      "Next": "StartScan"
    },
    "StartScan": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke",
      "Parameters": { "FunctionName": "${start_scan_arn}", "Payload.$": "$" },
      "OutputPath": "$.Payload",
      "Retry": [
        {
          "ErrorEquals": ["Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException", "Lambda.TooManyRequestsException"],
          "IntervalSeconds": 2,
          "MaxAttempts": 3,
          "BackoffRate": 2,
          "JitterStrategy": "FULL"
        }
      ],
      "Catch": [{ "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "RecordFailure" }],
      "Next": "CompleteScan"
    },
    "CompleteScan": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke",
      "Parameters": { "FunctionName": "${complete_scan_arn}", "Payload.$": "$" },
      "OutputPath": "$.Payload",
      "Retry": [
        {
          "ErrorEquals": ["Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException", "Lambda.TooManyRequestsException"],
          "IntervalSeconds": 2,
          "MaxAttempts": 3,
          "BackoffRate": 2,
          "JitterStrategy": "FULL"
        }
      ],
      "Catch": [{ "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "RecordFailure" }],
      "End": true
    },
    "RecordFailure": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke",
      "Parameters": { "FunctionName": "${record_failure_arn}", "Payload.$": "$" },
      "OutputPath": "$.Payload",
      "Next": "Failed"
    },
    "Failed": { "Type": "Fail", "Error": "PipelineFailed", "Cause": "See PIPELINE_FAILED ledger event" }
  }
}
```

- [ ] **Step 7: Create `terraform/modules/scan-pipeline/variables.tf`**

```hcl
variable "env" {
  type = string
}

variable "artifact_path" {
  type = string
}

variable "ledger_table_name" {
  type = string
}

variable "ledger_table_arn" {
  type = string
}

variable "flags_prefix" {
  type = string
}

variable "alarm_topic_arn" {
  type = string
}

variable "permissions_boundary_arn" {
  type = string
}

variable "schedule_expression" {
  type    = string
  default = "rate(1 hour)"
}

variable "schedule_enabled" {
  type    = bool
  default = true
}

variable "log_retention_days" {
  type    = number
  default = 14
}
```

- [ ] **Step 8: Create `terraform/modules/scan-pipeline/main.tf`**

```hcl
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  prefix     = "cip-${var.env}"
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region
  functions = {
    start-scan     = "cip.handlers.pipeline.start_scan"
    complete-scan  = "cip.handlers.pipeline.complete_scan"
    record-failure = "cip.handlers.pipeline.record_failure"
  }
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "pipeline_lambda" {
  name                 = "${local.prefix}-pipeline-lambda"
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume.json
  permissions_boundary = var.permissions_boundary_arn
}

resource "aws_cloudwatch_log_group" "fn" {
  for_each          = local.functions
  name              = "/aws/lambda/${local.prefix}-${each.key}"
  retention_in_days = var.log_retention_days
}

data "aws_iam_policy_document" "pipeline_lambda" {
  statement {
    sid       = "LedgerAppend"
    actions   = ["dynamodb:PutItem"]
    resources = [var.ledger_table_arn]
  }

  statement {
    sid       = "FlagsRead"
    actions   = ["ssm:GetParameters"]
    resources = ["arn:aws:ssm:${local.region}:${local.account_id}:parameter${var.flags_prefix}/*"]
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [for group in aws_cloudwatch_log_group.fn : "${group.arn}:*"]
  }

  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }

  statement {
    sid    = "DenyLedgerMutation"
    effect = "Deny"
    actions = [
      "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:BatchWriteItem",
      "dynamodb:PartiQLUpdate", "dynamodb:PartiQLDelete",
    ]
    resources = [var.ledger_table_arn]
  }
}

resource "aws_iam_role_policy" "pipeline_lambda" {
  name   = "${local.prefix}-pipeline-lambda"
  role   = aws_iam_role.pipeline_lambda.id
  policy = data.aws_iam_policy_document.pipeline_lambda.json
}

resource "aws_lambda_function" "fn" {
  for_each         = local.functions
  function_name    = "${local.prefix}-${each.key}"
  role             = aws_iam_role.pipeline_lambda.arn
  handler          = each.value
  runtime          = "python3.13"
  architectures    = ["arm64"]
  filename         = var.artifact_path
  source_code_hash = filebase64sha256(var.artifact_path)
  memory_size      = 256
  timeout          = 30

  environment {
    variables = {
      LEDGER_TABLE            = var.ledger_table_name
      FLAGS_PREFIX            = var.flags_prefix
      POLICY_PATH             = "/var/task/policies/investment-policy.yaml"
      POWERTOOLS_SERVICE_NAME = "cip-pipeline"
      POWERTOOLS_LOG_LEVEL    = "INFO"
    }
  }

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.fn[each.key].name
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_iam_role_policy.pipeline_lambda]
}

data "aws_iam_policy_document" "states_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "state_machine" {
  name                 = "${local.prefix}-scan-sfn"
  assume_role_policy   = data.aws_iam_policy_document.states_assume.json
  permissions_boundary = var.permissions_boundary_arn
}

data "aws_iam_policy_document" "state_machine" {
  statement {
    sid     = "InvokePipelineLambdas"
    actions = ["lambda:InvokeFunction"]
    resources = flatten([
      for fn in aws_lambda_function.fn : [fn.arn, "${fn.arn}:*"]
    ])
  }

  statement {
    sid = "LogDelivery"
    actions = [
      "logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery", "logs:ListLogDeliveries", "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies", "logs:DescribeLogGroups",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "state_machine" {
  name   = "${local.prefix}-scan-sfn"
  role   = aws_iam_role.state_machine.id
  policy = data.aws_iam_policy_document.state_machine.json
}

resource "aws_cloudwatch_log_group" "sfn" {
  name              = "/aws/vendedlogs/states/${local.prefix}-scan-pipeline"
  retention_in_days = var.log_retention_days
}

resource "aws_sfn_state_machine" "scan" {
  name     = "${local.prefix}-scan-pipeline"
  role_arn = aws_iam_role.state_machine.arn
  type     = "STANDARD"
  definition = templatefile("${path.module}/scan-pipeline.asl.json", {
    start_scan_arn     = aws_lambda_function.fn["start-scan"].arn
    complete_scan_arn  = aws_lambda_function.fn["complete-scan"].arn
    record_failure_arn = aws_lambda_function.fn["record-failure"].arn
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn.arn}:*"
    include_execution_data = true
    level                  = "ERROR"
  }

  depends_on = [aws_iam_role_policy.state_machine]
}

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name                 = "${local.prefix}-scan-scheduler"
  assume_role_policy   = data.aws_iam_policy_document.scheduler_assume.json
  permissions_boundary = var.permissions_boundary_arn
}

resource "aws_iam_role_policy" "scheduler" {
  name = "${local.prefix}-scan-scheduler"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "states:StartExecution"
      Resource = aws_sfn_state_machine.scan.arn
    }]
  })
}

resource "aws_scheduler_schedule" "scan" {
  name                = "${local.prefix}-scan"
  schedule_expression = var.schedule_expression
  state               = var.schedule_enabled ? "ENABLED" : "DISABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_sfn_state_machine.scan.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ trigger = "schedule" })
    retry_policy {
      maximum_retry_attempts = 0
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "pipeline_failed" {
  alarm_name          = "${local.prefix}-scan-pipeline-failed"
  namespace           = "AWS/States"
  metric_name         = "ExecutionsFailed"
  dimensions          = { StateMachineArn = aws_sfn_state_machine.scan.arn }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alarm_topic_arn]
}

resource "aws_cloudwatch_metric_alarm" "no_successful_scan" {
  count               = var.schedule_enabled ? 1 : 0
  alarm_name          = "${local.prefix}-scan-pipeline-missed"
  namespace           = "AWS/States"
  metric_name         = "ExecutionsSucceeded"
  dimensions          = { StateMachineArn = aws_sfn_state_machine.scan.arn }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [var.alarm_topic_arn]
}
```

- [ ] **Step 9: Create `terraform/modules/scan-pipeline/outputs.tf`**

```hcl
output "state_machine_arn" {
  value = aws_sfn_state_machine.scan.arn
}

output "pipeline_lambda_role_arn" {
  value = aws_iam_role.pipeline_lambda.arn
}
```

- [ ] **Step 10: Create `terraform/environments/dev/variables.tf` and `terraform.tfvars`**

```hcl
variable "region" {
  type    = string
  default = "us-east-1"
}

variable "alert_email" {
  type = string
}

variable "artifact_path" {
  type    = string
  default = "../../../build/cip-lambda.zip"
}
```

`terraform/environments/dev/terraform.tfvars`:

```hcl
alert_email = "soworks86@gmail.com"
```

- [ ] **Step 11: Create `terraform/environments/dev/main.tf`**

```hcl
terraform {
  required_version = "~> 1.16"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.67" }
  }
  backend "s3" {
    key          = "env/dev/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
    encrypt      = true
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { project = "cip", env = "dev", managed_by = "terraform" }
  }
}

data "aws_caller_identity" "current" {}

locals {
  # Built from the account ID: the PR plan role has no iam:ListPolicies.
  boundary_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/cip-dev-workload-boundary"
}

module "data" {
  source              = "../../modules/platform-data"
  env                 = "dev"
  deletion_protection = false
}

module "flags" {
  source = "../../modules/runtime-flags"
  env    = "dev"
}

module "alerts" {
  source      = "../../modules/alerts"
  env         = "dev"
  alert_email = var.alert_email
}

module "pipeline" {
  source                   = "../../modules/scan-pipeline"
  env                      = "dev"
  artifact_path            = var.artifact_path
  ledger_table_name        = module.data.ledger_table_name
  ledger_table_arn         = module.data.ledger_table_arn
  flags_prefix             = module.flags.prefix
  alarm_topic_arn          = module.alerts.topic_arn
  permissions_boundary_arn = local.boundary_arn
}
```

- [ ] **Step 12: Create `terraform/environments/dev/outputs.tf`**

```hcl
output "state_machine_arn" {
  value = module.pipeline.state_machine_arn
}

output "ledger_table_name" {
  value = module.data.ledger_table_name
}

output "ledger_table_arn" {
  value = module.data.ledger_table_arn
}

output "pipeline_lambda_role_arn" {
  value = module.pipeline.pipeline_lambda_role_arn
}
```

- [ ] **Step 13: Validate and plan locally (no apply - CI applies)**

```bash
make build
cd terraform/environments/dev
AWS_PROFILE=soworks terraform init -backend-config="bucket=cip-tfstate-258485600712"
terraform fmt -check -recursive ../.. && terraform validate
AWS_PROFILE=soworks terraform plan -lock=false
```

Expected: `Success! The configuration is valid.` and a plan that creates the data bucket, 3 tables, 3 SSM parameters, the SNS topic and subscription, 3 Lambdas, 3 roles, the state machine, the schedule, and 2 alarms. Zero destroys.

- [ ] **Step 14: Commit**

```bash
cd ../../..
git add terraform/modules terraform/environments/dev
git commit -m "infra: add dev environment (data, flags, alerts, scan pipeline)"
```

---

### Task 12: Pull-request workflow

**Files:**
- Create: `.github/workflows/pull-request.yml`, `.checkov.yaml`

**Interfaces:**
- Consumes: repository variables (Task 10); `make` targets (Task 1); `scripts/build_lambda.sh` (Task 8).
- Produces: required checks `python` and `terraform`.

- [ ] **Step 1: Create `.checkov.yaml`** (every skip has a reason; add new skips only with a reason)

```yaml
framework:
  - terraform
skip-check:
  - CKV_AWS_117   # Lambda outside VPC by design: public exchange APIs, no NAT cost (ADR-0003)
  - CKV_AWS_116   # Lambda DLQ: Step Functions catches and records failures (ADR-0006)
  - CKV_AWS_173   # Lambda env var CMK: env vars hold no secrets
  - CKV_AWS_272   # Lambda code signing: deferred until prod executor (M6)
  - CKV_AWS_115   # Reserved concurrency: deferred per reference doc 8.1
  - CKV_AWS_158   # Log group CMK: AWS-managed encryption is sufficient for v1
  - CKV_AWS_338   # 1-year log retention: cost; 14d dev / 90d prod
  - CKV_AWS_119   # DynamoDB CMK: AWS-owned key sufficient for v1
  - CKV_AWS_144   # S3 cross-region replication: not required for v1
  - CKV_AWS_145   # S3 CMK: SSE-S3 sufficient for v1
  - CKV_AWS_18    # S3 access logging: CloudTrail covers management events
  - CKV2_AWS_62   # S3 event notifications: not used
  - CKV_AWS_26    # SNS CMK: alert payloads contain no secrets
  - CKV_AWS_337   # SSM CMK: execution flags are non-secret
  - CKV_AWS_35    # CloudTrail CMK: SSE-S3 sufficient for v1
  - CKV_AWS_252   # CloudTrail SNS: not required
  - CKV2_AWS_10   # CloudTrail to CloudWatch Logs: cost; S3 retention used
```

- [ ] **Step 2: Create `.github/workflows/pull-request.yml`**

```yaml
name: pull-request

on:
  pull_request:
    branches: [main]

permissions:
  contents: read
  id-token: write

concurrency:
  group: pr-${{ github.event.pull_request.number }}
  cancel-in-progress: true

jobs:
  python:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v5
      - uses: astral-sh/setup-uv@v6
        with:
          enable-cache: true
      - run: uv sync --all-groups --frozen
      - run: uv run ruff format --check .
      - run: uv run ruff check .
      - run: uv run mypy
      - run: uv run pytest --cov --cov-report=term-missing
      - run: uv run pip-audit --skip-editable

  terraform:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v5
      - uses: astral-sh/setup-uv@v6
      - uses: hashicorp/setup-terraform@v3
        with:
          terraform_version: 1.16.5
      - uses: terraform-linters/setup-tflint@v4
      - run: terraform fmt -check -recursive terraform
      - run: tflint --chdir=terraform --recursive
      - run: uvx checkov --directory terraform --config-file .checkov.yaml --quiet --compact
      - run: ./scripts/build_lambda.sh
      - uses: aws-actions/configure-aws-credentials@v5
        with:
          role-to-assume: ${{ vars.AWS_ROLE_PLAN }}
          aws-region: ${{ vars.AWS_REGION }}
      - name: terraform plan (dev)
        working-directory: terraform/environments/dev
        run: |
          terraform init -input=false -backend-config="bucket=${{ vars.TF_STATE_BUCKET }}"
          terraform validate
          terraform plan -input=false -no-color -lock=false | tee plan.txt
          {
            echo '### Terraform plan (dev)'
            echo '```'
            tail -n 60 plan.txt
            echo '```'
          } >> "$GITHUB_STEP_SUMMARY"
```

- [ ] **Step 3: Push on a branch and open a PR**

```bash
git checkout -b ci/pull-request-workflow
git add .github/workflows/pull-request.yml .checkov.yaml
git commit -m "ci: add pull-request workflow"
git push -u origin ci/pull-request-workflow
gh pr create --fill --base main
gh pr checks --watch
```

Expected: `python` and `terraform` both pass, and the plan summary shows the dev resources from Task 11 Step 13. If checkov reports a finding not covered above, fix it in Terraform, or add a skip with a written reason, and push again.

- [ ] **Step 4: Merge**

Run: `gh pr merge --squash --delete-branch`

---

### Task 13: Deploy-dev workflow and integration tests

**Files:**
- Create: `.github/workflows/deploy-dev.yml`, `tests/integration/__init__.py`, `tests/integration/conftest.py`, `tests/integration/test_scan_pipeline.py`, `tests/integration/test_iam_boundaries.py`

**Interfaces:**
- Consumes: dev outputs `state_machine_arn`, `ledger_table_name`, `ledger_table_arn`, `pipeline_lambda_role_arn` (Task 11), exported as `CIP_STATE_MACHINE_ARN`, `CIP_LEDGER_TABLE`, `CIP_LEDGER_TABLE_ARN`, `CIP_PIPELINE_ROLE_ARN`.

- [ ] **Step 1: Create `tests/integration/conftest.py`** (and an empty `tests/integration/__init__.py`)

```python
import os

import pytest


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.fail(f"{name} must be set for integration tests")
    return value
```

- [ ] **Step 2: Create `tests/integration/test_scan_pipeline.py`**

```python
import json
import time
import uuid

import boto3
import pytest

from cip.domain.events import EventType, LedgerEvent
from cip.persistence.ledger import LedgerRepository
from tests.integration.conftest import require_env

pytestmark = pytest.mark.integration


def _wait_for_execution(sfn: object, execution_arn: str, timeout_s: int = 120) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status: str = sfn.describe_execution(executionArn=execution_arn)["status"]  # type: ignore[attr-defined]
        if status != "RUNNING":
            return status
        time.sleep(2)
    pytest.fail(f"execution {execution_arn} still RUNNING after {timeout_s}s")


def _wait_for_events(ledger: LedgerRepository, correlation_id: str, count: int) -> list[LedgerEvent]:
    for _ in range(10):
        events = ledger.list_by_correlation(correlation_id)
        if len(events) >= count:
            return events
        time.sleep(1)
    return ledger.list_by_correlation(correlation_id)


def test_pipeline_records_start_and_completion_in_shadow_mode() -> None:
    sfn = boto3.client("stepfunctions")
    correlation_id = f"it-{uuid.uuid4().hex}"
    execution = sfn.start_execution(
        stateMachineArn=require_env("CIP_STATE_MACHINE_ARN"),
        input=json.dumps({"correlation_id": correlation_id}),
    )

    assert _wait_for_execution(sfn, execution["executionArn"]) == "SUCCEEDED"

    ledger = LedgerRepository(boto3.resource("dynamodb").Table(require_env("CIP_LEDGER_TABLE")))
    events = _wait_for_events(ledger, correlation_id, count=2)
    assert [event.event_type for event in events] == [
        EventType.SCAN_STARTED,
        EventType.SCAN_COMPLETED,
    ]
    assert events[0].payload["execution_mode"] == "SHADOW"
    assert events[0].payload["trading_enabled"] is False
    assert events[0].policy_version == events[1].policy_version
```

- [ ] **Step 3: Create `tests/integration/test_iam_boundaries.py`**

```python
import boto3
import pytest

from tests.integration.conftest import require_env

pytestmark = pytest.mark.integration


def _decision(action: str, resource: str) -> str:
    iam = boto3.client("iam")
    result = iam.simulate_principal_policy(
        PolicySourceArn=require_env("CIP_PIPELINE_ROLE_ARN"),
        ActionNames=[action],
        ResourceArns=[resource],
    )
    decision: str = result["EvaluationResults"][0]["EvalDecision"]
    return decision


@pytest.mark.parametrize(
    "action", ["dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:BatchWriteItem"]
)
def test_pipeline_role_cannot_mutate_ledger(action: str) -> None:
    assert _decision(action, require_env("CIP_LEDGER_TABLE_ARN")) != "allowed"


def test_pipeline_role_cannot_read_secrets() -> None:
    assert _decision("secretsmanager:GetSecretValue", "*") != "allowed"


def test_pipeline_role_cannot_invoke_bedrock() -> None:
    assert _decision("bedrock:InvokeModel", "*") != "allowed"


def test_pipeline_role_can_append_to_ledger() -> None:
    assert _decision("dynamodb:PutItem", require_env("CIP_LEDGER_TABLE_ARN")) == "allowed"
```

- [ ] **Step 4: Create `.github/workflows/deploy-dev.yml`**

```yaml
name: deploy-dev

on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read
  id-token: write

concurrency:
  group: deploy-dev
  cancel-in-progress: false

jobs:
  deploy:
    runs-on: ubuntu-24.04
    environment: dev
    steps:
      - uses: actions/checkout@v5
      - uses: astral-sh/setup-uv@v6
        with:
          enable-cache: true
      - uses: hashicorp/setup-terraform@v3
        with:
          terraform_version: 1.16.5
          terraform_wrapper: false
      - run: uv sync --all-groups --frozen
      - run: ./scripts/build_lambda.sh
      - uses: aws-actions/configure-aws-credentials@v5
        with:
          role-to-assume: ${{ vars.AWS_ROLE_DEV }}
          aws-region: ${{ vars.AWS_REGION }}
      - name: terraform apply (dev)
        working-directory: terraform/environments/dev
        run: |
          terraform init -input=false -backend-config="bucket=${{ vars.TF_STATE_BUCKET }}"
          terraform apply -input=false -auto-approve
          {
            echo "CIP_STATE_MACHINE_ARN=$(terraform output -raw state_machine_arn)"
            echo "CIP_LEDGER_TABLE=$(terraform output -raw ledger_table_name)"
            echo "CIP_LEDGER_TABLE_ARN=$(terraform output -raw ledger_table_arn)"
            echo "CIP_PIPELINE_ROLE_ARN=$(terraform output -raw pipeline_lambda_role_arn)"
          } >> "$GITHUB_ENV"
      - name: integration tests
        run: uv run pytest -m integration tests/integration -v
```

- [ ] **Step 5: Verify the unit suite still excludes integration tests locally**

Run: `make check`
Expected: green; the output shows the integration tests as deselected.

- [ ] **Step 6: Ship through a PR and watch the deploy**

```bash
git checkout -b ci/deploy-dev
git add .github/workflows/deploy-dev.yml tests/integration
git commit -m "ci: deploy dev via OIDC and run integration tests"
git push -u origin ci/deploy-dev
gh pr create --fill --base main && gh pr checks --watch
gh pr merge --squash --delete-branch
gh run watch "$(gh run list --workflow deploy-dev.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
```

Expected: `deploy-dev` succeeds: `terraform apply` completes, and the integration tests report 7 passed.

- [ ] **Step 7: Confirm alerts and the schedule**

- Confirm the SNS email subscription from the AWS notification email.
- After about 1 hour, run:

```bash
aws stepfunctions list-executions --profile soworks \
  --state-machine-arn "$(cd terraform/environments/dev && AWS_PROFILE=soworks terraform output -raw state_machine_arn)" \
  --max-results 3 --query 'executions[].[name,status]' --output table
```

Expected: at least one scheduler-triggered `SUCCEEDED` execution.

---

## Phase 1 exit criteria (maps to reference doc M0 + M1)

- [ ] `make check` is green locally and in CI; coverage >= 85%; policy/flags modules are at 100% branch coverage.
- [ ] Binance reachability is decided and recorded in ADR-0003.
- [ ] Bootstrap is applied; Terraform state lives in S3 with native locking; no AWS keys exist in GitHub.
- [ ] `deploy-dev` deploys via OIDC; integration tests prove the pipeline records SCAN_STARTED/SCAN_COMPLETED in SHADOW mode.
- [ ] IAM negative tests pass: the pipeline role cannot mutate the ledger, read secrets, or call Bedrock.
- [ ] Budget, CloudTrail, pipeline-failed and missed-schedule alarms are active, with email subscriptions confirmed.
