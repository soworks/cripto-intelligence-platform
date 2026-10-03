output "data_bucket_name" {
  value = aws_s3_bucket.data.id
}

output "ledger_table_name" {
  value = aws_dynamodb_table.ledger.name
}

output "ledger_table_arn" {
  value = aws_dynamodb_table.ledger.arn
}

output "state_table_arn" {
  value = aws_dynamodb_table.state.arn
}

output "counters_table_arn" {
  value = aws_dynamodb_table.counters.arn
}
