output "app_url" {
  description = "Open this in the browser: dashboard, API and Telegram webhook"
  value       = local.public_url
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "ecs_cluster" {
  value = aws_ecs_cluster.main.name
}

output "aws_region" {
  value = var.aws_region
}

output "log_group" {
  value = aws_cloudwatch_log_group.app.name
}

output "telegram_webhook_secret" {
  value     = random_password.webhook.result
  sensitive = true
}
