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

  # Exempt from the state and CloudTrail bucket denies. Losing both locks the owner out
  # of those buckets; the root user can always delete a bucket policy.
  admin_principal_arns = concat(
    ["arn:aws:iam::${local.account_id}:root"],
    [for user in var.admin_user_names : "arn:aws:iam::${local.account_id}:user/${user}"],
    var.extra_admin_principal_arns,
  )
}
