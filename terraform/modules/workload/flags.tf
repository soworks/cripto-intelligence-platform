# Fail-closed defaults: SHADOW mode, trading disabled. The kill switch is operated
# out of band, so Terraform never resets its value.
module "flag_execution_mode" {
  source  = "terraform-aws-modules/ssm-parameter/aws"
  version = "2.1.2"

  name  = "${local.flags_prefix}/execution_mode"
  type  = "String"
  value = "SHADOW"
}

module "flag_trading_enabled" {
  source  = "terraform-aws-modules/ssm-parameter/aws"
  version = "2.1.2"

  name  = "${local.flags_prefix}/trading_enabled"
  type  = "String"
  value = "false"
}

module "flag_kill_switch" {
  source  = "terraform-aws-modules/ssm-parameter/aws"
  version = "2.1.2"

  name                 = "${local.flags_prefix}/kill_switch"
  type                 = "String"
  value                = "false"
  ignore_value_changes = true
}
