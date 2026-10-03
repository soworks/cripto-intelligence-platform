{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "TerraformState",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
      "Resource": "arn:aws:s3:::cip-tfstate-${ACCOUNT_ID}/env/${ENV}/*",
      "Condition": {"StringEquals": {"aws:ResourceAccount": "${ACCOUNT_ID}"}}
    },
    {
      "Sid": "TerraformStateList",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::cip-tfstate-${ACCOUNT_ID}",
      "Condition": {"StringEquals": {"aws:ResourceAccount": "${ACCOUNT_ID}"}}
    },
    {
      "Sid": "ListOnlyActionsWithoutResourceScope",
      "Effect": "Allow",
      "Action": ["sts:GetCallerIdentity", "ssm:DescribeParameters", "logs:DescribeLogGroups"],
      "Resource": "*"
    },
    {
      "Sid": "StepFunctionsLogDeliveryWithoutResourceScope",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogDelivery",
        "logs:GetLogDelivery",
        "logs:UpdateLogDelivery",
        "logs:DeleteLogDelivery",
        "logs:ListLogDeliveries",
        "logs:PutResourcePolicy",
        "logs:DescribeResourcePolicies"
      ],
      "Resource": "*"
    },
    {
      "Sid": "ValidateStateMachineDefinitionsWithoutName",
      "Effect": "Allow",
      "Action": "states:ValidateStateMachineDefinition",
      "Resource": "arn:aws:states:${REGION}:${ACCOUNT_ID}:stateMachine:*"
    },
    {
      "Sid": "ReadDefaultEventBus",
      "Effect": "Allow",
      "Action": "events:DescribeEventBus",
      "Resource": "arn:aws:events:${REGION}:${ACCOUNT_ID}:event-bus/default"
    },
    {
      "Sid": "ManageEnvironmentResources",
      "Effect": "Allow",
      "Action": [
        "states:*",
        "ssm:*",
        "sns:*",
        "scheduler:*",
        "logs:*",
        "lambda:*",
        "dynamodb:*",
        "cloudwatch:*"
      ],
      "Resource": [
        "arn:aws:states:${REGION}:${ACCOUNT_ID}:stateMachine:cip-${ENV}-*",
        "arn:aws:states:${REGION}:${ACCOUNT_ID}:execution:cip-${ENV}-*",
        "arn:aws:ssm:${REGION}:${ACCOUNT_ID}:parameter/cip/${ENV}/*",
        "arn:aws:sns:${REGION}:${ACCOUNT_ID}:cip-${ENV}-*",
        "arn:aws:scheduler:${REGION}:${ACCOUNT_ID}:schedule/default/cip-${ENV}-*",
        "arn:aws:logs:${REGION}:${ACCOUNT_ID}:log-group:/aws/vendedlogs/states/cip-${ENV}-*",
        "arn:aws:logs:${REGION}:${ACCOUNT_ID}:log-group:/aws/lambda/cip-${ENV}-*",
        "arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:cip-${ENV}-*",
        "arn:aws:dynamodb:${REGION}:${ACCOUNT_ID}:table/cip-${ENV}-*",
        "arn:aws:cloudwatch:${REGION}:${ACCOUNT_ID}:alarm:cip-${ENV}-*",
        "arn:aws:cloudwatch::${ACCOUNT_ID}:dashboard/cip-${ENV}-*"
      ]
    },
    {
      "Sid": "ManageEnvironmentBuckets",
      "Effect": "Allow",
      "Action": "s3:*",
      "Resource": ["arn:aws:s3:::cip-${ENV}-*", "arn:aws:s3:::cip-${ENV}-*/*"],
      "Condition": {"StringEquals": {"aws:ResourceAccount": "${ACCOUNT_ID}"}}
    },
    {
      "Sid": "CreateEnvironmentBuckets",
      "Effect": "Allow",
      "Action": "s3:CreateBucket",
      "Resource": "arn:aws:s3:::cip-${ENV}-*"
    },
    {
      "Sid": "ReadWorkloadRolesAndBoundary",
      "Effect": "Allow",
      "Action": [
        "iam:GetRole",
        "iam:GetRolePolicy",
        "iam:ListRolePolicies",
        "iam:ListAttachedRolePolicies",
        "iam:ListInstanceProfilesForRole",
        "iam:ListRoleTags",
        "iam:SimulatePrincipalPolicy",
        "iam:GetPolicy",
        "iam:GetPolicyVersion"
      ],
      "Resource": [
        "arn:aws:iam::${ACCOUNT_ID}:role/cip/${ENV}/cip-${ENV}-*",
        "arn:aws:iam::${ACCOUNT_ID}:policy/cip-${ENV}-workload-boundary"
      ]
    },
    {
      "Sid": "CreateBoundedWorkloadRoles",
      "Effect": "Allow",
      "Action": ["iam:CreateRole", "iam:PutRolePolicy", "iam:DeleteRolePolicy"],
      "Resource": "arn:aws:iam::${ACCOUNT_ID}:role/cip/${ENV}/cip-${ENV}-*",
      "Condition": {
        "StringEquals": {
          "iam:PermissionsBoundary": "arn:aws:iam::${ACCOUNT_ID}:policy/cip-${ENV}-workload-boundary"
        }
      }
    },
    {
      "Sid": "MaintainWorkloadRolesOnBoundedPath",
      "Effect": "Allow",
      "Action": [
        "iam:UpdateRole",
        "iam:UpdateRoleDescription",
        "iam:UpdateAssumeRolePolicy",
        "iam:TagRole",
        "iam:UntagRole",
        "iam:DeleteRole"
      ],
      "Resource": "arn:aws:iam::${ACCOUNT_ID}:role/cip/${ENV}/cip-${ENV}-*"
    },
    {
      "Sid": "PassWorkloadRoles",
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": "arn:aws:iam::${ACCOUNT_ID}:role/cip/${ENV}/cip-${ENV}-*",
      "Condition": {
        "StringEquals": {
          "iam:PassedToService": ["lambda.amazonaws.com", "states.amazonaws.com", "scheduler.amazonaws.com"]
        }
      }
    },
    {
      "Sid": "ProtectBoundaries",
      "Effect": "Deny",
      "Action": [
        "iam:PutRolePermissionsBoundary",
        "iam:DeleteRolePermissionsBoundary",
        "iam:DeletePolicy",
        "iam:CreatePolicyVersion",
        "iam:DeletePolicyVersion",
        "iam:SetDefaultPolicyVersion",
        "iam:AttachRolePolicy"
      ],
      "Resource": "*"
    },
    {
      "Sid": "DenyPolicyCreationOutsideEnvironment",
      "Effect": "Deny",
      "Action": ["iam:CreatePolicy", "iam:CreatePolicyVersion"],
      "NotResource": "arn:aws:iam::${ACCOUNT_ID}:policy/cip-${ENV}-*"
    },
    {
      "Sid": "DenyLedgerItemWritesAndDeletion",
      "Effect": "Deny",
      "Action": [
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:DeleteItem",
        "dynamodb:BatchWriteItem",
        "dynamodb:PartiQLInsert",
        "dynamodb:PartiQLUpdate",
        "dynamodb:PartiQLDelete",
        "dynamodb:DeleteTable"
      ],
      "Resource": "arn:aws:dynamodb:${REGION}:${ACCOUNT_ID}:table/cip-${ENV}-ledger"
    },
    {
      "Sid": "DenyFunctionUrls",
      "Effect": "Deny",
      "Action": ["lambda:CreateFunctionUrlConfig", "lambda:UpdateFunctionUrlConfig"],
      "Resource": "*"
    },
    {
      "Sid": "DenyPublicFunctionPermissions",
      "Effect": "Deny",
      "Action": "lambda:AddPermission",
      "Resource": "*",
      "Condition": {"StringEquals": {"lambda:Principal": "*"}}
    },
    {
      "Sid": "DenyPublicBucketExposure",
      "Effect": "Deny",
      "Action": [
        "s3:PutBucketPublicAccessBlock",
        "s3:PutAccountPublicAccessBlock",
        "s3:PutBucketAcl",
        "s3:PutObjectAcl",
        "s3:PutBucketOwnershipControls"
      ],
      "Resource": "*"
    }
  ]
}
