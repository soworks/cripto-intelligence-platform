# ADR-0004: Single AWS account with environment isolation

Status: Accepted (2026-10-03)

## Decision
dev and prod share account 258485600712. Isolation controls:
- Resource prefix `cip-<env>-`, SSM path `/cip/<env>/`, secret path `cip/<env>/`, tag `env`.
- Separate Terraform state keys `env/<env>/terraform.tfstate`.
- CI roles `cip-gha-plan` (PRs, read-only), `cip-gha-dev` (GitHub environment `dev`),
  and `cip-gha-prod` (GitHub environment `prod` with a required reviewer).
- Each deploy role may create workload roles only with its env's permission
  boundary (`cip-<env>-workload-boundary`). The dev boundary denies Secrets
  Manager entirely and denies all prod resources. Both boundaries deny ledger mutation.

## Amendment (2026-10-03): CI roles managed by CLI script
By owner choice, the GitHub OIDC provider and the three `cip-gha-*` roles are managed by
`scripts/bootstrap_github_oidc.sh` (AWS CLI, policies in `iam/github/`), not Terraform.
Terraform bootstrap keeps the state bucket, boundaries, budget and CloudTrail. Role ARNs
are GitHub repository variables (not secrets; an ARN is not a credential).
- Trust: `StringEquals` on `aud = sts.amazonaws.com`, an exact `sub` per role, and
  `repository_id = 1403465152` + `repository_owner_id = 146444006`, so a re-created
  repository with the same name cannot assume them. The repository uses GitHub immutable
  subjects, so `sub` is `repo:soworks@146444006/crypto-intelligence-platform@1403465152:`
  followed by `pull_request`, `environment:dev` or `environment:prod`; the script reads
  this prefix from `GET /repos/{repo}/actions/oidc/customization/sub` (`sub_claim_prefix`).
- `cip-gha-plan` has no AWS managed policy. Its inline `cip-gha-plan-read` policy allows
  only Describe/Get/List on `cip-dev-*` / `cip-prod-*` resources and `/cip/*` parameters,
  `s3:GetObject`/`ListBucket` on the state bucket's `env/dev/` and `env/prod/` prefixes
  (no bootstrap state, no CloudTrail bucket), and `sts:GetCallerIdentity`. PR plans run
  with `-lock=false` because the role cannot write the lock file.
- `scripts/verify_github_oidc.sh` re-checks the boundaries with `iam:SimulatePrincipalPolicy`.

## Amendment (2026-10-03): boundary as a real ceiling, resource-side protection (PR #1 review H1, M3-M7)
Workload boundary (`terraform/bootstrap/boundaries.tf`):
- Allows are scoped to the role's own environment: `table/cip-<env>-*`, `cip-<env>-*` buckets
  (only when `aws:ResourceAccount` is this account), `/cip/<env>/*` parameters (read only),
  `/aws/lambda/cip-<env>-*` log streams, and invoke/publish on `cip-<env>-*`.
- `*` remains only where AWS has no resource scope: X-Ray, `cloudwatch:PutMetricData`,
  Step Functions log delivery (`logs:*LogDelivery`, `PutResourcePolicy`), and
  `bedrock:InvokeModel` (narrowed to model ARNs in Phase 2).
- `kms:Decrypt` requires `kms:ViaService` = SSM, Secrets Manager, DynamoDB or S3.
- Explicit denies:
  - everything on `cip-tfstate-*` and `cip-cloudtrail-*`;
  - flag writes (`PutParameter`, `DeleteParameter*`, `(Un)LabelParameterVersion`) on
    `/cip/*/{execution_mode,trading_enabled,kill_switch}`;
  - ledger item mutation and table-level tampering (see ADR-0005);
  - function URLs.

Deploy roles (`iam/github/deploy-policy.json.tpl`):
- Workload roles live on the IAM path `/cip/<env>/`. `CreateRole`, `PutRolePolicy` and
  `DeleteRolePolicy` require `iam:PermissionsBoundary` = the env boundary. Update, tag,
  delete and `PassRole` are limited to that path. `iam:UpdateAssumeRolePolicy` does not
  support `iam:PermissionsBoundary`, so the path stands in for it: the deploy role can only
  modify roles it created there, and it could only create them with the boundary.
- No managed policies: `AttachRolePolicy` and boundary edits (`CreatePolicyVersion`,
  `SetDefaultPolicyVersion`, `DeletePolicy*`, `Put/DeleteRolePermissionsBoundary`) are
  explicitly denied, and `CreatePolicy` is denied outside `cip-<env>-*`.
- Reads go through the environment-scoped `ManageEnvironmentResources` statement. Only
  `sts:GetCallerIdentity`, `ssm:DescribeParameters`, `logs:DescribeLogGroups` and Step
  Functions log delivery use `*`, so `cip-gha-dev` cannot read `cip-prod-*` configuration
  and vice versa.
- S3 statements require `aws:ResourceAccount` = this account, except `s3:CreateBucket`
  (name-scoped; a bucket can only be created in the caller's account).
- Explicit denies: ledger item writes and `DeleteTable`; function URLs and
  `lambda:AddPermission` for `Principal: *`; `PutBucketPublicAccessBlock`,
  `PutAccountPublicAccessBlock`, `PutBucketAcl`, `PutObjectAcl` and
  `PutBucketOwnershipControls`. `PutBucketPolicy` stays because the TLS-only policies need it.
- `logs:PutResourcePolicy` stays on `*` (Step Functions log delivery; no resource scope).

Plan role: dev only. It has no prod state, parameters or resource reads, and its S3 reads
also require `aws:ResourceAccount`.

Resource side:
- Account-level S3 Block Public Access (all four settings). Before enabling it, the only
  buckets in the account were the state and CloudTrail buckets, and neither was public.
- State bucket policy. Admin principals are `user/asolano` and the account root.
  - `bootstrap/*` is readable and writable only by the admin principals.
  - `env/dev/*` is writable only by `cip-gha-dev` and the admins.
  - `env/prod/*` is readable and writable only by `cip-gha-prod` and the admins.
- CloudTrail bucket: versioning plus Object Lock (GOVERNANCE, 90 days). Deletes,
  governance bypass, retention, policy, versioning, Object Lock and lifecycle changes are
  denied to all non-admin principals. The TLS-only deny is kept.
  GOVERNANCE was chosen over COMPLIANCE on purpose (decision 2026-10-03): COMPLIANCE
  retention cannot be shortened or removed by anyone, including root, until it expires,
  and the bucket could not be deleted for 90 days. In GOVERNANCE mode only the admin
  principals can bypass retention, and the tamper-deny blocks everyone else.
- `scripts/verify_github_oidc.sh` checks four things with the IAM policy simulator:
  - the CI roles;
  - the boundary as a ceiling over an admin identity policy (`simulate-custom-policy`);
  - the live state and CloudTrail bucket policies, per principal ARN;
  - cross-account S3 (`aws:ResourceAccount`).

## Consequences
Lower setup cost than AWS Organizations. Blast radius is limited by IAM, not by an
account boundary. Revisit before M7 if the prod trading secret's risk profile
requires a dedicated account.
