variable "env" {
  type = string

  validation {
    condition     = contains(["dev", "prod"], var.env)
    error_message = "env is dev or prod."
  }
}

variable "region" {
  type = string
}

variable "alert_email" {
  type      = string
  sensitive = true
}

variable "artifact_path" {
  type = string
}

variable "deletion_protection" {
  type = bool
}

variable "log_retention_days" {
  type = number
}

variable "schedule_enabled" {
  type = bool
}
