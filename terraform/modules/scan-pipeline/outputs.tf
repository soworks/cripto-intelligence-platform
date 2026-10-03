output "state_machine_arn" {
  value = aws_sfn_state_machine.scan.arn
}

output "pipeline_lambda_role_arn" {
  value = aws_iam_role.pipeline_lambda.arn
}
