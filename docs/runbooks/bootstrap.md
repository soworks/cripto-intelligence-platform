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

Also creates:
- Account-level S3 Block Public Access (all four settings).
- State bucket policy: `bootstrap/*` is limited to the admin principals; `env/dev/*` writes
  are limited to `cip-gha-dev` and the admins; `env/prod/*` reads and writes are limited to
  `cip-gha-prod` and the admins. Admin principals are the account root plus
  `admin_user_names` (default `asolano`) plus `extra_admin_principal_arns`.
- CloudTrail bucket versioning and Object Lock (GOVERNANCE, 90 days). Deleting objects,
  bypassing governance, and changing the policy, versioning, lock or lifecycle are denied
  to non-admin principals.

**Lockout warning.** Before switching the owner's credentials to IAM Identity Center or
any other principal, add the new principal to `extra_admin_principal_arns` (for example
`arn:aws:iam::258485600712:role/aws-reserved/sso.amazonaws.com/*/AWSReservedSSO_AdministratorAccess_*`)
and apply with the old credentials. Otherwise the new principal cannot read bootstrap state.
Recovery: sign in as the account root, delete the `cip-tfstate-258485600712` bucket policy,
then re-apply bootstrap. The root user can always delete a bucket policy.

To enable Object Lock on an existing bucket, versioning has to be enabled first. That needs
two applies: one with `object_lock_enabled = false` (versioning), then one with `true`.

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

`verify_github_oidc.sh` runs about 120 IAM policy simulator checks and exits non-zero on any
mismatch:
- CI roles (`simulate-principal-policy`). The plan role is dev-only. The dev role cannot
  read `cip-prod-*`, write ledger items, attach managed policies, create roles off
  `/cip/dev/` or without the boundary, or create function URLs.
- The live boundaries as a ceiling over an admin identity policy
  (`simulate-custom-policy --permissions-boundary-policy-input-list`): state, CloudTrail,
  flag writes, ledger tampering, cross-account S3 and IAM are all denied.
- The live state and CloudTrail bucket policies, per principal ARN (`aws:PrincipalArn` in
  context).

It needs only the admin profile; it changes nothing.

The plan and deploy roles have `events:DescribeEventBus` on `event-bus/default` only. The
eventbridge module reads the default bus even though it only manages schedules (ADR-0008).

The deploy roles have `states:ValidateStateMachineDefinition` on `stateMachine:*` in this
account and region. The AWS provider validates the definition before the state machine
exists, so the request resource is the literal `stateMachine:*` and a `cip-<env>-*` pattern
cannot match it. The action only validates JSON and changes nothing. The IAM simulator
cannot evaluate it, so `verify_github_oidc.sh` does not check it; the first dev deploy
(run 37149792669, attempt 2) proved it.

The OIDC roles stay on this script. The iam module's `iam-oidc-provider` and GitHub OIDC
`iam-role` submodules are a possible future replacement (ADR-0008); we are not switching now.

## After bootstrap
- Confirm the AWS Budgets notification email if prompted.
- Replace the `asolano` access key with short-lived credentials (IAM Identity Center or `aws login`).
