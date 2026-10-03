{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadEnvironmentState",
      "Effect": "Allow",
      "Action": "s3:GetObject",
      "Resource": [
        "arn:aws:s3:::cip-tfstate-${ACCOUNT_ID}/env/dev/*",
        "arn:aws:s3:::cip-tfstate-${ACCOUNT_ID}/env/prod/*"
      ]
    },
    {
      "Sid": "ListEnvironmentState",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::cip-tfstate-${ACCOUNT_ID}",
      "Condition": {
        "StringLike": {
          "s3:prefix": ["env/dev/*", "env/prod/*"]
        }
      }
    },
    {
      "Sid": "ReadProjectResources",
      "Effect": "Allow",
      "Action": [
        "dynamodb:DescribeTable",
        "dynamodb:DescribeContinuousBackups",
        "dynamodb:DescribeTimeToLive",
        "dynamodb:ListTagsOfResource",
        "lambda:GetFunction",
        "lambda:GetFunctionConfiguration",
        "lambda:GetFunctionCodeSigningConfig",
        "lambda:GetFunctionConcurrency",
        "lambda:GetPolicy",
        "lambda:ListVersionsByFunction",
        "lambda:ListTags",
        "states:DescribeStateMachine",
        "states:ListTagsForResource",
        "scheduler:GetSchedule",
        "sns:GetTopicAttributes",
        "sns:GetSubscriptionAttributes",
        "sns:ListTagsForResource",
        "ssm:GetParameter",
        "ssm:GetParameters",
        "ssm:ListTagsForResource",
        "logs:ListTagsForResource",
        "logs:ListTagsLogGroup",
        "cloudwatch:DescribeAlarms",
        "cloudwatch:ListTagsForResource",
        "cloudwatch:GetDashboard",
        "s3:GetBucket*",
        "s3:GetAccelerateConfiguration",
        "s3:GetEncryptionConfiguration",
        "s3:GetLifecycleConfiguration",
        "s3:GetReplicationConfiguration",
        "iam:GetRole",
        "iam:GetRolePolicy",
        "iam:ListRolePolicies",
        "iam:ListAttachedRolePolicies",
        "iam:GetPolicy",
        "iam:GetPolicyVersion"
      ],
      "Resource": [
        "arn:aws:dynamodb:${REGION}:${ACCOUNT_ID}:table/cip-dev-*",
        "arn:aws:dynamodb:${REGION}:${ACCOUNT_ID}:table/cip-prod-*",
        "arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:cip-dev-*",
        "arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:cip-prod-*",
        "arn:aws:states:${REGION}:${ACCOUNT_ID}:stateMachine:cip-dev-*",
        "arn:aws:states:${REGION}:${ACCOUNT_ID}:stateMachine:cip-prod-*",
        "arn:aws:scheduler:${REGION}:${ACCOUNT_ID}:schedule/default/cip-dev-*",
        "arn:aws:scheduler:${REGION}:${ACCOUNT_ID}:schedule/default/cip-prod-*",
        "arn:aws:sns:${REGION}:${ACCOUNT_ID}:cip-dev-*",
        "arn:aws:sns:${REGION}:${ACCOUNT_ID}:cip-prod-*",
        "arn:aws:ssm:${REGION}:${ACCOUNT_ID}:parameter/cip/*",
        "arn:aws:logs:${REGION}:${ACCOUNT_ID}:log-group:/aws/lambda/cip-dev-*",
        "arn:aws:logs:${REGION}:${ACCOUNT_ID}:log-group:/aws/lambda/cip-prod-*",
        "arn:aws:logs:${REGION}:${ACCOUNT_ID}:log-group:/aws/vendedlogs/states/cip-dev-*",
        "arn:aws:logs:${REGION}:${ACCOUNT_ID}:log-group:/aws/vendedlogs/states/cip-prod-*",
        "arn:aws:cloudwatch:${REGION}:${ACCOUNT_ID}:alarm:cip-dev-*",
        "arn:aws:cloudwatch:${REGION}:${ACCOUNT_ID}:alarm:cip-prod-*",
        "arn:aws:cloudwatch::${ACCOUNT_ID}:dashboard/cip-dev-*",
        "arn:aws:cloudwatch::${ACCOUNT_ID}:dashboard/cip-prod-*",
        "arn:aws:s3:::cip-dev-*",
        "arn:aws:s3:::cip-prod-*",
        "arn:aws:iam::${ACCOUNT_ID}:role/cip-dev-*",
        "arn:aws:iam::${ACCOUNT_ID}:role/cip-prod-*",
        "arn:aws:iam::${ACCOUNT_ID}:policy/cip-dev-workload-boundary",
        "arn:aws:iam::${ACCOUNT_ID}:policy/cip-prod-workload-boundary"
      ]
    },
    {
      "Sid": "ListOnlyActionsWithoutResourceScope",
      "Effect": "Allow",
      "Action": [
        "ssm:DescribeParameters",
        "logs:DescribeLogGroups",
        "sts:GetCallerIdentity"
      ],
      "Resource": "*"
    }
  ]
}
