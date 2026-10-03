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
  subjects, so `sub` is `repo:soworks@146444006/cripto-intelligence-platform@1403465152:`
  followed by `pull_request`, `environment:dev` or `environment:prod`; the script reads
  this prefix from `GET /repos/{repo}/actions/oidc/customization/sub` (`sub_claim_prefix`).
- `cip-gha-plan` has no AWS managed policy. Its inline `cip-gha-plan-read` policy allows
  only Describe/Get/List on `cip-dev-*` / `cip-prod-*` resources and `/cip/*` parameters,
  `s3:GetObject`/`ListBucket` on the state bucket's `env/dev/` and `env/prod/` prefixes
  (no bootstrap state, no CloudTrail bucket), and `sts:GetCallerIdentity`. PR plans run
  with `-lock=false` because the role cannot write the lock file.
- `scripts/verify_github_oidc.sh` re-checks the boundaries with `iam:SimulatePrincipalPolicy`.

## Consequences
Lower setup cost than AWS Organizations. Blast radius is limited by IAM, not by an
account boundary. Revisit before M7 if the prod trading secret's risk profile
requires a dedicated account.
