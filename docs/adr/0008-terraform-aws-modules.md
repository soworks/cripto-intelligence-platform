# ADR-0008: Use terraform-aws-modules instead of local modules

Status: Accepted (2026-10-03)

## Context
Phase 1 shipped four local modules (`platform-data`, `runtime-flags`, `alerts`,
`scan-pipeline`). They are thin wrappers that we would have to maintain, test and keep
current with the AWS provider. The community
[terraform-aws-modules](https://github.com/terraform-aws-modules) cover every resource
type we use except CloudTrail, budgets and the permission boundaries.

## Decision
Root configurations (`terraform/bootstrap`, `terraform/environments/<env>`) call registry
modules directly. There are no local modules. Every call pins an exact version
(`version = "x.y.z"`), the latest compatible with AWS provider `~> 6.67` and Terraform
`~> 1.16`. Upgrades are explicit PRs.

| Module | Version | Used for |
|---|---|---|
| `terraform-aws-modules/s3-bucket/aws` | 5.16.1 | CloudTrail bucket (bootstrap), data bucket |
| `terraform-aws-modules/dynamodb-table/aws` | 5.5.2 | ledger, state, counters tables |
| `terraform-aws-modules/ssm-parameter/aws` | 2.1.2 | execution flags |
| `terraform-aws-modules/sns/aws` | 7.2.0 | alerts topic and email subscription |
| `terraform-aws-modules/iam/aws//modules/iam-role` | 6.8.2 | pipeline Lambda, Step Functions, scheduler roles |
| `terraform-aws-modules/lambda/aws` | 8.9.0 | scan pipeline functions and their log groups |
| `terraform-aws-modules/step-functions/aws` | 5.1.1 | scan state machine and its log group |
| `terraform-aws-modules/eventbridge/aws` | 4.3.2 | hourly EventBridge Scheduler schedule |
| `terraform-aws-modules/cloudwatch/aws//modules/metric-alarm` | 5.7.3 | pipeline failed / missed alarms |

Module settings that keep the security posture:
- Workload roles: `use_name_prefix = false` (exact `cip-<env>-*` names) and
  `permissions_boundary = cip-<env>-workload-boundary`. Each role has one inline policy
  named after the role.
- No module creates IAM roles or managed policies. The Lambda, Step Functions and
  EventBridge modules use `create_role = false` / `use_existing_role = true`. Their
  built-in roles attach managed policies, which the deploy role cannot create.
- Lambda: `create_package = false` with `local_existing_package` pointing to
  `build/cip-lambda.zip`. The module never builds packages; `scripts/build_lambda.sh`
  stays the only build path.
- S3: `attach_deny_insecure_transport_policy = true` on every bucket. The CloudTrail
  bucket keeps its own policy with `aws:SourceArn` conditions, because the module's
  `attach_cloudtrail_log_delivery_policy` has no source condition and allows writes
  to `AWSLogs/*` for any account.
- After the PR #1 review (ADR-0004 amendment): workload roles use `path = "/cip/<env>/"`.
  The data bucket sets `attach_public_policy = false` and relies on account-level S3 Block
  Public Access, because the deploy roles may not change public access settings.
- EventBridge: `create_bus = false`, `append_schedule_postfix = false`,
  `group_name = "default"`, so schedules stay `schedule/default/cip-<env>-*`.

## Remaining plain resources
- `aws_s3_bucket.tf_state` and its configuration: `prevent_destroy` cannot be set on
  a resource inside a module. The state bucket is the one resource that must never be
  deleted by a plan.
- `aws_iam_policy.workload_boundary`: the boundaries are the control that limits every
  CI-created role. They stay as reviewed, plain policy documents.
- `aws_budgets_budget.monthly`: there is no terraform-aws-modules budget module.
- `aws_cloudtrail.management`: there is no terraform-aws-modules CloudTrail module.

## Consequences
- `.checkov.yaml` sets `download-external-modules: true`, so checkov scans the
  resources inside the modules. Where checkov cannot evaluate a module's dynamic blocks,
  a `#checkov:skip` with a reason sits on the module call. `CKV_TF_1` (commit-hash
  sources) is skipped globally because registry sources are pinned by exact version.
- The EventBridge module always reads the target bus (`data "aws_cloudwatch_event_bus"`)
  when it doesn't create one. The plan and deploy CI roles therefore have
  `events:DescribeEventBus` on `event-bus/default` only.
- The DynamoDB module still sets the deprecated GSI `hash_key` / `range_key`, which
  produces provider deprecation warnings until upstream moves to `key_schema`.
- A future prod root is a copy of `environments/dev` with different locals. If the
  duplication grows, a thin composition module can be reintroduced.
- The GitHub OIDC provider and the `cip-gha-*` roles stay on
  `scripts/bootstrap_github_oidc.sh` (ADR-0004 amendment). The iam module's
  `iam-oidc-provider` and `iam-role` (`enable_github_oidc`) submodules are a possible future
  replacement, but we are not switching now.
