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

## Consequences
Lower setup cost than AWS Organizations. Blast radius is limited by IAM, not by an
account boundary. Revisit before M7 if the prod trading secret's risk profile
requires a dedicated account.
