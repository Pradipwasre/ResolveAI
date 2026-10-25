# Every secret lives in Secrets Manager; containers read them at start-up.
# Note: values also end up in the local Terraform state file, so keep that file private.

resource "random_password" "jwt" {
  length  = 48
  special = false
}

resource "random_password" "webhook" {
  length  = 48
  special = false
}

locals {
  secrets = {
    DATABASE_URL            = local.database_url
    OPENAI_API_KEY          = var.openai_api_key
    TELEGRAM_BOT_TOKEN      = var.telegram_bot_token
    TELEGRAM_WEBHOOK_SECRET = random_password.webhook.result
    JWT_SECRET              = random_password.jwt.result
    ADMIN_PASSWORD          = var.admin_password
  }
}

locals {
  # for_each needs plain (non-sensitive) keys
  secret_names = toset(["DATABASE_URL", "OPENAI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET", "JWT_SECRET", "ADMIN_PASSWORD"])
}

resource "aws_secretsmanager_secret" "app" {
  for_each                = local.secret_names
  name                    = "${var.project}/${lower(each.key)}"
  recovery_window_in_days = 0 # allows immediate re-create after destroy
}

resource "aws_secretsmanager_secret_version" "app" {
  for_each      = local.secret_names
  secret_id     = aws_secretsmanager_secret.app[each.key].id
  secret_string = local.secrets[each.key] == "" ? "unset" : local.secrets[each.key]
}
