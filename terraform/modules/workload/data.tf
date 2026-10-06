module "data_bucket" {
  #checkov:skip=CKV_AWS_19:SSE-S3 is set through server_side_encryption_configuration (separate module resource)
  #checkov:skip=CKV_AWS_21:Versioning is enabled through the module's versioning input (separate module resource)
  #checkov:skip=CKV_AWS_300:abort_incomplete_multipart_upload_days is set in lifecycle_rule (dynamic block)
  #checkov:skip=CKV2_AWS_6:The account-level S3 public access block (bootstrap) covers this bucket; the deploy role cannot change public access settings
  source  = "terraform-aws-modules/s3-bucket/aws"
  version = "5.16.1"

  bucket        = "${local.prefix}-data-${local.account_id}"
  force_destroy = !local.deletion_protection

  # Public access is blocked account-wide in bootstrap. The deploy role is denied
  # s3:PutBucketPublicAccessBlock, so the bucket-level block is not managed here.
  attach_public_policy = false

  versioning = { enabled = true }

  server_side_encryption_configuration = {
    rule = {
      apply_server_side_encryption_by_default = { sse_algorithm = "AES256" }
    }
  }

  attach_deny_insecure_transport_policy = true

  lifecycle_rule = [
    {
      id                            = "snapshots-to-glacier-ir"
      enabled                       = true
      filter                        = { prefix = "snapshots/" }
      transition                    = [{ days = 90, storage_class = "GLACIER_IR" }]
      noncurrent_version_expiration = { noncurrent_days = 30 }
    },
    {
      id                                     = "abort-incomplete-uploads"
      enabled                                = true
      abort_incomplete_multipart_upload_days = 7
    },
  ]
}

module "ledger_table" {
  source  = "terraform-aws-modules/dynamodb-table/aws"
  version = "5.5.2"

  name      = "${local.prefix}-ledger"
  hash_key  = "PK"
  range_key = "SK"

  attributes = [
    for name in ["PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK"] : { name = name, type = "S" }
  ]

  global_secondary_indexes = [
    for index in ["GSI1", "GSI2"] : {
      name            = index
      hash_key        = "${index}PK"
      range_key       = "${index}SK"
      projection_type = "ALL"
    }
  ]

  point_in_time_recovery_enabled = true
  server_side_encryption_enabled = true
  deletion_protection_enabled    = local.deletion_protection
}

module "state_table" {
  source  = "terraform-aws-modules/dynamodb-table/aws"
  version = "5.5.2"

  name      = "${local.prefix}-state"
  hash_key  = "PK"
  range_key = "SK"

  attributes = [
    { name = "PK", type = "S" },
    { name = "SK", type = "S" },
  ]

  ttl_enabled        = true
  ttl_attribute_name = "expires_at"

  point_in_time_recovery_enabled = true
  server_side_encryption_enabled = true
  deletion_protection_enabled    = local.deletion_protection
}

module "counters_table" {
  source  = "terraform-aws-modules/dynamodb-table/aws"
  version = "5.5.2"

  name     = "${local.prefix}-counters"
  hash_key = "PK"

  attributes = [{ name = "PK", type = "S" }]

  ttl_enabled        = true
  ttl_attribute_name = "expires_at"

  point_in_time_recovery_enabled = true
  server_side_encryption_enabled = true
  deletion_protection_enabled    = local.deletion_protection
}
