locals {
  functions = {
    start-scan     = "cip.handlers.pipeline.start_scan"
    complete-scan  = "cip.handlers.pipeline.complete_scan"
    record-failure = "cip.handlers.pipeline.record_failure"
  }

  state_machine_name = "${local.prefix}-scan-pipeline"

  # ARNs are built as strings so the roles do not depend on the resources they
  # grant access to (the Lambda module owns the log groups).
  lambda_log_group_arns = [
    for name in keys(local.functions) :
    "arn:aws:logs:${var.region}:${local.account_id}:log-group:/aws/lambda/${local.prefix}-${name}:*"
  ]
}

module "pipeline_lambda_role" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role"
  version = "6.8.2"

  name                 = "${local.prefix}-pipeline-lambda"
  use_name_prefix      = false
  permissions_boundary = local.boundary_arn

  trust_policy_permissions = {
    LambdaAssume = {
      actions    = ["sts:AssumeRole"]
      principals = [{ type = "Service", identifiers = ["lambda.amazonaws.com"] }]
    }
  }

  create_inline_policy = true
  inline_policy_permissions = {
    LedgerAppend = {
      actions   = ["dynamodb:PutItem"]
      resources = [module.ledger_table.dynamodb_table_arn]
    }
    FlagsRead = {
      actions   = ["ssm:GetParameters"]
      resources = ["arn:aws:ssm:${var.region}:${local.account_id}:parameter${local.flags_prefix}/*"]
    }
    Logs = {
      actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      resources = local.lambda_log_group_arns
    }
    Tracing = {
      actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
      resources = ["*"]
    }
    DenyLedgerMutation = {
      effect = "Deny"
      actions = [
        "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:BatchWriteItem",
        "dynamodb:PartiQLUpdate", "dynamodb:PartiQLDelete",
      ]
      resources = [module.ledger_table.dynamodb_table_arn]
    }
  }
}

module "lambda" {
  #checkov:skip=CKV_AWS_50:tracing_mode = Active is applied through a dynamic block
  #checkov:skip=CKV_AWS_258:No function URL is created (create_lambda_function_url defaults to false)
  source   = "terraform-aws-modules/lambda/aws"
  version  = "8.9.0"
  for_each = local.functions

  function_name = "${local.prefix}-${each.key}"
  handler       = each.value
  runtime       = "python3.13"
  architectures = ["arm64"]
  memory_size   = 256
  timeout       = 30

  create_package         = false
  local_existing_package = var.artifact_path

  create_role = false
  lambda_role = module.pipeline_lambda_role.arn

  environment_variables = {
    LEDGER_TABLE            = module.ledger_table.dynamodb_table_id
    FLAGS_PREFIX            = local.flags_prefix
    POLICY_PATH             = "/var/task/policies/investment-policy.yaml"
    POWERTOOLS_SERVICE_NAME = "cip-pipeline"
    POWERTOOLS_LOG_LEVEL    = "INFO"
  }

  logging_log_format                = "JSON"
  cloudwatch_logs_retention_in_days = local.log_retention_days
  tracing_mode                      = "Active"
}

module "state_machine_role" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role"
  version = "6.8.2"

  name                 = "${local.prefix}-scan-sfn"
  use_name_prefix      = false
  permissions_boundary = local.boundary_arn

  trust_policy_permissions = {
    StatesAssume = {
      actions    = ["sts:AssumeRole"]
      principals = [{ type = "Service", identifiers = ["states.amazonaws.com"] }]
    }
  }

  create_inline_policy = true
  inline_policy_permissions = {
    InvokePipelineLambdas = {
      actions = ["lambda:InvokeFunction"]
      resources = flatten([
        for fn in module.lambda : [fn.lambda_function_arn, "${fn.lambda_function_arn}:*"]
      ])
    }
    LogDelivery = {
      actions = [
        "logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery",
        "logs:DeleteLogDelivery", "logs:ListLogDeliveries", "logs:PutResourcePolicy",
        "logs:DescribeResourcePolicies", "logs:DescribeLogGroups",
      ]
      resources = ["*"]
    }
  }
}

module "scan_state_machine" {
  source  = "terraform-aws-modules/step-functions/aws"
  version = "5.1.1"

  name = local.state_machine_name
  type = "STANDARD"
  definition = templatefile("${path.module}/../../definitions/scan-pipeline.asl.json", {
    start_scan_arn     = module.lambda["start-scan"].lambda_function_arn
    complete_scan_arn  = module.lambda["complete-scan"].lambda_function_arn
    record_failure_arn = module.lambda["record-failure"].lambda_function_arn
  })

  use_existing_role = true
  role_arn          = module.state_machine_role.arn

  logging_configuration = {
    include_execution_data = true
    level                  = "ERROR"
  }
  cloudwatch_log_group_retention_in_days = local.log_retention_days

  # Step Functions validates log delivery permissions at create time.
  depends_on = [module.state_machine_role]
}

module "scheduler_role" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role"
  version = "6.8.2"

  name                 = "${local.prefix}-scan-scheduler"
  use_name_prefix      = false
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
    StartScan = {
      actions   = ["states:StartExecution"]
      resources = [module.scan_state_machine.state_machine_arn]
    }
  }
}

module "scan_schedule" {
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
    "${local.prefix}-scan" = {
      group_name          = "default"
      schedule_expression = "rate(1 hour)"
      state               = local.schedule_enabled
      arn                 = module.scan_state_machine.state_machine_arn
      role_arn            = module.scheduler_role.arn
      input               = jsonencode({ trigger = "schedule" })
      retry_policy = {
        maximum_retry_attempts       = 0
        maximum_event_age_in_seconds = 3600
      }
    }
  }
}
