terraform {
  required_version = "~> 1.16"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.67" }
  }
}

provider "aws" {
  region  = var.region
  profile = "soworks"
  default_tags {
    tags = { project = "cip", managed_by = "terraform", stack = "bootstrap" }
  }
}

data "aws_caller_identity" "current" {}

locals {
  account_id   = data.aws_caller_identity.current.account_id
  environments = toset(["dev", "prod"])
}
