terraform {
  required_version = "~> 1.16"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.67" }
  }
  backend "s3" {
    key          = "env/dev/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
    encrypt      = true
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { project = "cip", env = local.env, managed_by = "terraform" }
  }
}

data "aws_caller_identity" "current" {}

locals {
  env          = "dev"
  prefix       = "cip-${local.env}"
  account_id   = data.aws_caller_identity.current.account_id
  flags_prefix = "/cip/${local.env}"

  # Built from the account ID: the PR plan role has no iam:ListPolicies.
  boundary_arn = "arn:aws:iam::${local.account_id}:policy/${local.prefix}-workload-boundary"
  # The deploy role can only update, tag, delete or pass roles on this path, and it can
  # only create roles here with the boundary attached.
  role_path = "/cip/${local.env}/"

  deletion_protection = false
  log_retention_days  = 14
  schedule_enabled    = true
}
