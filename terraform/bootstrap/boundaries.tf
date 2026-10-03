locals {
  other_env  = { dev = "prod", prod = "dev" }
  flag_names = ["execution_mode", "trading_enabled", "kill_switch"]
}

data "aws_iam_policy_document" "workload_boundary" {
  #checkov:skip=CKV_AWS_355:Only X-Ray, metrics, Bedrock, KMS-via-service and log delivery use "*"; they have no resource-level scope
  #checkov:skip=CKV_AWS_356:Only X-Ray, metrics, Bedrock, KMS-via-service and log delivery use "*"; they have no resource-level scope
  #checkov:skip=CKV_AWS_111:logs:PutResourcePolicy (Step Functions log delivery) has no resource-level scope
  for_each = local.environments

  statement {
    sid = "AllowEnvironmentTables"
    actions = [
      "dynamodb:GetItem", "dynamodb:BatchGetItem", "dynamodb:Query", "dynamodb:Scan",
      "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:BatchWriteItem",
      "dynamodb:ConditionCheckItem", "dynamodb:DescribeTable",
    ]
    resources = ["arn:aws:dynamodb:${var.region}:${local.account_id}:table/cip-${each.key}-*"]
  }

  statement {
    sid = "AllowEnvironmentBuckets"
    actions = [
      "s3:GetObject", "s3:GetObjectVersion", "s3:PutObject", "s3:DeleteObject",
      "s3:ListBucket", "s3:GetBucketLocation", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts",
    ]
    resources = [
      "arn:aws:s3:::cip-${each.key}-*",
      "arn:aws:s3:::cip-${each.key}-*/*",
    ]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceAccount"
      values   = [local.account_id]
    }
  }

  statement {
    sid       = "AllowEnvironmentParameters"
    actions   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
    resources = ["arn:aws:ssm:${var.region}:${local.account_id}:parameter/cip/${each.key}/*"]
  }

  statement {
    sid     = "AllowEnvironmentLogs"
    actions = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [
      "arn:aws:logs:${var.region}:${local.account_id}:log-group:/aws/lambda/cip-${each.key}-*",
      "arn:aws:logs:${var.region}:${local.account_id}:log-group:/aws/lambda/cip-${each.key}-*:*",
    ]
  }

  statement {
    sid     = "AllowEnvironmentInvocations"
    actions = ["lambda:InvokeFunction", "states:StartExecution", "sns:Publish"]
    resources = [
      "arn:aws:lambda:${var.region}:${local.account_id}:function:cip-${each.key}-*",
      "arn:aws:states:${var.region}:${local.account_id}:stateMachine:cip-${each.key}-*",
      "arn:aws:sns:${var.region}:${local.account_id}:cip-${each.key}-*",
    ]
  }

  # These actions have no resource-level scope. Bedrock is narrowed to model ARNs in Phase 2.
  statement {
    sid = "AllowUnscopableActions"
    actions = [
      "xray:PutTraceSegments", "xray:PutTelemetryRecords",
      "cloudwatch:PutMetricData",
      "bedrock:InvokeModel",
      "logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery", "logs:ListLogDeliveries", "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies", "logs:DescribeLogGroups",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "AllowDecryptViaServices"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values = [
        "ssm.${var.region}.amazonaws.com",
        "secretsmanager.${var.region}.amazonaws.com",
        "dynamodb.${var.region}.amazonaws.com",
        "s3.${var.region}.amazonaws.com",
      ]
    }
  }

  dynamic "statement" {
    for_each = each.key == "prod" ? [1] : []
    content {
      sid       = "AllowProdSecrets"
      actions   = ["secretsmanager:GetSecretValue"]
      resources = ["arn:aws:secretsmanager:${var.region}:${local.account_id}:secret:cip/prod/*"]
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
    sid     = "DenyStateAndAuditBuckets"
    effect  = "Deny"
    actions = ["*"]
    resources = [
      "arn:aws:s3:::cip-tfstate-*",
      "arn:aws:s3:::cip-tfstate-*/*",
      "arn:aws:s3:::cip-cloudtrail-*",
      "arn:aws:s3:::cip-cloudtrail-*/*",
    ]
  }

  statement {
    sid    = "DenyExecutionFlagWrites"
    effect = "Deny"
    actions = [
      "ssm:PutParameter", "ssm:DeleteParameter", "ssm:DeleteParameters",
      "ssm:LabelParameterVersion", "ssm:UnlabelParameterVersion",
    ]
    resources = [for flag in local.flag_names : "arn:aws:ssm:*:${local.account_id}:parameter/cip/*/${flag}"]
  }

  statement {
    sid    = "DenyLedgerMutation"
    effect = "Deny"
    actions = [
      "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:BatchWriteItem",
      "dynamodb:PartiQLUpdate", "dynamodb:PartiQLDelete",
      "dynamodb:UpdateTable", "dynamodb:DeleteTable", "dynamodb:UpdateTimeToLive",
      "dynamodb:UpdateContinuousBackups", "dynamodb:RestoreTableFromBackup",
      "dynamodb:RestoreTableToPointInTime", "dynamodb:ImportTable", "dynamodb:DeleteBackup",
      "dynamodb:PutResourcePolicy", "dynamodb:DeleteResourcePolicy",
    ]
    resources = [
      "arn:aws:dynamodb:*:${local.account_id}:table/cip-${each.key}-ledger",
      "arn:aws:dynamodb:*:${local.account_id}:table/cip-${each.key}-ledger/backup/*",
    ]
  }

  statement {
    sid       = "DenyFunctionUrls"
    effect    = "Deny"
    actions   = ["lambda:CreateFunctionUrlConfig", "lambda:UpdateFunctionUrlConfig"]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "workload_boundary" {
  for_each = local.environments
  name     = "cip-${each.key}-workload-boundary"
  policy   = data.aws_iam_policy_document.workload_boundary[each.key].json
}
