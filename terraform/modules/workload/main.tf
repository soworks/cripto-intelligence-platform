data "aws_caller_identity" "current" {}

locals {
  env          = var.env
  prefix       = "cip-${local.env}"
  account_id   = data.aws_caller_identity.current.account_id
  flags_prefix = "/cip/${local.env}"

  # Built from the account ID: the PR plan role has no iam:ListPolicies.
  boundary_arn = "arn:aws:iam::${local.account_id}:policy/${local.prefix}-workload-boundary"
  # The deploy role can only update, tag, delete or pass roles on this path, and it can
  # only create roles here with the boundary attached.
  role_path = "/cip/${local.env}/"

  deletion_protection = var.deletion_protection
  log_retention_days  = var.log_retention_days
  schedule_enabled    = var.schedule_enabled
}
