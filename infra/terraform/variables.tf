variable "aws_region" {
  description = "AWS region. ap-south-1 is Mumbai."
  type        = string
  default     = "ap-south-1"
}

variable "project" {
  type    = string
  default = "resolveai"
}

variable "image_tag" {
  description = "Docker image tag to run (set by the deploy script)."
  type        = string
  default     = "latest"
}

variable "llm_provider" {
  description = "openai for real models, fake for the offline demo mode."
  type        = string
  default     = "openai"
}

variable "openai_api_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "telegram_bot_token" {
  type      = string
  sensitive = true
  default   = ""
}

variable "admin_email" {
  type    = string
  default = "admin@resolveai.dev"
}

variable "admin_password" {
  description = "Dashboard password for admin_email."
  type        = string
  sensitive   = true
}

variable "daily_budget_usd" {
  description = "Daily LLM spend limit enforced by the app."
  type        = number
  default     = 5
}

variable "monthly_budget_alert_usd" {
  description = "AWS Budgets alert threshold for the whole AWS bill."
  type        = number
  default     = 100
}

variable "alert_email" {
  description = "Email for AWS budget and CloudWatch alarms. Leave empty to skip alerts."
  type        = string
  default     = ""
}

variable "api_desired_count" {
  type    = number
  default = 1
}
