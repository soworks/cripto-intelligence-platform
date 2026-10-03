variable "region" {
  type    = string
  default = "us-east-1"
}

variable "alert_email" {
  type = string
}

variable "artifact_path" {
  type    = string
  default = "../../../build/cip-lambda.zip"
}
