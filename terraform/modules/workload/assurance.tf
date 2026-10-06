locals {
  weekly_assurance_name = "${local.prefix}-weekly-assurance"
  weekly_assurance_log_group_arns = [
    "arn:aws:logs:${var.region}:${local.account_id}:log-group:/aws/lambda/${local.weekly_assurance_name}:*",
  ]
}

module "weekly_assurance_role" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role"
  version = "6.8.2"

  name                 = "${local.weekly_assurance_name}-lambda"
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
      resources = local.weekly_assurance_log_group_arns
    }
    Tracing = {
      actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
      resources = ["*"]
    }
  }
}

module "weekly_assurance_lambda" {
  #checkov:skip=CKV_AWS_50:tracing_mode = Active is applied through a dynamic block
  #checkov:skip=CKV_AWS_258:No function URL is created (create_lambda_function_url defaults to false)
  source  = "terraform-aws-modules/lambda/aws"
  version = "8.9.0"

  function_name = local.weekly_assurance_name
  handler       = "cip.handlers.assurance.weekly"
  runtime       = "python3.13"
  architectures = ["arm64"]
  memory_size   = 256
  timeout       = 30

  create_package         = false
  local_existing_package = var.artifact_path

  create_role = false
  lambda_role = module.weekly_assurance_role.arn

  environment_variables = {
    POWERTOOLS_SERVICE_NAME = "cip-assurance"
    POWERTOOLS_LOG_LEVEL    = "INFO"
  }

  logging_log_format                = "JSON"
  cloudwatch_logs_retention_in_days = local.log_retention_days
  tracing_mode                      = "Active"
}

module "weekly_assurance_scheduler_role" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role"
  version = "6.8.2"

  name                 = "${local.weekly_assurance_name}-scheduler"
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
    InvokeAssurance = {
      actions = ["lambda:InvokeFunction"]
      resources = [
        module.weekly_assurance_lambda.lambda_function_arn,
        "${module.weekly_assurance_lambda.lambda_function_arn}:*",
      ]
    }
  }
}

module "weekly_assurance_schedule" {
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
    (local.weekly_assurance_name) = {
      group_name          = "default"
      description         = "Weekly four-dimension scorecard. No stored inputs means no report."
      schedule_expression = "cron(0 0 ? * MON *)"
      state               = local.schedule_enabled
      arn                 = module.weekly_assurance_lambda.lambda_function_arn
      role_arn            = module.weekly_assurance_scheduler_role.arn
      input               = jsonencode({ trigger = "schedule" })
      retry_policy = {
        maximum_retry_attempts       = 0
        maximum_event_age_in_seconds = 86400
      }
    }
  }
}
