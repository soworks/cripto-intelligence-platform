# Bootstrap runbook

Applied once, locally, by the account owner. CI never applies `terraform/bootstrap`.

## Prerequisites
- AWS CLI profile `soworks` with administrator rights; MFA enabled on root and on IAM user `asolano`.
- Terraform 1.16.5 (`tfenv use 1.16.5`).

## Apply / change
    cd terraform/bootstrap
    terraform init
    terraform plan -out=tfplan && terraform apply tfplan

State lives in `s3://cip-tfstate-258485600712/bootstrap/terraform.tfstate` (S3 native lock file).

## What it creates
- `cip-tfstate-258485600712` state bucket (versioned, TLS-only, no public access, `prevent_destroy`)
- GitHub OIDC provider; roles `cip-gha-plan` (PRs, ReadOnlyAccess), `cip-gha-dev`, `cip-gha-prod`
- Boundaries `cip-dev-workload-boundary`, `cip-prod-workload-boundary`
- Budget `cip-monthly` (US$50; email at 20/50/100% actual and 100% forecast)
- CloudTrail `cip-management` (multi-region, log validation, 1-year retention)

## After bootstrap
- Confirm the AWS Budgets notification email if prompted.
- Replace the `asolano` access key with short-lived credentials (IAM Identity Center or `aws login`).
