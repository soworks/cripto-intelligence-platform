module "account_public_access_block" {
  source  = "terraform-aws-modules/s3-bucket/aws//modules/account-public-access"
  version = "5.16.1"

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
