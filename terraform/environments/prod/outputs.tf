output "state_machine_arn" {
  value = module.workload.state_machine_arn
}

output "ledger_table_name" {
  value = module.workload.ledger_table_name
}

output "ledger_table_arn" {
  value = module.workload.ledger_table_arn
}

output "pipeline_lambda_role_arn" {
  value = module.workload.pipeline_lambda_role_arn
}

output "market_probe_function_name" {
  value = module.workload.market_probe_function_name
}

output "recorder_function_name" {
  value = module.workload.recorder_function_name
}

output "recorder_schedule_name" {
  value = module.workload.recorder_schedule_name
}

output "data_bucket_name" {
  value = module.workload.data_bucket_name
}
