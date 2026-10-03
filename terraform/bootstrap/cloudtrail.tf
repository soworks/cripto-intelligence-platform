locals {
  trail_name        = "cip-management"
  trail_arn         = "arn:aws:cloudtrail:${var.region}:${local.account_id}:trail/${local.trail_name}"
  cloudtrail_bucket = "cip-cloudtrail-${local.account_id}"
}

# The module's built-in CloudTrail policy has no aws:SourceArn condition and allows
# AWSLogs/* for any account, so the scoped policy below is attached instead.
data "aws_iam_policy_document" "cloudtrail_bucket" {
  count = var.create_cloudtrail ? 1 : 0

  statement {
    sid       = "AclCheck"
    actions   = ["s3:GetBucketAcl"]
    resources = ["arn:aws:s3:::${local.cloudtrail_bucket}"]
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values   = [local.trail_arn]
    }
  }

  statement {
    sid       = "Write"
    actions   = ["s3:PutObject"]
    resources = ["arn:aws:s3:::${local.cloudtrail_bucket}/AWSLogs/${local.account_id}/*"]
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "s3:x-amz-acl"
      values   = ["bucket-owner-full-control"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values   = [local.trail_arn]
    }
  }
}

module "cloudtrail_bucket" {
  #checkov:skip=CKV_AWS_21:CloudTrail log file validation provides integrity; versioning adds cost only
  #checkov:skip=CKV2_AWS_61:Lifecycle is set through the module's lifecycle_rule (dynamic block)
  #checkov:skip=CKV2_AWS_6:Public access block is created by the module (all four flags default to true)
  #checkov:skip=CKV_AWS_300:abort_incomplete_multipart_upload_days is set in lifecycle_rule (dynamic block)
  source  = "terraform-aws-modules/s3-bucket/aws"
  version = "5.16.1"

  create_bucket = var.create_cloudtrail
  bucket        = local.cloudtrail_bucket

  attach_policy                         = true
  policy                                = try(data.aws_iam_policy_document.cloudtrail_bucket[0].json, null)
  attach_deny_insecure_transport_policy = true

  lifecycle_rule = [{
    id                                     = "expire-after-1-year"
    enabled                                = true
    expiration                             = { days = 365 }
    abort_incomplete_multipart_upload_days = 7
  }]
}

moved {
  from = aws_s3_bucket.cloudtrail[0]
  to   = module.cloudtrail_bucket.aws_s3_bucket.this[0]
}

moved {
  from = aws_s3_bucket_public_access_block.cloudtrail[0]
  to   = module.cloudtrail_bucket.aws_s3_bucket_public_access_block.this[0]
}

moved {
  from = aws_s3_bucket_lifecycle_configuration.cloudtrail[0]
  to   = module.cloudtrail_bucket.aws_s3_bucket_lifecycle_configuration.this[0]
}

moved {
  from = aws_s3_bucket_policy.cloudtrail[0]
  to   = module.cloudtrail_bucket.aws_s3_bucket_policy.this[0]
}

resource "aws_cloudtrail" "management" {
  count                         = var.create_cloudtrail ? 1 : 0
  name                          = local.trail_name
  s3_bucket_name                = module.cloudtrail_bucket.s3_bucket_id
  is_multi_region_trail         = true
  include_global_service_events = true
  enable_log_file_validation    = true
  depends_on                    = [module.cloudtrail_bucket]
}
