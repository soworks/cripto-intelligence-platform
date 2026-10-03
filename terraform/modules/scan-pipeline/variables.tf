variable "env" {
  type = string
}

variable "artifact_path" {
  type = string
}

variable "ledger_table_name" {
  type = string
}

variable "ledger_table_arn" {
  type = string
}

variable "flags_prefix" {
  type = string
}

variable "alarm_topic_arn" {
  type = string
}

variable "permissions_boundary_arn" {
  type = string
}

variable "schedule_expression" {
  type    = string
  default = "rate(1 hour)"
}

variable "schedule_enabled" {
  type    = bool
  default = true
}

variable "log_retention_days" {
  type    = number
  default = 14
}
