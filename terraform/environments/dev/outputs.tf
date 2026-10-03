output "state_machine_arn" {
  value = module.scan_state_machine.state_machine_arn
}

output "ledger_table_name" {
  value = module.ledger_table.dynamodb_table_id
}

output "ledger_table_arn" {
  value = module.ledger_table.dynamodb_table_arn
}

output "pipeline_lambda_role_arn" {
  value = module.pipeline_lambda_role.arn
}

output "market_probe_function_name" {
  value = module.market_probe_lambda.lambda_function_name
}
