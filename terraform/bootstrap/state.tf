resource "aws_s3_bucket" "tf_state" {
  #checkov:skip=CKV2_AWS_61:Terraform state keeps all versions intentionally
  bucket = "cip-tfstate-${local.account_id}"
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tf_state" {
  bucket                  = aws_s3_bucket.tf_state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

locals {
  # Each state prefix is writable only by its deployer and the admin principals.
  state_writers = {
    "env/dev"  = "arn:aws:iam::${local.account_id}:role/cip-gha-dev"
    "env/prod" = "arn:aws:iam::${local.account_id}:role/cip-gha-prod"
  }
}

data "aws_iam_policy_document" "tf_state_tls" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.tf_state.arn,
      "${aws_s3_bucket.tf_state.arn}/*",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  statement {
    sid       = "DenyBootstrapStateExceptAdmins"
    effect    = "Deny"
    actions   = ["s3:GetObject*", "s3:PutObject*", "s3:DeleteObject*", "s3:RestoreObject"]
    resources = ["${aws_s3_bucket.tf_state.arn}/bootstrap/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "ArnNotLike"
      variable = "aws:PrincipalArn"
      values   = local.admin_principal_arns
    }
  }

  dynamic "statement" {
    for_each = local.state_writers
    content {
      sid       = "DenyWrites${replace(title(replace(statement.key, "/", " ")), " ", "")}ExceptDeployer"
      effect    = "Deny"
      actions   = ["s3:PutObject*", "s3:DeleteObject*", "s3:RestoreObject"]
      resources = ["${aws_s3_bucket.tf_state.arn}/${statement.key}/*"]
      principals {
        type        = "*"
        identifiers = ["*"]
      }
      condition {
        test     = "ArnNotLike"
        variable = "aws:PrincipalArn"
        values   = concat([statement.value], local.admin_principal_arns)
      }
    }
  }

  statement {
    sid       = "DenyProdStateReadsExceptProdDeployer"
    effect    = "Deny"
    actions   = ["s3:GetObject*"]
    resources = ["${aws_s3_bucket.tf_state.arn}/env/prod/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "ArnNotLike"
      variable = "aws:PrincipalArn"
      values   = concat([local.state_writers["env/prod"]], local.admin_principal_arns)
    }
  }
}

resource "aws_s3_bucket_policy" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id
  policy = data.aws_iam_policy_document.tf_state_tls.json
}
