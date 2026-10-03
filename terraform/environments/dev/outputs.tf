output "state_machine_arn" {
  value = module.pipeline.state_machine_arn
}

output "ledger_table_name" {
  value = module.data.ledger_table_name
}

output "ledger_table_arn" {
  value = module.data.ledger_table_arn
}

output "pipeline_lambda_role_arn" {
  value = module.pipeline.pipeline_lambda_role_arn
}
