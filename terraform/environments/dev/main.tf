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
    tags = { project = "cip", env = "dev", managed_by = "terraform" }
  }
}

data "aws_caller_identity" "current" {}

locals {
  # Built from the account ID: the PR plan role has no iam:ListPolicies.
  boundary_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/cip-dev-workload-boundary"
}

module "data" {
  source              = "../../modules/platform-data"
  env                 = "dev"
  deletion_protection = false
}

module "flags" {
  source = "../../modules/runtime-flags"
  env    = "dev"
}

module "alerts" {
  source      = "../../modules/alerts"
  env         = "dev"
  alert_email = var.alert_email
}

module "pipeline" {
  source                   = "../../modules/scan-pipeline"
  env                      = "dev"
  artifact_path            = var.artifact_path
  ledger_table_name        = module.data.ledger_table_name
  ledger_table_arn         = module.data.ledger_table_arn
  flags_prefix             = module.flags.prefix
  alarm_topic_arn          = module.alerts.topic_arn
  permissions_boundary_arn = local.boundary_arn
}
