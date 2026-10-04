variable "region" {
  type    = string
  default = "us-east-1"
}

variable "alert_email" {
  description = "Alert recipient. Set with TF_VAR_alert_email or an untracked tfvars file."
  type        = string
  sensitive   = true
}

variable "monthly_budget_usd" {
  type    = number
  default = 50
}

variable "admin_user_names" {
  description = "IAM users exempt from the state and CloudTrail bucket denies (with the account root)"
  type        = list(string)
  default     = ["asolano"]
}

variable "extra_admin_principal_arns" {
  description = "More admin principal ARNs (ArnLike patterns), e.g. an IAM Identity Center role, exempt from the bucket denies"
  type        = list(string)
  default     = []
}

variable "create_cloudtrail" {
  type    = bool
  default = true
}
