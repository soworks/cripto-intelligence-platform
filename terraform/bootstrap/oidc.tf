resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

locals {
  oidc_subjects = {
    plan = "repo:${var.github_repository}:pull_request"
    dev  = "repo:${var.github_repository}:environment:dev"
    prod = "repo:${var.github_repository}:environment:prod"
  }
}

data "aws_iam_policy_document" "github_trust" {
  for_each = local.oidc_subjects

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [each.value]
    }
  }
}

resource "aws_iam_role" "github" {
  for_each             = local.oidc_subjects
  name                 = "cip-gha-${each.key}"
  assume_role_policy   = data.aws_iam_policy_document.github_trust[each.key].json
  max_session_duration = 3600
}

resource "aws_iam_role_policy_attachment" "plan_read_only" {
  role       = aws_iam_role.github["plan"].name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

data "aws_iam_policy_document" "deploy" {
  for_each = local.environments

  statement {
    sid       = "TerraformState"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.tf_state.arn}/env/${each.key}/*"]
  }

  statement {
    sid       = "TerraformStateList"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.tf_state.arn]
  }

  statement {
    sid = "ReadForPlanning"
    actions = [
      "iam:Get*", "iam:List*", "iam:SimulatePrincipalPolicy",
      "lambda:Get*", "lambda:List*",
      "states:Describe*", "states:List*",
      "dynamodb:Describe*", "dynamodb:List*",
      "s3:GetBucket*", "s3:GetEncryptionConfiguration", "s3:GetLifecycleConfiguration",
      "s3:GetReplicationConfiguration", "s3:GetAccelerateConfiguration",
      "s3:ListAllMyBuckets",
      "ssm:Describe*", "ssm:ListTagsForResource",
      "sns:Get*", "sns:List*",
      "logs:Describe*", "logs:ListTagsForResource", "logs:ListTagsLogGroup",
      "cloudwatch:Describe*", "cloudwatch:Get*", "cloudwatch:List*",
      "scheduler:Get*", "scheduler:List*",
      "sts:GetCallerIdentity",
    ]
    resources = ["*"]
  }

  statement {
    sid = "StepFunctionsLogDelivery"
    actions = [
      "logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery", "logs:ListLogDeliveries", "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies",
    ]
    resources = ["*"]
  }

  statement {
    sid     = "ManageEnvironmentResources"
    actions = ["lambda:*", "states:*", "dynamodb:*", "s3:*", "ssm:*", "sns:*", "logs:*", "cloudwatch:*", "scheduler:*"]
    resources = [
      "arn:aws:lambda:${var.region}:${local.account_id}:function:cip-${each.key}-*",
      "arn:aws:states:${var.region}:${local.account_id}:stateMachine:cip-${each.key}-*",
      "arn:aws:states:${var.region}:${local.account_id}:execution:cip-${each.key}-*",
      "arn:aws:dynamodb:${var.region}:${local.account_id}:table/cip-${each.key}-*",
      "arn:aws:s3:::cip-${each.key}-*",
      "arn:aws:ssm:${var.region}:${local.account_id}:parameter/cip/${each.key}/*",
      "arn:aws:sns:${var.region}:${local.account_id}:cip-${each.key}-*",
      "arn:aws:logs:${var.region}:${local.account_id}:log-group:/aws/lambda/cip-${each.key}-*",
      "arn:aws:logs:${var.region}:${local.account_id}:log-group:/aws/vendedlogs/states/cip-${each.key}-*",
      "arn:aws:cloudwatch:${var.region}:${local.account_id}:alarm:cip-${each.key}-*",
      "arn:aws:cloudwatch::${local.account_id}:dashboard/cip-${each.key}-*",
      "arn:aws:scheduler:${var.region}:${local.account_id}:schedule/default/cip-${each.key}-*",
    ]
  }

  statement {
    sid = "CreateBoundedWorkloadRoles"
    actions = [
      "iam:CreateRole", "iam:PutRolePolicy", "iam:DeleteRolePolicy",
      "iam:AttachRolePolicy", "iam:DetachRolePolicy",
    ]
    resources = ["arn:aws:iam::${local.account_id}:role/cip-${each.key}-*"]
    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [aws_iam_policy.workload_boundary[each.key].arn]
    }
  }

  statement {
    sid = "MaintainWorkloadRoles"
    actions = [
      "iam:DeleteRole", "iam:UpdateRole", "iam:UpdateRoleDescription",
      "iam:UpdateAssumeRolePolicy", "iam:TagRole", "iam:UntagRole",
    ]
    resources = ["arn:aws:iam::${local.account_id}:role/cip-${each.key}-*"]
  }

  statement {
    sid       = "PassWorkloadRoles"
    actions   = ["iam:PassRole"]
    resources = ["arn:aws:iam::${local.account_id}:role/cip-${each.key}-*"]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["lambda.amazonaws.com", "states.amazonaws.com", "scheduler.amazonaws.com"]
    }
  }

  statement {
    sid       = "ProtectBoundaries"
    effect    = "Deny"
    actions   = ["iam:DeleteRolePermissionsBoundary", "iam:PutRolePermissionsBoundary", "iam:CreatePolicyVersion", "iam:DeletePolicy"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "deploy" {
  for_each = local.environments
  name     = "cip-${each.key}-deploy"
  role     = aws_iam_role.github[each.key].id
  policy   = data.aws_iam_policy_document.deploy[each.key].json
}
