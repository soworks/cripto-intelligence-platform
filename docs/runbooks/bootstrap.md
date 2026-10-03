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
- CloudTrail `cip-management` (multi-region, log validation, 1-year retention)

## 2. GitHub OIDC provider and CI roles (AWS CLI)
    gh auth login                                  # enables repository_id pinning
    scripts/bootstrap_github_oidc.sh --profile soworks
    scripts/verify_github_oidc.sh --profile soworks

`bootstrap_github_oidc.sh` is idempotent: rerun it after editing anything under
`iam/github/`. Each run:
- creates the `token.actions.githubusercontent.com` provider if missing (audience `sts.amazonaws.com`);
- creates or updates `cip-gha-plan`, `cip-gha-dev`, `cip-gha-prod` (max session 3600 s);
- rewrites each trust policy: `aud = sts.amazonaws.com`, exact `sub`
  (`repo:soworks/cripto-intelligence-platform:pull_request` / `:environment:dev` /
  `:environment:prod`), and `repository_id` + `repository_owner_id` when `gh` is
  authenticated (or `GITHUB_REPOSITORY_ID` / `GITHUB_REPOSITORY_OWNER_ID` are set).
  Without them it prints a WARNING; rerun once `gh` is logged in;
- detaches every managed policy, deletes unexpected inline policies, and puts the
  single expected inline policy (`cip-gha-plan-read`, `cip-dev-deploy`, `cip-prod-deploy`);
- prints `AWS_ROLE_PLAN|DEV|PROD=<arn>` for the GitHub repository variables.

Preview the exact documents without changing anything: `scripts/bootstrap_github_oidc.sh --dry-run`.

`verify_github_oidc.sh` runs `iam:SimulatePrincipalPolicy` spot checks (for example: the
plan role cannot read bootstrap state or the CloudTrail bucket; the dev role cannot touch
`cip-prod-*` or create roles without the dev boundary) and exits non-zero on any mismatch.

## After bootstrap
- Confirm the AWS Budgets notification email if prompted.
- Replace the `asolano` access key with short-lived credentials (IAM Identity Center or `aws login`).
