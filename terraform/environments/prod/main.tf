terraform {
  required_version = "~> 1.16"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.67" }
  }
  backend "s3" {
    key          = "env/prod/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
    encrypt      = true
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { project = "cip", env = "prod", managed_by = "terraform" }
  }
}

module "workload" {
  source = "../../modules/workload"

  env                 = "prod"
  region              = var.region
  alert_email         = var.alert_email
  artifact_path       = var.artifact_path
  deletion_protection = true
  log_retention_days  = 90
  schedule_enabled    = true
  capture_enabled     = true
  # Hourly capture stays off until the deployed role is confirmed evidence-only.
  capture_schedule_enabled = false
}
