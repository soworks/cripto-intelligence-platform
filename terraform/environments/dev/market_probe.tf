locals {
  market_probe_name = "${local.prefix}-market-probe"
  market_probe_log_group_arns = [
    "arn:aws:logs:${var.region}:${local.account_id}:log-group:/aws/lambda/${local.market_probe_name}:*",
  ]
}

module "market_probe_role" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role"
  version = "6.8.2"

  name                 = "${local.market_probe_name}-lambda"
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
      resources = local.market_probe_log_group_arns
    }
    Tracing = {
      actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
      resources = ["*"]
    }
  }
}

module "market_probe_lambda" {
  #checkov:skip=CKV_AWS_50:tracing_mode = Active is applied through a dynamic block
  #checkov:skip=CKV_AWS_258:No function URL is created (create_lambda_function_url defaults to false)
  source  = "terraform-aws-modules/lambda/aws"
  version = "8.9.0"

  function_name = local.market_probe_name
  handler       = "cip.handlers.market_probe.probe"
  runtime       = "python3.13"
  architectures = ["arm64"]
  memory_size   = 256
  timeout       = 15

  create_package         = false
  local_existing_package = var.artifact_path

  create_role = false
  lambda_role = module.market_probe_role.arn

  environment_variables = {
    POLICY_PATH                  = "/var/task/policies/investment-policy.yaml"
    POWERTOOLS_SERVICE_NAME      = "cip-market-probe"
    POWERTOOLS_METRICS_NAMESPACE = "CIP/MarketData"
    POWERTOOLS_LOG_LEVEL         = "INFO"
  }

  logging_log_format                = "JSON"
  cloudwatch_logs_retention_in_days = local.log_retention_days
  tracing_mode                      = "Active"
}

module "market_probe_scheduler_role" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role"
  version = "6.8.2"

  name                 = "${local.market_probe_name}-scheduler"
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
    InvokeProbe = {
      actions = ["lambda:InvokeFunction"]
      resources = [
        module.market_probe_lambda.lambda_function_arn,
        "${module.market_probe_lambda.lambda_function_arn}:*",
      ]
    }
  }
}

module "market_probe_schedule" {
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
    (local.market_probe_name) = {
      group_name          = "default"
      schedule_expression = "rate(1 hour)"
      state               = local.schedule_enabled
      arn                 = module.market_probe_lambda.lambda_function_arn
      role_arn            = module.market_probe_scheduler_role.arn
      input               = jsonencode({ trigger = "schedule" })
      retry_policy = {
        maximum_retry_attempts       = 0
        maximum_event_age_in_seconds = 3600
      }
    }
  }
}

module "alarm_market_geo_blocked" {
  source  = "terraform-aws-modules/cloudwatch/aws//modules/metric-alarm"
  version = "5.7.3"

  alarm_name          = "${local.prefix}-market-data-geo-blocked"
  alarm_description   = "data-api.binance.vision returned HTTP 451 to the market probe"
  namespace           = "CIP/MarketData"
  metric_name         = "GeoBlocked"
  dimensions          = { service = "cip-market-probe" }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [module.alerts_topic.topic_arn]
}

module "alarm_market_probe_missed" {
  source  = "terraform-aws-modules/cloudwatch/aws//modules/metric-alarm"
  version = "5.7.3"

  create_metric_alarm = local.schedule_enabled

  alarm_name          = "${local.prefix}-market-probe-missed"
  namespace           = "AWS/Lambda"
  metric_name         = "Invocations"
  dimensions          = { FunctionName = module.market_probe_lambda.lambda_function_name }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [module.alerts_topic.topic_arn]
}
