#!/usr/bin/env bash
# Spot-checks the IAM controls with the IAM policy simulator:
#   role      - the live cip-gha-* role policies (simulate-principal-policy)
#   boundary  - the live cip-<env>-workload-boundary as a ceiling over an admin identity
#               policy (simulate-custom-policy with a permissions boundary)
#   bucket    - the live state and CloudTrail bucket policies for a given principal ARN
#               (admin identity policy + resource policy, aws:PrincipalArn in context)
# Exits non-zero if any decision differs from the expected one.
#
# Usage: scripts/verify_github_oidc.sh [--profile soworks]
set -euo pipefail

PROFILE="soworks"
if [[ "${1:-}" == "--profile" ]]; then PROFILE="$2"; fi

ACCOUNT_ID="$(aws --profile "$PROFILE" sts get-caller-identity --query Account --output text)"
IAM="arn:aws:iam::${ACCOUNT_ID}"
STATE_BUCKET="cip-tfstate-${ACCOUNT_ID}"
TRAIL_BUCKET="cip-cloudtrail-${ACCOUNT_ID}"
STATE="arn:aws:s3:::${STATE_BUCKET}"
TRAIL="arn:aws:s3:::${TRAIL_BUCKET}"
DATA="arn:aws:s3:::cip-dev-data-${ACCOUNT_ID}"
FOREIGN_BUCKET="arn:aws:s3:::cip-dev-attacker-bucket"
DDB="arn:aws:dynamodb:us-east-1:${ACCOUNT_ID}:table"
FN="arn:aws:lambda:us-east-1:${ACCOUNT_ID}:function"
SSM="arn:aws:ssm:us-east-1:${ACCOUNT_ID}:parameter"
BUS="arn:aws:events:us-east-1:${ACCOUNT_ID}:event-bus"
DEV_ROLE="${IAM}:role/cip/dev/cip-dev-pipeline-lambda"
DEV_BOUNDARY="${IAM}:policy/cip-dev-workload-boundary"
ADMIN_POLICY="arn:aws:iam::aws:policy/AdministratorAccess"
OWN="aws:ResourceAccount=${ACCOUNT_ID}"
FOREIGN="aws:ResourceAccount=111122223333"
ADMIN_IDENTITY='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"*","Resource":"*"}]}'
failures=0

boundary_doc() {
  local arn="${IAM}:policy/cip-$1-workload-boundary" version
  version="$(aws --profile "$PROFILE" iam get-policy --policy-arn "$arn" --query Policy.DefaultVersionId --output text)"
  aws --profile "$PROFILE" iam get-policy-version --policy-arn "$arn" --version-id "$version" \
    --query PolicyVersion.Document --output json | jq -c .
}
bucket_policy() {
  aws --profile "$PROFILE" s3api get-bucket-policy --bucket "$1" --query Policy --output text
}
BOUNDARY_DEV="$(boundary_doc dev)"
BOUNDARY_PROD="$(boundary_doc prod)"
STATE_POLICY="$(bucket_policy "$STATE_BUCKET")"
TRAIL_POLICY="$(bucket_policy "$TRAIL_BUCKET")"

