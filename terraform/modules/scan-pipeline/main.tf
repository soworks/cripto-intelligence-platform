data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  prefix     = "cip-${var.env}"
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region
  functions = {
    start-scan     = "cip.handlers.pipeline.start_scan"
    complete-scan  = "cip.handlers.pipeline.complete_scan"
    record-failure = "cip.handlers.pipeline.record_failure"
  }
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "pipeline_lambda" {
  name                 = "${local.prefix}-pipeline-lambda"
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume.json
  permissions_boundary = var.permissions_boundary_arn
}

resource "aws_cloudwatch_log_group" "fn" {
  for_each          = local.functions
  name              = "/aws/lambda/${local.prefix}-${each.key}"
  retention_in_days = var.log_retention_days
}

data "aws_iam_policy_document" "pipeline_lambda" {
  statement {
    sid       = "LedgerAppend"
    actions   = ["dynamodb:PutItem"]
    resources = [var.ledger_table_arn]
  }

  statement {
    sid       = "FlagsRead"
    actions   = ["ssm:GetParameters"]
    resources = ["arn:aws:ssm:${local.region}:${local.account_id}:parameter${var.flags_prefix}/*"]
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [for group in aws_cloudwatch_log_group.fn : "${group.arn}:*"]
  }

  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }

  statement {
    sid    = "DenyLedgerMutation"
    effect = "Deny"
    actions = [
      "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:BatchWriteItem",
      "dynamodb:PartiQLUpdate", "dynamodb:PartiQLDelete",
    ]
    resources = [var.ledger_table_arn]
  }
}

resource "aws_iam_role_policy" "pipeline_lambda" {
  name   = "${local.prefix}-pipeline-lambda"
  role   = aws_iam_role.pipeline_lambda.id
  policy = data.aws_iam_policy_document.pipeline_lambda.json
}

resource "aws_lambda_function" "fn" {
  for_each         = local.functions
  function_name    = "${local.prefix}-${each.key}"
  role             = aws_iam_role.pipeline_lambda.arn
  handler          = each.value
  runtime          = "python3.13"
  architectures    = ["arm64"]
  filename         = var.artifact_path
  source_code_hash = filebase64sha256(var.artifact_path)
  memory_size      = 256
  timeout          = 30

  environment {
    variables = {
      LEDGER_TABLE            = var.ledger_table_name
      FLAGS_PREFIX            = var.flags_prefix
      POLICY_PATH             = "/var/task/policies/investment-policy.yaml"
      POWERTOOLS_SERVICE_NAME = "cip-pipeline"
      POWERTOOLS_LOG_LEVEL    = "INFO"
    }
  }

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.fn[each.key].name
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_iam_role_policy.pipeline_lambda]
}

data "aws_iam_policy_document" "states_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "state_machine" {
  name                 = "${local.prefix}-scan-sfn"
  assume_role_policy   = data.aws_iam_policy_document.states_assume.json
  permissions_boundary = var.permissions_boundary_arn
}

data "aws_iam_policy_document" "state_machine" {
  statement {
    sid     = "InvokePipelineLambdas"
    actions = ["lambda:InvokeFunction"]
    resources = flatten([
      for fn in aws_lambda_function.fn : [fn.arn, "${fn.arn}:*"]
    ])
  }

  statement {
    sid = "LogDelivery"
    actions = [
      "logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery", "logs:ListLogDeliveries", "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies", "logs:DescribeLogGroups",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "state_machine" {
  name   = "${local.prefix}-scan-sfn"
  role   = aws_iam_role.state_machine.id
  policy = data.aws_iam_policy_document.state_machine.json
}

resource "aws_cloudwatch_log_group" "sfn" {
  name              = "/aws/vendedlogs/states/${local.prefix}-scan-pipeline"
  retention_in_days = var.log_retention_days
}

resource "aws_sfn_state_machine" "scan" {
  name     = "${local.prefix}-scan-pipeline"
  role_arn = aws_iam_role.state_machine.arn
  type     = "STANDARD"
  definition = templatefile("${path.module}/scan-pipeline.asl.json", {
    start_scan_arn     = aws_lambda_function.fn["start-scan"].arn
    complete_scan_arn  = aws_lambda_function.fn["complete-scan"].arn
    record_failure_arn = aws_lambda_function.fn["record-failure"].arn
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn.arn}:*"
    include_execution_data = true
    level                  = "ERROR"
  }

  depends_on = [aws_iam_role_policy.state_machine]
}

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name                 = "${local.prefix}-scan-scheduler"
  assume_role_policy   = data.aws_iam_policy_document.scheduler_assume.json
  permissions_boundary = var.permissions_boundary_arn
}

resource "aws_iam_role_policy" "scheduler" {
  name = "${local.prefix}-scan-scheduler"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "states:StartExecution"
      Resource = aws_sfn_state_machine.scan.arn
    }]
  })
}

resource "aws_scheduler_schedule" "scan" {
  name                = "${local.prefix}-scan"
  schedule_expression = var.schedule_expression
  state               = var.schedule_enabled ? "ENABLED" : "DISABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_sfn_state_machine.scan.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ trigger = "schedule" })
    retry_policy {
      maximum_retry_attempts = 0
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "pipeline_failed" {
  alarm_name          = "${local.prefix}-scan-pipeline-failed"
  namespace           = "AWS/States"
  metric_name         = "ExecutionsFailed"
  dimensions          = { StateMachineArn = aws_sfn_state_machine.scan.arn }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alarm_topic_arn]
}

resource "aws_cloudwatch_metric_alarm" "no_successful_scan" {
  count               = var.schedule_enabled ? 1 : 0
  alarm_name          = "${local.prefix}-scan-pipeline-missed"
  namespace           = "AWS/States"
  metric_name         = "ExecutionsSucceeded"
  dimensions          = { StateMachineArn = aws_sfn_state_machine.scan.arn }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [var.alarm_topic_arn]
}
