#!/usr/bin/env bash
# Creates or updates the GitHub Actions OIDC provider and the cip-gha-{plan,dev,prod} roles.
# Safe to rerun: every step converges the account to the policies under iam/github/.
#
# Usage: scripts/bootstrap_github_oidc.sh [--profile soworks] [--region us-east-1]
#                                         [--account 258485600712] [--dry-run]
# Repository ID pinning uses `gh api` when gh is authenticated, or the
# GITHUB_REPOSITORY_ID / GITHUB_REPOSITORY_OWNER_ID environment variables.
set -euo pipefail

PROFILE="soworks"
REGION="us-east-1"
EXPECTED_ACCOUNT="258485600712"
REPO="soworks/cripto-intelligence-platform"
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --account) EXPECTED_ACCOUNT="$2"; shift 2 ;;
    --repo) REPO="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
POLICY_DIR="$ROOT/iam/github"
PROVIDER_HOST="token.actions.githubusercontent.com"
AUDIENCE="sts.amazonaws.com"
MAX_SESSION_SECONDS=3600
TAGS=(Key=project,Value=cip Key=managed_by,Value=scripts/bootstrap_github_oidc.sh)

awscli() { aws --profile "$PROFILE" --region "$REGION" --output json "$@"; }
log() { printf '==> %s\n' "$*" >&2; }

ACCOUNT_ID="$(awscli sts get-caller-identity --query Account --output text)"
if [[ "$ACCOUNT_ID" != "$EXPECTED_ACCOUNT" ]]; then
  echo "profile $PROFILE resolves to account $ACCOUNT_ID, expected $EXPECTED_ACCOUNT" >&2
  exit 1
fi
PROVIDER_ARN="arn:aws:iam::${ACCOUNT_ID}:oidc-provider/${PROVIDER_HOST}"

REPO_ID="${GITHUB_REPOSITORY_ID:-}"
OWNER_ID="${GITHUB_REPOSITORY_OWNER_ID:-}"
if [[ -z "$REPO_ID" || -z "$OWNER_ID" ]] && command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
  read -r REPO_ID OWNER_ID < <(gh api "repos/$REPO" --jq '"\(.id) \(.owner.id)"')
fi
if [[ -n "$REPO_ID" && ! "$REPO_ID" =~ ^[0-9]+$ ]] || [[ -n "$OWNER_ID" && ! "$OWNER_ID" =~ ^[0-9]+$ ]]; then
  echo "repository_id/repository_owner_id must be numeric (got '$REPO_ID'/'$OWNER_ID')" >&2
  exit 1
fi
if [[ -z "$REPO_ID" || -z "$OWNER_ID" ]]; then
  REPO_ID="" OWNER_ID=""
  log "WARNING: repository_id pinning NOT applied (gh not authenticated and IDs not provided)."
  log "WARNING: run 'gh auth login' and rerun this script to pin $REPO by immutable ID."
fi

render() {
  sed -e "s/\${ACCOUNT_ID}/${ACCOUNT_ID}/g" \
      -e "s/\${REGION}/${REGION}/g" \
      -e "s/\${ENV}/${2:-}/g" "$1" | jq -c .
}

trust_policy() {
  jq -nc \
    --arg provider "$PROVIDER_ARN" --arg host "$PROVIDER_HOST" --arg aud "$AUDIENCE" \
    --arg sub "repo:${REPO}:$1" --arg repo_id "$REPO_ID" --arg owner_id "$OWNER_ID" '
    {
      Version: "2012-10-17",
      Statement: [{
        Effect: "Allow",
        Principal: {Federated: $provider},
        Action: "sts:AssumeRoleWithWebIdentity",
        Condition: {
          StringEquals: (
            {("\($host):aud"): $aud, ("\($host):sub"): $sub}
            + (if $repo_id == "" then {} else
                {("\($host):repository_id"): $repo_id,
                 ("\($host):repository_owner_id"): $owner_id} end)
          )
        }
      }]
    }'
}

