locals {
  other_env = { dev = "prod", prod = "dev" }
}

data "aws_iam_policy_document" "workload_boundary" {
  #checkov:skip=CKV_AWS_355:Permission boundary is a ceiling; grants are scoped in role policies
  #checkov:skip=CKV_AWS_356:Permission boundary is a ceiling; grants are scoped in role policies
  #checkov:skip=CKV_AWS_288:Permission boundary is a ceiling; grants are scoped in role policies
  #checkov:skip=CKV_AWS_289:Permission boundary is a ceiling; grants are scoped in role policies
  #checkov:skip=CKV_AWS_290:Permission boundary is a ceiling; grants are scoped in role policies
  for_each = local.environments

  statement {
    sid = "AllowWorkloadServices"
    actions = [
      "dynamodb:*",
      "s3:*",
      "ssm:GetParameter",
      "ssm:GetParameters",
      "ssm:PutParameter",
      "logs:*",
      "cloudwatch:PutMetricData",
      "xray:PutTraceSegments",
      "xray:PutTelemetryRecords",
      "lambda:InvokeFunction",
      "states:StartExecution",
      "sns:Publish",
      "bedrock:InvokeModel",
      "kms:Decrypt",
    ]
    resources = ["*"]
  }

  dynamic "statement" {
    for_each = each.key == "prod" ? [1] : []
    content {
      sid       = "AllowProdSecrets"
      actions   = ["secretsmanager:GetSecretValue"]
      resources = ["arn:aws:secretsmanager:*:${local.account_id}:secret:cip/prod/*"]
    }
  }

  statement {
    sid     = "DenyOtherEnvironment"
    effect  = "Deny"
    actions = ["*"]
    resources = [
      "arn:aws:dynamodb:*:${local.account_id}:table/cip-${local.other_env[each.key]}-*",
      "arn:aws:s3:::cip-${local.other_env[each.key]}-*",
      "arn:aws:ssm:*:${local.account_id}:parameter/cip/${local.other_env[each.key]}/*",
      "arn:aws:secretsmanager:*:${local.account_id}:secret:cip/${local.other_env[each.key]}/*",
      "arn:aws:lambda:*:${local.account_id}:function:cip-${local.other_env[each.key]}-*",
      "arn:aws:states:*:${local.account_id}:stateMachine:cip-${local.other_env[each.key]}-*",
    ]
  }

  statement {
    sid    = "DenyLedgerMutation"
    effect = "Deny"
    actions = [
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:BatchWriteItem",
      "dynamodb:PartiQLUpdate",
      "dynamodb:PartiQLDelete",
    ]
    resources = ["arn:aws:dynamodb:*:${local.account_id}:table/cip-${each.key}-ledger"]
  }
}

resource "aws_iam_policy" "workload_boundary" {
  for_each = local.environments
  name     = "cip-${each.key}-workload-boundary"
  policy   = data.aws_iam_policy_document.workload_boundary[each.key].json
}
