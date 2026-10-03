terraform {
  backend "s3" {
    bucket       = "cip-tfstate-258485600712"
    key          = "bootstrap/terraform.tfstate"
    region       = "us-east-1"
    profile      = "soworks"
    use_lockfile = true
    encrypt      = true
  }
}