ensure_provider() {
  if awscli iam get-open-id-connect-provider --open-id-connect-provider-arn "$PROVIDER_ARN" \
      > /tmp/cip-oidc-provider.json 2>/dev/null; then
    if ! jq -e --arg aud "$AUDIENCE" '.ClientIDList | index($aud)' /tmp/cip-oidc-provider.json >/dev/null; then
      log "adding audience $AUDIENCE to $PROVIDER_ARN"
      awscli iam add-client-id-to-open-id-connect-provider \
        --open-id-connect-provider-arn "$PROVIDER_ARN" --client-id "$AUDIENCE"
    fi
    awscli iam tag-open-id-connect-provider --open-id-connect-provider-arn "$PROVIDER_ARN" --tags "${TAGS[@]}"
    for key in $(awscli iam list-open-id-connect-provider-tags --open-id-connect-provider-arn "$PROVIDER_ARN" \
        --query "Tags[?Key!='project' && Key!='managed_by'].Key" --output text); do
      log "removing stray tag $key from OIDC provider"
      awscli iam untag-open-id-connect-provider --open-id-connect-provider-arn "$PROVIDER_ARN" --tag-keys "$key"
    done
    log "OIDC provider present: $PROVIDER_ARN"
  else
    log "creating OIDC provider $PROVIDER_ARN"
    awscli iam create-open-id-connect-provider --url "https://${PROVIDER_HOST}" \
      --client-id-list "$AUDIENCE" --tags "${TAGS[@]}" >/dev/null
  fi
}

ensure_role() {
  local role="$1" description="$2" trust="$3" policy_name="$4" policy="$5"

  if awscli iam get-role --role-name "$role" >/dev/null 2>&1; then
    log "updating role $role"
    awscli iam update-assume-role-policy --role-name "$role" --policy-document "$trust"
    awscli iam update-role --role-name "$role" --description "$description" \
      --max-session-duration "$MAX_SESSION_SECONDS"
  else
    log "creating role $role"
    awscli iam create-role --role-name "$role" --description "$description" \
      --assume-role-policy-document "$trust" --max-session-duration "$MAX_SESSION_SECONDS" >/dev/null
  fi
  awscli iam tag-role --role-name "$role" --tags "${TAGS[@]}"
  for key in $(awscli iam list-role-tags --role-name "$role" \
      --query "Tags[?Key!='project' && Key!='managed_by'].Key" --output text); do
    log "removing stray tag $key from $role"
    awscli iam untag-role --role-name "$role" --tag-keys "$key"
  done

  for arn in $(awscli iam list-attached-role-policies --role-name "$role" \
      --query 'AttachedPolicies[].PolicyArn' --output text); do
    log "detaching managed policy $arn from $role"
    awscli iam detach-role-policy --role-name "$role" --policy-arn "$arn"
  done
  for name in $(awscli iam list-role-policies --role-name "$role" --query 'PolicyNames[]' --output text); do
    if [[ "$name" != "$policy_name" ]]; then
      log "deleting unexpected inline policy $name from $role"
      awscli iam delete-role-policy --role-name "$role" --policy-name "$name"
    fi
  done
  awscli iam put-role-policy --role-name "$role" --policy-name "$policy_name" --policy-document "$policy"
}

PLAN_POLICY="$(render "$POLICY_DIR/plan-policy.json.tpl")"
DEV_POLICY="$(render "$POLICY_DIR/deploy-policy.json.tpl" dev)"
PROD_POLICY="$(render "$POLICY_DIR/deploy-policy.json.tpl" prod)"
PLAN_TRUST="$(trust_policy pull_request)"
DEV_TRUST="$(trust_policy environment:dev)"
PROD_TRUST="$(trust_policy environment:prod)"

if [[ "$DRY_RUN" == true ]]; then
  jq -n --argjson plan_trust "$PLAN_TRUST" --argjson dev_trust "$DEV_TRUST" \
    --argjson prod_trust "$PROD_TRUST" --argjson plan "$PLAN_POLICY" \
    --argjson dev "$DEV_POLICY" --argjson prod "$PROD_POLICY" \
    '{trust: {plan: $plan_trust, dev: $dev_trust, prod: $prod_trust},
      policies: {plan: $plan, dev: $dev, prod: $prod}}'
  exit 0
fi

ensure_provider
ensure_role cip-gha-plan "CIP GitHub Actions: pull request terraform plan (read-only)" \
  "$PLAN_TRUST" cip-gha-plan-read "$PLAN_POLICY"
ensure_role cip-gha-dev "CIP GitHub Actions: deploy dev environment" \
  "$DEV_TRUST" cip-dev-deploy "$DEV_POLICY"
ensure_role cip-gha-prod "CIP GitHub Actions: deploy prod environment" \
  "$PROD_TRUST" cip-prod-deploy "$PROD_POLICY"

for env in plan dev prod; do
  upper="$(echo "$env" | tr '[:lower:]' '[:upper:]')"
  echo "AWS_ROLE_${upper}=$(awscli iam get-role --role-name "cip-gha-$env" --query Role.Arn --output text)"
done
