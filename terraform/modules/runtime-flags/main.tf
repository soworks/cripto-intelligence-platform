locals {
  prefix = "/cip/${var.env}"
}

resource "aws_ssm_parameter" "execution_mode" {
  name  = "${local.prefix}/execution_mode"
  type  = "String"
  value = "SHADOW"
}

resource "aws_ssm_parameter" "trading_enabled" {
  name  = "${local.prefix}/trading_enabled"
  type  = "String"
  value = "false"
}

resource "aws_ssm_parameter" "kill_switch" {
  name  = "${local.prefix}/kill_switch"
  type  = "String"
  value = "false"
  lifecycle {
    ignore_changes = [value]
  }
}
