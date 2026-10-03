variable "region" {
  type    = string
  default = "us-east-1"
}

variable "alert_email" {
  type = string
}

variable "monthly_budget_usd" {
  type    = number
  default = 50
}

variable "create_cloudtrail" {
  type    = bool
  default = true
}
