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

variable "capture_enabled" {
  type        = bool
  default     = false
  description = "Create the evidence-capture Lambda. This is not the DecisionRecord workload."
}

variable "capture_schedule_enabled" {
  type        = bool
  default     = false
  description = "Enable the hourly evidence-capture schedule after the role is capture-only."
}