# Trailing key=value arguments become simulator context entries (bash 3.2 compatible).
CTX=()
set_context() {
  CTX=()
  local kv
  for kv in "$@"; do
    CTX+=("ContextKeyName=${kv%%=*},ContextKeyValues=${kv#*=},ContextKeyType=string")
  done
  if [[ ${#CTX[@]} -gt 0 ]]; then CTX=(--context-entries "${CTX[@]}"); fi
}

report() {
  local expected="$1" decision="$2" subject="$3" action="$4" resource="$5"; shift 5
  local verdict="ok"
  if [[ "$expected" == "allowed" && "$decision" != "allowed" ]] ||
     [[ "$expected" == "denied" && "$decision" == "allowed" ]]; then
    verdict="MISMATCH"
    failures=$((failures + 1))
  fi
  printf '%-8s %-22s %-13s %-36s %s%s\n' "$verdict" "$subject" "$decision" "$action" \
    "${resource#arn:aws:}" "${*:+ [$*]}"
}

# check <expected> <role-name> <action> <resource> [key=value ...]
check() {
  local expected="$1" role="$2" action="$3" resource="$4"; shift 4
  local decision
  set_context "$@"
  decision="$(aws --profile "$PROFILE" iam simulate-principal-policy \
    --policy-source-arn "${IAM}:role/${role}" --action-names "$action" --resource-arns "$resource" \
    ${CTX[@]+"${CTX[@]}"} --query 'EvaluationResults[0].EvalDecision' --output text)"
  report "$expected" "$decision" "$role" "$action" "$resource" "$@"
}

# boundary_check <expected> <env> <action> <resource> [key=value ...]
boundary_check() {
  local expected="$1" env="$2" action="$3" resource="$4"; shift 4
  local decision boundary="$BOUNDARY_DEV"
  [[ "$env" == "prod" ]] && boundary="$BOUNDARY_PROD"
  set_context "$@"
  decision="$(aws --profile "$PROFILE" iam simulate-custom-policy \
    --policy-input-list "$ADMIN_IDENTITY" --permissions-boundary-policy-input-list "$boundary" \
    --action-names "$action" --resource-arns "$resource" \
    ${CTX[@]+"${CTX[@]}"} --query 'EvaluationResults[0].EvalDecision' --output text)"
  report "$expected" "$decision" "boundary:$env" "$action" "$resource" "$@"
}

# bucket_check <expected> <state|trail> <principal-arn> <action> <resource> [key=value ...]
bucket_check() {
  local expected="$1" bucket="$2" principal="$3" action="$4" resource="$5"; shift 5
  local decision policy="$STATE_POLICY"
  [[ "$bucket" == "trail" ]] && policy="$TRAIL_POLICY"
  local transport="aws:SecureTransport=true"
  [[ "$*" == *aws:SecureTransport=* ]] && transport=""
  set_context "aws:PrincipalArn=${principal}" ${transport:+"$transport"} "$@"
  decision="$(aws --profile "$PROFILE" iam simulate-custom-policy \
    --policy-input-list "$ADMIN_IDENTITY" --resource-policy "$policy" \
    --resource-owner "${IAM}:root" --caller-arn "${IAM}:user/asolano" \
    --action-names "$action" --resource-arns "$resource" \
    ${CTX[@]+"${CTX[@]}"} --query 'EvaluationResults[0].EvalDecision' --output text)"
  report "$expected" "$decision" "$bucket:${principal##*[:/]}" "$action" "$resource" "$@"
}

echo "== cip-gha-plan (dev only)"
check denied  cip-gha-plan s3:GetObject "${STATE}/bootstrap/terraform.tfstate" "$OWN"
check denied  cip-gha-plan s3:GetObject "${TRAIL}/AWSLogs/${ACCOUNT_ID}/x.json.gz" "$OWN"
check allowed cip-gha-plan s3:GetObject "${STATE}/env/dev/terraform.tfstate" "$OWN"
check denied  cip-gha-plan s3:GetObject "${STATE}/env/prod/terraform.tfstate" "$OWN"
check denied  cip-gha-plan s3:PutObject "${STATE}/env/dev/terraform.tfstate" "$OWN"
check denied  cip-gha-plan s3:ListBucket "${STATE}" s3:prefix=bootstrap/ "$OWN"
check denied  cip-gha-plan s3:ListBucket "${STATE}" s3:prefix=env/prod/terraform.tfstate "$OWN"
check allowed cip-gha-plan s3:ListBucket "${STATE}" s3:prefix=env/dev/terraform.tfstate "$OWN"
check allowed cip-gha-plan dynamodb:DescribeTable "${DDB}/cip-dev-ledger"
check denied  cip-gha-plan dynamodb:DescribeTable "${DDB}/cip-prod-ledger"
check denied  cip-gha-plan dynamodb:PutItem "${DDB}/cip-dev-ledger"
check allowed cip-gha-plan ssm:GetParameter "${SSM}/cip/dev/trading_enabled"
check denied  cip-gha-plan ssm:GetParameter "${SSM}/cip/prod/trading_enabled"
check denied  cip-gha-plan lambda:GetFunction "${FN}:cip-prod-start-scan"
check allowed cip-gha-plan iam:GetRole "$DEV_ROLE"
check denied  cip-gha-plan iam:GetRole "${IAM}:role/cip-gha-dev"
check denied  cip-gha-plan secretsmanager:GetSecretValue \
  "arn:aws:secretsmanager:us-east-1:${ACCOUNT_ID}:secret:cip/prod/binance"
check denied  cip-gha-plan s3:GetBucketPolicy "$TRAIL" "$OWN"
check allowed cip-gha-plan s3:GetBucketPolicy "$DATA" "$OWN"
check denied  cip-gha-plan s3:GetBucketPolicy "$FOREIGN_BUCKET" "$FOREIGN"
check allowed cip-gha-plan events:DescribeEventBus "${BUS}/default"
check denied  cip-gha-plan events:DescribeEventBus "${BUS}/other"
check denied  cip-gha-plan events:PutEvents "${BUS}/default"

echo "== cip-gha-dev"
check denied  cip-gha-dev dynamodb:PutItem "${DDB}/cip-prod-ledger"
check allowed cip-gha-dev dynamodb:CreateTable "${DDB}/cip-dev-ledger"
check allowed cip-gha-dev dynamodb:UpdateContinuousBackups "${DDB}/cip-dev-ledger"
check allowed cip-gha-dev dynamodb:PutItem "${DDB}/cip-dev-state"
check denied  cip-gha-dev dynamodb:PutItem "${DDB}/cip-dev-ledger"
check denied  cip-gha-dev dynamodb:DeleteItem "${DDB}/cip-dev-ledger"
check denied  cip-gha-dev dynamodb:PartiQLUpdate "${DDB}/cip-dev-ledger"
check denied  cip-gha-dev dynamodb:DeleteTable "${DDB}/cip-dev-ledger"
check denied  cip-gha-dev s3:GetObject "${STATE}/env/prod/terraform.tfstate" "$OWN"
check denied  cip-gha-dev s3:GetObject "${STATE}/bootstrap/terraform.tfstate" "$OWN"
check allowed cip-gha-dev s3:PutObject "${STATE}/env/dev/terraform.tfstate" "$OWN"
check allowed cip-gha-dev s3:CreateBucket "$DATA"
check allowed cip-gha-dev s3:PutObject "${DATA}/x" "$OWN"
check denied  cip-gha-dev s3:PutObject "${FOREIGN_BUCKET}/x" "$FOREIGN"
check allowed cip-gha-dev s3:PutBucketPolicy "$DATA" "$OWN"
check denied  cip-gha-dev s3:PutBucketPublicAccessBlock "$DATA" "$OWN"
check denied  cip-gha-dev s3:PutBucketAcl "$DATA" "$OWN"
check allowed cip-gha-dev iam:CreateRole "$DEV_ROLE" "iam:PermissionsBoundary=${DEV_BOUNDARY}"
check denied  cip-gha-dev iam:CreateRole "$DEV_ROLE"
check denied  cip-gha-dev iam:CreateRole "$DEV_ROLE" "iam:PermissionsBoundary=${IAM}:policy/cip-prod-workload-boundary"
check denied  cip-gha-dev iam:CreateRole "${IAM}:role/cip-dev-pipeline-lambda" "iam:PermissionsBoundary=${DEV_BOUNDARY}"
check denied  cip-gha-dev iam:CreateRole "${IAM}:role/cip/prod/cip-prod-pipeline-lambda" "iam:PermissionsBoundary=${DEV_BOUNDARY}"
check denied  cip-gha-dev iam:AttachRolePolicy "$DEV_ROLE" "iam:PermissionsBoundary=${DEV_BOUNDARY}" "iam:PolicyARN=${ADMIN_POLICY}"
check allowed cip-gha-dev iam:UpdateAssumeRolePolicy "$DEV_ROLE"
check denied  cip-gha-dev iam:UpdateAssumeRolePolicy "${IAM}:role/cip-dev-unbounded"
check denied  cip-gha-dev iam:UpdateAssumeRolePolicy "${IAM}:role/cip-gha-prod"
check allowed cip-gha-dev iam:PassRole "$DEV_ROLE" iam:PassedToService=lambda.amazonaws.com
check denied  cip-gha-dev iam:PassRole "$DEV_ROLE" iam:PassedToService=ec2.amazonaws.com
check denied  cip-gha-dev iam:DeleteRolePermissionsBoundary "$DEV_ROLE"
check denied  cip-gha-dev iam:PutRolePolicy "${IAM}:role/cip-gha-dev"
check denied  cip-gha-dev iam:CreatePolicy "${IAM}:policy/escalate"
check denied  cip-gha-dev iam:CreatePolicyVersion "$DEV_BOUNDARY"
check denied  cip-gha-dev iam:SetDefaultPolicyVersion "$DEV_BOUNDARY"
check allowed cip-gha-dev lambda:GetFunction "${FN}:cip-dev-start-scan"
check denied  cip-gha-dev lambda:GetFunction "${FN}:cip-prod-start-scan"
check denied  cip-gha-dev states:DescribeExecution \
  "arn:aws:states:us-east-1:${ACCOUNT_ID}:execution:cip-prod-scan-pipeline:x"
check denied  cip-gha-dev scheduler:GetSchedule \
  "arn:aws:scheduler:us-east-1:${ACCOUNT_ID}:schedule/default/cip-prod-scan"
check denied  cip-gha-dev lambda:CreateFunctionUrlConfig "${FN}:cip-dev-start-scan"
check denied  cip-gha-dev lambda:AddPermission "${FN}:cip-dev-start-scan" "lambda:Principal=*"
check allowed cip-gha-dev events:DescribeEventBus "${BUS}/default"
check denied  cip-gha-dev events:PutRule "${BUS}/default"

echo "== cip-gha-prod"
check denied  cip-gha-prod lambda:UpdateFunctionCode "${FN}:cip-dev-start-scan"
check allowed cip-gha-prod lambda:UpdateFunctionCode "${FN}:cip-prod-start-scan"
check denied  cip-gha-prod lambda:GetFunction "${FN}:cip-dev-start-scan"
check denied  cip-gha-prod dynamodb:DeleteItem "${DDB}/cip-prod-ledger"
check denied  cip-gha-prod s3:GetObject "${STATE}/env/dev/terraform.tfstate" "$OWN"

echo "== workload boundary as a ceiling over an admin identity policy"
boundary_check denied  dev s3:PutObject "${STATE}/env/prod/terraform.tfstate" "$OWN"
boundary_check denied  dev s3:PutObject "${STATE}/env/dev/terraform.tfstate" "$OWN"
boundary_check denied  dev s3:GetObject "${STATE}/bootstrap/terraform.tfstate" "$OWN"
boundary_check denied  dev s3:DeleteObject "${TRAIL}/AWSLogs/${ACCOUNT_ID}/x.json.gz" "$OWN"
boundary_check denied  dev s3:PutBucketPolicy "$TRAIL" "$OWN"
boundary_check allowed dev s3:GetObject "${DATA}/snapshots/x" "$OWN"
boundary_check denied  dev s3:PutObject "${FOREIGN_BUCKET}/x" "$FOREIGN"
boundary_check denied  dev s3:PutBucketPolicy "$DATA" "$OWN"
boundary_check allowed dev ssm:GetParameter "${SSM}/cip/dev/kill_switch"
boundary_check denied  dev ssm:PutParameter "${SSM}/cip/dev/trading_enabled"
boundary_check denied  dev ssm:PutParameter "${SSM}/cip/dev/execution_mode"
boundary_check denied  dev ssm:PutParameter "${SSM}/cip/dev/kill_switch"
boundary_check denied  dev ssm:DeleteParameter "${SSM}/cip/dev/kill_switch"
boundary_check allowed dev dynamodb:PutItem "${DDB}/cip-dev-ledger"
boundary_check denied  dev dynamodb:UpdateItem "${DDB}/cip-dev-ledger"
boundary_check denied  dev dynamodb:DeleteTable "${DDB}/cip-dev-ledger"
boundary_check denied  dev dynamodb:UpdateTimeToLive "${DDB}/cip-dev-ledger"
boundary_check denied  dev dynamodb:UpdateContinuousBackups "${DDB}/cip-dev-ledger"
boundary_check denied  dev dynamodb:RestoreTableToPointInTime "${DDB}/cip-dev-ledger"
boundary_check denied  dev dynamodb:PutItem "${DDB}/cip-prod-ledger"
boundary_check denied  dev iam:AttachRolePolicy "$DEV_ROLE" "iam:PolicyARN=${ADMIN_POLICY}"
boundary_check denied  dev iam:UpdateAssumeRolePolicy "${IAM}:role/cip-dev-unbounded"
boundary_check denied  dev iam:CreateRole "$DEV_ROLE"
boundary_check denied  dev lambda:CreateFunctionUrlConfig "${FN}:cip-dev-start-scan"
boundary_check allowed dev lambda:InvokeFunction "${FN}:cip-dev-start-scan"
boundary_check allowed dev xray:PutTraceSegments "*"
boundary_check allowed dev kms:Decrypt "*" kms:ViaService=ssm.us-east-1.amazonaws.com
boundary_check denied  dev kms:Decrypt "*"
boundary_check denied  dev secretsmanager:GetSecretValue \
  "arn:aws:secretsmanager:us-east-1:${ACCOUNT_ID}:secret:cip/prod/binance"
boundary_check allowed prod secretsmanager:GetSecretValue \
  "arn:aws:secretsmanager:us-east-1:${ACCOUNT_ID}:secret:cip/prod/binance"
boundary_check denied  prod ssm:PutParameter "${SSM}/cip/prod/trading_enabled"
boundary_check denied  prod dynamodb:DeleteItem "${DDB}/cip-prod-ledger"

echo "== state bucket policy"
bucket_check allowed state "${IAM}:user/asolano" s3:GetObject "${STATE}/bootstrap/terraform.tfstate"
bucket_check allowed state "${IAM}:user/asolano" s3:PutObject "${STATE}/bootstrap/terraform.tfstate"
bucket_check allowed state "${IAM}:root" s3:PutObject "${STATE}/bootstrap/terraform.tfstate"
bucket_check denied  state "${IAM}:role/cip-gha-dev" s3:GetObject "${STATE}/bootstrap/terraform.tfstate"
bucket_check allowed state "${IAM}:role/cip-gha-dev" s3:PutObject "${STATE}/env/dev/terraform.tfstate"
bucket_check denied  state "${IAM}:role/cip-gha-dev" s3:PutObject "${STATE}/env/prod/terraform.tfstate"
bucket_check denied  state "${IAM}:role/cip-gha-dev" s3:GetObject "${STATE}/env/prod/terraform.tfstate"
bucket_check denied  state "${IAM}:role/cip-gha-plan" s3:PutObject "${STATE}/env/dev/terraform.tfstate"
bucket_check allowed state "${IAM}:role/cip-gha-plan" s3:GetObject "${STATE}/env/dev/terraform.tfstate"
bucket_check allowed state "${IAM}:role/cip-gha-prod" s3:PutObject "${STATE}/env/prod/terraform.tfstate"
bucket_check denied  state "${IAM}:role/cip-gha-prod" s3:PutObject "${STATE}/env/dev/terraform.tfstate"
bucket_check denied  state "${IAM}:user/asolano" s3:GetObject "${STATE}/env/dev/terraform.tfstate" \
  aws:SecureTransport=false

echo "== CloudTrail bucket policy"
bucket_check denied  trail "${IAM}:role/cip-gha-dev" s3:DeleteObject "${TRAIL}/AWSLogs/${ACCOUNT_ID}/x.json.gz"
bucket_check denied  trail "${IAM}:role/cip-gha-dev" s3:PutBucketPolicy "$TRAIL"
bucket_check denied  trail "${IAM}:role/cip-gha-dev" s3:BypassGovernanceRetention "${TRAIL}/AWSLogs/${ACCOUNT_ID}/x.json.gz"
bucket_check allowed trail "${IAM}:user/asolano" s3:PutBucketPolicy "$TRAIL"
bucket_check allowed trail "${IAM}:root" s3:DeleteObject "${TRAIL}/AWSLogs/${ACCOUNT_ID}/x.json.gz"

if [[ "$failures" -gt 0 ]]; then
  echo "$failures check(s) did not match" >&2
  exit 1
fi
echo "all checks matched"
