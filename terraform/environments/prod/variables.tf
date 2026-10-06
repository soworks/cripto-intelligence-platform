variable "region" {
  type    = string
  default = "us-east-1"
}

variable "alert_email" {
  description = "Alert recipient. Set with TF_VAR_alert_email or an untracked *.auto.tfvars file."
  type        = string
  sensitive   = true

  validation {
    condition     = can(regex("^[^[:space:]@]+@[^[:space:]@]+[.][^[:space:]@]+$", var.alert_email))
    error_message = "alert_email must be one email address. Set TF_VAR_alert_email or an untracked *.auto.tfvars file."
  }
}

variable "artifact_path" {
  type    = string
  default = "../../../build/cip-lambda.zip"
}
