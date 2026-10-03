# Bootstrap runbook

Applied locally by the account owner. CI never applies `terraform/bootstrap` and never
runs the OIDC script.

## Prerequisites
- AWS CLI profile `soworks` with administrator rights; MFA enabled on root and on IAM user `asolano`.
- Terraform 1.16.5 (`tfenv use 1.16.5`), `jq`, and `gh` (authenticated, for repository ID pinning).

## 1. Terraform bootstrap (state, boundaries, budget, CloudTrail)
    cd terraform/bootstrap
    terraform init
    terraform plan -out=tfplan && terraform apply tfplan

State lives in `s3://cip-tfstate-258485600712/bootstrap/terraform.tfstate` (S3 native lock file).

Creates:
- `cip-tfstate-258485600712` state bucket (versioned, TLS-only, no public access, `prevent_destroy`)
- Boundaries `cip-dev-workload-boundary`, `cip-prod-workload-boundary`
- Budget `cip-monthly` (US$50; email at 20/50/100% actual and 100% forecast)
- CloudTrail `cip-management` (multi-region, log validation, 1-year retention); its bucket
  `cip-cloudtrail-258485600712` is `module.cloudtrail_bucket` (terraform-aws-modules/s3-bucket,
  TLS-only policy plus the `aws:SourceArn`-scoped CloudTrail statements)

Module usage follows ADR-0008: registry modules pinned to exact versions. The state bucket,
boundaries, budget and trail are plain resources on purpose. When a refactor moves live
resources into a module, add `moved {}` blocks and apply only a plan with 0 to destroy and
no replacements.

## 2. GitHub OIDC provider and CI roles (AWS CLI)
    gh auth login                                  # enables repository_id pinning
    scripts/bootstrap_github_oidc.sh --profile soworks
    scripts/verify_github_oidc.sh --profile soworks

`bootstrap_github_oidc.sh` is idempotent: rerun it after editing anything under
`iam/github/`. Each run:
- creates the `token.actions.githubusercontent.com` provider if missing (audience `sts.amazonaws.com`);
- creates or updates `cip-gha-plan`, `cip-gha-dev`, `cip-gha-prod` (max session 3600 s);
- rewrites each trust policy: `aud = sts.amazonaws.com`, exact `sub`, and
  `repository_id` + `repository_owner_id`. The repository uses GitHub immutable subjects,
  so `sub` = `repo:soworks@146444006/cripto-intelligence-platform@1403465152:` +
  `pull_request` / `environment:dev` / `environment:prod`. The prefix and IDs are read with
  `gh api` (`actions/oidc/customization/sub` -> `sub_claim_prefix`, and `repos/{repo}`), or
  from `GITHUB_SUB_PREFIX` / `GITHUB_REPOSITORY_ID` / `GITHUB_REPOSITORY_OWNER_ID`.
  Without them it prints WARNINGs and falls back to the legacy `repo:OWNER/REPO` prefix,
  which will not match this repository; rerun once `gh` is logged in. A custom
  (non-default) sub claim template makes the script exit with an error;
- detaches every managed policy, deletes unexpected inline policies, and puts the
  single expected inline policy (`cip-gha-plan-read`, `cip-dev-deploy`, `cip-prod-deploy`);
- prints `AWS_ROLE_PLAN|DEV|PROD=<arn>` for the GitHub repository variables.

Preview the exact documents without changing anything: `scripts/bootstrap_github_oidc.sh --dry-run`.

`verify_github_oidc.sh` runs `iam:SimulatePrincipalPolicy` spot checks (for example: the
plan role cannot read bootstrap state or the CloudTrail bucket; the dev role cannot touch
`cip-prod-*` or create roles without the dev boundary) and exits non-zero on any mismatch.

The plan and deploy roles have `events:DescribeEventBus` on `event-bus/default` only. The
eventbridge module reads the default bus even though it only manages schedules (ADR-0008).

The OIDC roles stay on this script. The iam module's `iam-oidc-provider` and GitHub OIDC
`iam-role` submodules are a possible future replacement (ADR-0008); we are not switching now.

## After bootstrap
- Confirm the AWS Budgets notification email if prompted.
- Replace the `asolano` access key with short-lived credentials (IAM Identity Center or `aws login`).
