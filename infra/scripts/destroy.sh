#!/usr/bin/env bash
# Delete everything this project created on AWS, so it stops costing money.
set -euo pipefail
cd "$(dirname "$0")/../terraform"
TOKEN="$(sed -n 's/^[[:space:]]*telegram_bot_token[[:space:]]*=[[:space:]]*"\(.*\)".*/\1/p' terraform.tfvars | head -1)"
if [ -n "$TOKEN" ]; then
  curl -sS "https://api.telegram.org/bot$TOKEN/deleteWebhook" >/dev/null && echo "Telegram webhook removed"
fi
TAG="$(cat .image_tag 2>/dev/null || echo latest)"
terraform destroy -input=false -auto-approve -var "image_tag=$TAG"
echo "All AWS resources for this project are deleted."
