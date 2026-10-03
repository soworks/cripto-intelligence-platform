output "state_bucket" {
  value = aws_s3_bucket.tf_state.id
}

output "workload_boundary_arns" {
  value = { for env, policy in aws_iam_policy.workload_boundary : env => policy.arn }
}
