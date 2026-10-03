#!/usr/bin/env bash
# Spot-checks the cip-gha-* role policies with iam:SimulatePrincipalPolicy.
# Exits non-zero if any decision differs from the expected one.
#
# Usage: scripts/verify_github_oidc.sh [--profile soworks]
set -euo pipefail

PROFILE="soworks"
if [[ "${1:-}" == "--profile" ]]; then PROFILE="$2"; fi

ACCOUNT_ID="$(aws --profile "$PROFILE" sts get-caller-identity --query Account --output text)"
IAM="arn:aws:iam::${ACCOUNT_ID}"
STATE="arn:aws:s3:::cip-tfstate-${ACCOUNT_ID}"
DDB="arn:aws:dynamodb:us-east-1:${ACCOUNT_ID}:table"
FN="arn:aws:lambda:us-east-1:${ACCOUNT_ID}:function"
BUS="arn:aws:events:us-east-1:${ACCOUNT_ID}:event-bus"
failures=0

check() {
  local expected="$1" role="$2" action="$3" resource="$4" context_key="${5:-}" context_value="${6:-}"
  local args=(--policy-source-arn "${IAM}:role/${role}" --action-names "$action" --resource-arns "$resource")
  if [[ -n "$context_key" ]]; then
    args+=(--context-entries "ContextKeyName=${context_key},ContextKeyValues=${context_value},ContextKeyType=string")
  fi
  local decision
  decision="$(aws --profile "$PROFILE" iam simulate-principal-policy "${args[@]}" \
    --query 'EvaluationResults[0].EvalDecision' --output text)"
  local verdict="ok"
  if [[ "$expected" == "allowed" && "$decision" != "allowed" ]] ||
     [[ "$expected" == "denied" && "$decision" == "allowed" ]]; then
    verdict="MISMATCH"
    failures=$((failures + 1))
  fi
  printf '%-8s %-13s %-14s %-34s %s%s\n' "$verdict" "$role" "$decision" "$action" "${resource#arn:aws:}" \
    "${context_key:+ [$context_key=$context_value]}"
}

check denied  cip-gha-plan s3:GetObject "${STATE}/bootstrap/terraform.tfstate"
check denied  cip-gha-plan s3:GetObject "arn:aws:s3:::cip-cloudtrail-${ACCOUNT_ID}/AWSLogs/${ACCOUNT_ID}/x.json.gz"
check allowed cip-gha-plan s3:GetObject "${STATE}/env/dev/terraform.tfstate"
check allowed cip-gha-plan s3:GetObject "${STATE}/env/prod/terraform.tfstate"
check denied  cip-gha-plan s3:PutObject "${STATE}/env/dev/terraform.tfstate"
check denied  cip-gha-plan s3:ListBucket "${STATE}" s3:prefix bootstrap/
check allowed cip-gha-plan s3:ListBucket "${STATE}" s3:prefix env/dev/terraform.tfstate
check allowed cip-gha-plan dynamodb:DescribeTable "${DDB}/cip-prod-ledger"
check denied  cip-gha-plan dynamodb:PutItem "${DDB}/cip-dev-ledger"
check denied  cip-gha-plan dynamodb:Scan "${DDB}/cip-prod-ledger"
check denied  cip-gha-plan iam:GetRole "${IAM}:role/cip-gha-dev"
check denied  cip-gha-plan secretsmanager:GetSecretValue \
  "arn:aws:secretsmanager:us-east-1:${ACCOUNT_ID}:secret:cip/prod/binance"
check denied  cip-gha-plan s3:GetBucketPolicy "arn:aws:s3:::cip-cloudtrail-${ACCOUNT_ID}"
check allowed cip-gha-plan events:DescribeEventBus "${BUS}/default"
check denied  cip-gha-plan events:DescribeEventBus "${BUS}/other"
check denied  cip-gha-plan events:PutEvents "${BUS}/default"

check denied  cip-gha-dev dynamodb:PutItem "${DDB}/cip-prod-ledger"
check allowed cip-gha-dev dynamodb:CreateTable "${DDB}/cip-dev-ledger"
check denied  cip-gha-dev s3:GetObject "${STATE}/env/prod/terraform.tfstate"
check denied  cip-gha-dev s3:GetObject "${STATE}/bootstrap/terraform.tfstate"
check allowed cip-gha-dev iam:CreateRole "${IAM}:role/cip-dev-pipeline-lambda" \
  iam:PermissionsBoundary "${IAM}:policy/cip-dev-workload-boundary"
check denied  cip-gha-dev iam:CreateRole "${IAM}:role/cip-dev-pipeline-lambda"
check denied  cip-gha-dev iam:CreateRole "${IAM}:role/cip-dev-pipeline-lambda" \
  iam:PermissionsBoundary "${IAM}:policy/cip-prod-workload-boundary"
check denied  cip-gha-dev iam:CreateRole "${IAM}:role/cip-prod-pipeline-lambda" \
  iam:PermissionsBoundary "${IAM}:policy/cip-dev-workload-boundary"
check allowed cip-gha-dev iam:PassRole "${IAM}:role/cip-dev-pipeline-lambda" \
  iam:PassedToService lambda.amazonaws.com
check denied  cip-gha-dev iam:PassRole "${IAM}:role/cip-dev-pipeline-lambda" \
  iam:PassedToService ec2.amazonaws.com
check denied  cip-gha-dev iam:DeleteRolePermissionsBoundary "${IAM}:role/cip-dev-pipeline-lambda"
check denied  cip-gha-dev iam:UpdateAssumeRolePolicy "${IAM}:role/cip-gha-prod"
check denied  cip-gha-dev iam:PutRolePolicy "${IAM}:role/cip-gha-dev"
check allowed cip-gha-dev events:DescribeEventBus "${BUS}/default"
check denied  cip-gha-dev events:PutRule "${BUS}/default"

check denied  cip-gha-prod lambda:UpdateFunctionCode "${FN}:cip-dev-start-scan"
check allowed cip-gha-prod lambda:UpdateFunctionCode "${FN}:cip-prod-start-scan"
check denied  cip-gha-prod s3:GetObject "${STATE}/env/dev/terraform.tfstate"

if [[ "$failures" -gt 0 ]]; then
  echo "$failures check(s) did not match" >&2
  exit 1
fi
echo "all checks matched"
