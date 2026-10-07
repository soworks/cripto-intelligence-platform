locals {
  capture_name = "${local.prefix}-session-capture"
  capture_log_group_arns = [
    "arn:aws:logs:${var.region}:${local.account_id}:log-group:/aws/lambda/${local.capture_name}:*",
  ]
  capture_object_arns = [
    "${module.data_bucket.s3_bucket_arn}/captures/*",
    "${module.data_bucket.s3_bucket_arn}/sessions/*",
  ]
}

module "session_capture_role" {
  count   = var.capture_enabled ? 1 : 0
  source  = "terraform-aws-modules/iam/aws//modules/iam-role"
  version = "6.8.2"

  name                 = "${local.capture_name}-lambda"
  use_name_prefix      = false
  path                 = local.role_path
  permissions_boundary = local.boundary_arn

  trust_policy_permissions = {
    LambdaAssume = {
      actions    = ["sts:AssumeRole"]
      principals = [{ type = "Service", identifiers = ["lambda.amazonaws.com"] }]
    }
  }

  create_inline_policy = true
  inline_policy_permissions = {
    Logs = {
      actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      resources = local.capture_log_group_arns
    }
    Tracing = {
      actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
      resources = ["*"]
    }
    EvidenceObjects = {
      actions   = ["s3:GetObject", "s3:PutObject"]
      resources = local.capture_object_arns
    }
    EvidenceList = {
      actions   = ["s3:ListBucket"]
      resources = [module.data_bucket.s3_bucket_arn]
      condition = [{
        test     = "StringLike"
        variable = "s3:prefix"
        values   = ["captures/", "captures/*", "sessions/", "sessions/*"]
      }]
    }
    DenyEvidenceDelete = {
      effect    = "Deny"
      actions   = ["s3:DeleteObject", "s3:DeleteObjectVersion"]
      resources = local.capture_object_arns
    }
  }
}

module "session_capture_lambda" {
  count = var.capture_enabled ? 1 : 0
  #checkov:skip=CKV_AWS_50:tracing_mode = Active is applied through a dynamic block
  #checkov:skip=CKV_AWS_258:No function URL is created (create_lambda_function_url defaults to false)
  source  = "terraform-aws-modules/lambda/aws"
  version = "8.9.0"

  function_name = local.capture_name
  handler       = "cip.handlers.session_capture.capture"
  runtime       = "python3.13"
  architectures = ["arm64"]
  memory_size   = 512
  # exchangeInfo and the 24h ticker dominate the public-call budget. 180s matches
  # the recorder cycle, which already includes the same spot timeout and 429 pause.
  timeout = 180

  create_package         = false
  local_existing_package = var.artifact_path

  create_role = false
  lambda_role = module.session_capture_role[0].arn

  environment_variables = {
    POLICY_PATH             = "/var/task/policies/investment-policy.yaml"
    DATA_BUCKET             = module.data_bucket.s3_bucket_id
    CAPTURE_SYMBOLS         = "BTCUSDT"
    POWERTOOLS_SERVICE_NAME = "cip-session-capture"
    POWERTOOLS_LOG_LEVEL    = "INFO"
  }

  logging_log_format                = "JSON"
  cloudwatch_logs_retention_in_days = local.log_retention_days
  tracing_mode                      = "Active"
}

module "session_capture_scheduler_role" {
  count   = var.capture_enabled ? 1 : 0
  source  = "terraform-aws-modules/iam/aws//modules/iam-role"
  version = "6.8.2"

  name                 = "${local.capture_name}-scheduler"
  use_name_prefix      = false
  path                 = local.role_path
  permissions_boundary = local.boundary_arn

  trust_policy_permissions = {
    SchedulerAssume = {
      actions    = ["sts:AssumeRole"]
      principals = [{ type = "Service", identifiers = ["scheduler.amazonaws.com"] }]
      condition = [{
        test     = "StringEquals"
        variable = "aws:SourceAccount"
        values   = [local.account_id]
      }]
    }
  }

  create_inline_policy = true
  inline_policy_permissions = {
    InvokeCapture = {
      actions = ["lambda:InvokeFunction"]
      resources = [
        module.session_capture_lambda[0].lambda_function_arn,
        "${module.session_capture_lambda[0].lambda_function_arn}:*",
      ]
    }
  }
}

module "session_capture_schedule" {
  count   = var.capture_enabled ? 1 : 0
  source  = "terraform-aws-modules/eventbridge/aws"
  version = "4.3.2"

  create_bus             = false
  create_role            = false
  create_rules           = false
  create_targets         = false
  create_permissions     = false
  create_log_delivery    = false
  create_schedule_groups = false

  append_schedule_postfix = false

  schedules = {
    (local.capture_name) = {
      group_name          = "default"
      schedule_expression = "rate(1 hour)"
      state               = var.capture_schedule_enabled
      arn                 = module.session_capture_lambda[0].lambda_function_arn
      role_arn            = module.session_capture_scheduler_role[0].arn
      input               = jsonencode({ trigger = "schedule" })
      retry_policy = {
        maximum_retry_attempts       = 0
        maximum_event_age_in_seconds = 3600
      }
    }
  }
}

module "alarm_session_capture_missed" {
  count   = var.capture_enabled && var.capture_schedule_enabled ? 1 : 0
  source  = "terraform-aws-modules/cloudwatch/aws//modules/metric-alarm"
  version = "5.7.3"

  alarm_name          = "${local.prefix}-session-capture-missed"
  namespace           = "AWS/Lambda"
  metric_name         = "Invocations"
  dimensions          = { FunctionName = module.session_capture_lambda[0].lambda_function_name }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [module.alerts_topic.topic_arn]
}
