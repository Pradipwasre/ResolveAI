#!/usr/bin/env bash
# Re-register the Telegram webhook with the AWS deployment (for example after running the bot locally).
set -euo pipefail
cd "$(dirname "$0")/../terraform"
TOKEN="$(sed -n 's/^[[:space:]]*telegram_bot_token[[:space:]]*=[[:space:]]*"\(.*\)".*/\1/p' terraform.tfvars | head -1)"
[ -n "$TOKEN" ] || { echo "telegram_bot_token is empty in terraform.tfvars"; exit 1; }
APP_URL="$(terraform output -raw app_url)"
SECRET="$(terraform output -raw telegram_webhook_secret)"
curl -sS "https://api.telegram.org/bot$TOKEN/setWebhook" \
  --data-urlencode "url=$APP_URL/api/telegram/webhook" \
  --data-urlencode "secret_token=$SECRET" \
  --data-urlencode 'allowed_updates=["message","callback_query"]'
echo
curl -sS "https://api.telegram.org/bot$TOKEN/getWebhookInfo"
echo
