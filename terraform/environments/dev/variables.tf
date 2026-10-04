variable "region" {
  type    = string
  default = "us-east-1"
}

variable "alert_email" {
  description = "Alert recipient. Set with TF_VAR_alert_email or an untracked tfvars file."
  type        = string
  sensitive   = true
}

variable "artifact_path" {
  type    = string
  default = "../../../build/cip-lambda.zip"
}
