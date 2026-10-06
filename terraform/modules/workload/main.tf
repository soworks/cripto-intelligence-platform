data "aws_caller_identity" "current" {}

locals {
  env          = var.env
  prefix       = "cip-${local.env}"
  account_id   = data.aws_caller_identity.current.account_id
  flags_prefix = "/cip/${local.env}"

  boundary_arn = "arn:aws:iam::${local.account_id}:policy/${local.prefix}-workload-boundary"
  role_path    = "/cip/${local.env}/"

  deletion_protection = var.deletion_protection
  log_retention_days  = var.log_retention_days
  schedule_enabled    = var.schedule_enabled
}
