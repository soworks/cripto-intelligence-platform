module "alerts_topic" {
  source  = "terraform-aws-modules/sns/aws"
  version = "7.2.0"

  name                = "${local.prefix}-alerts"
  create_topic_policy = false

  subscriptions = {
    email = {
      protocol = "email"
      endpoint = var.alert_email
    }
  }
}

module "alarm_pipeline_failed" {
  source  = "terraform-aws-modules/cloudwatch/aws//modules/metric-alarm"
  version = "5.7.3"

  alarm_name          = "${local.prefix}-scan-pipeline-failed"
  namespace           = "AWS/States"
  metric_name         = "ExecutionsFailed"
  dimensions          = { StateMachineArn = module.scan_state_machine.state_machine_arn }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [module.alerts_topic.topic_arn]
}

module "alarm_pipeline_missed" {
  source  = "terraform-aws-modules/cloudwatch/aws//modules/metric-alarm"
  version = "5.7.3"

  create_metric_alarm = local.schedule_enabled

  alarm_name          = "${local.prefix}-scan-pipeline-missed"
  namespace           = "AWS/States"
  metric_name         = "ExecutionsSucceeded"
  dimensions          = { StateMachineArn = module.scan_state_machine.state_machine_arn }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [module.alerts_topic.topic_arn]
}
