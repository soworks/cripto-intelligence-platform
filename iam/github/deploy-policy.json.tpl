{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Action": [
        "s3:PutObject",
        "s3:GetObject",
        "s3:DeleteObject"
      ],
      "Effect": "Allow",
      "Resource": "arn:aws:s3:::cip-tfstate-${ACCOUNT_ID}/env/${ENV}/*",
      "Sid": "TerraformState"
    },
    {
      "Action": "s3:ListBucket",
      "Effect": "Allow",
      "Resource": "arn:aws:s3:::cip-tfstate-${ACCOUNT_ID}",
      "Sid": "TerraformStateList"
    },
    {
      "Action": [
        "sts:GetCallerIdentity",
        "states:List*",
        "states:Describe*",
        "ssm:ListTagsForResource",
        "ssm:Describe*",
        "sns:List*",
        "sns:Get*",
        "scheduler:List*",
        "scheduler:Get*",
        "s3:ListAllMyBuckets",
        "s3:GetReplicationConfiguration",
        "s3:GetLifecycleConfiguration",
        "s3:GetEncryptionConfiguration",
        "s3:GetBucket*",
        "s3:GetAccelerateConfiguration",
        "logs:ListTagsLogGroup",
        "logs:ListTagsForResource",
        "logs:Describe*",
        "lambda:List*",
        "lambda:Get*",
        "iam:SimulatePrincipalPolicy",
        "iam:List*",
        "iam:Get*",
        "dynamodb:List*",
        "dynamodb:Describe*",
        "cloudwatch:List*",
        "cloudwatch:Get*",
        "cloudwatch:Describe*"
      ],
      "Effect": "Allow",
      "Resource": "*",
      "Sid": "ReadForPlanning"
    },
    {
      "Action": [
        "logs:UpdateLogDelivery",
        "logs:PutResourcePolicy",
        "logs:ListLogDeliveries",
        "logs:GetLogDelivery",
        "logs:DescribeResourcePolicies",
        "logs:DeleteLogDelivery",
        "logs:CreateLogDelivery"
      ],
      "Effect": "Allow",
      "Resource": "*",
      "Sid": "StepFunctionsLogDelivery"
    },
    {
      "Action": [
        "states:*",
        "ssm:*",
        "sns:*",
        "scheduler:*",
        "s3:*",
        "logs:*",
        "lambda:*",
        "dynamodb:*",
        "cloudwatch:*"
      ],
      "Effect": "Allow",
      "Resource": [
        "arn:aws:states:${REGION}:${ACCOUNT_ID}:stateMachine:cip-${ENV}-*",
        "arn:aws:states:${REGION}:${ACCOUNT_ID}:execution:cip-${ENV}-*",
        "arn:aws:ssm:${REGION}:${ACCOUNT_ID}:parameter/cip/${ENV}/*",
        "arn:aws:sns:${REGION}:${ACCOUNT_ID}:cip-${ENV}-*",
        "arn:aws:scheduler:${REGION}:${ACCOUNT_ID}:schedule/default/cip-${ENV}-*",
        "arn:aws:s3:::cip-${ENV}-*",
        "arn:aws:logs:${REGION}:${ACCOUNT_ID}:log-group:/aws/vendedlogs/states/cip-${ENV}-*",
        "arn:aws:logs:${REGION}:${ACCOUNT_ID}:log-group:/aws/lambda/cip-${ENV}-*",
        "arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:cip-${ENV}-*",
        "arn:aws:dynamodb:${REGION}:${ACCOUNT_ID}:table/cip-${ENV}-*",
        "arn:aws:cloudwatch:${REGION}:${ACCOUNT_ID}:alarm:cip-${ENV}-*",
        "arn:aws:cloudwatch::${ACCOUNT_ID}:dashboard/cip-${ENV}-*"
      ],
      "Sid": "ManageEnvironmentResources"
    },
    {
      "Action": [
        "iam:PutRolePolicy",
        "iam:DetachRolePolicy",
        "iam:DeleteRolePolicy",
        "iam:CreateRole",
        "iam:AttachRolePolicy"
      ],
      "Condition": {
        "StringEquals": {
          "iam:PermissionsBoundary": "arn:aws:iam::${ACCOUNT_ID}:policy/cip-${ENV}-workload-boundary"
        }
      },
      "Effect": "Allow",
      "Resource": "arn:aws:iam::${ACCOUNT_ID}:role/cip-${ENV}-*",
      "Sid": "CreateBoundedWorkloadRoles"
    },
    {
      "Action": [
        "iam:UpdateRoleDescription",
        "iam:UpdateRole",
        "iam:UpdateAssumeRolePolicy",
        "iam:UntagRole",
        "iam:TagRole",
        "iam:DeleteRole"
      ],
      "Effect": "Allow",
      "Resource": "arn:aws:iam::${ACCOUNT_ID}:role/cip-${ENV}-*",
      "Sid": "MaintainWorkloadRoles"
    },
    {
      "Action": "iam:PassRole",
      "Condition": {
        "StringEquals": {
          "iam:PassedToService": [
            "lambda.amazonaws.com",
            "states.amazonaws.com",
            "scheduler.amazonaws.com"
          ]
        }
      },
      "Effect": "Allow",
      "Resource": "arn:aws:iam::${ACCOUNT_ID}:role/cip-${ENV}-*",
      "Sid": "PassWorkloadRoles"
    },
    {
      "Action": [
        "iam:PutRolePermissionsBoundary",
        "iam:DeleteRolePermissionsBoundary",
        "iam:DeletePolicy",
        "iam:CreatePolicyVersion"
      ],
      "Effect": "Deny",
      "Resource": "*",
      "Sid": "ProtectBoundaries"
    }
  ]
}
