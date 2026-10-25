#!/usr/bin/env bash
# Build the image, push it to ECR, apply Terraform, wait for ECS and register the Telegram webhook.
# Usage: make aws-deploy     (or: bash infra/scripts/deploy.sh)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TF="$ROOT/infra/terraform"
cd "$TF"

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$1"; }
fail() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$1"; exit 1; }
tfvar() { sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*\"\(.*\)\".*/\1/p" terraform.tfvars | head -1; }

step "Checking tools and credentials"
for tool in aws terraform docker curl; do
  command -v "$tool" >/dev/null || fail "$tool is not installed (see docs/AWS_DEPLOYMENT.md, step 1)"
done
docker info >/dev/null 2>&1 || fail "Docker Desktop is not running"
[ -f terraform.tfvars ] || fail "infra/terraform/terraform.tfvars is missing. Copy terraform.tfvars.example and fill it in."
REGION="$(tfvar aws_region)"; REGION="${REGION:-ap-south-1}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)" || fail "AWS CLI is not logged in. Run: aws configure"
echo "AWS account $ACCOUNT, region $REGION"

step "Terraform init"
terraform init -input=false -upgrade >/dev/null

step "Creating the container registry (ECR)"
terraform apply -input=false -auto-approve -target=aws_ecr_repository.app -target=aws_ecr_lifecycle_policy.app >/dev/null
REPO="$(aws ecr describe-repositories --region "$REGION" --repository-names resolveai --query 'repositories[0].repositoryUri' --output text)"
TAG="$(date +%Y%m%d-%H%M%S)"
echo "$TAG" > .image_tag

step "Building the image for ARM64 and pushing $REPO:$TAG"
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${REPO%%/*}" >/dev/null
docker build --platform linux/arm64 -t "$REPO:$TAG" "$ROOT"
docker push "$REPO:$TAG"

step "Creating or updating the AWS infrastructure (first run takes 15 to 25 minutes, mostly RDS and CloudFront)"
terraform apply -input=false -auto-approve -var "image_tag=$TAG"

step "Waiting for the containers to become healthy"
aws ecs wait services-stable --region "$REGION" --cluster resolveai --services api worker \
  || fail "ECS services did not stabilise. Check logs with: make aws-logs"

APP_URL="$(terraform output -raw app_url)"
step "Checking $APP_URL/ready"
for i in $(seq 1 30); do
  if curl -sf "$APP_URL/ready" >/dev/null; then echo "API is ready"; break; fi
  [ "$i" = 30 ] && fail "The app did not answer at $APP_URL/ready. CloudFront can take a few more minutes; run 'make aws-webhook' later."
  sleep 10
done

TOKEN="$(tfvar telegram_bot_token)"
if [ -n "$TOKEN" ]; then
  step "Registering the Telegram webhook"
  SECRET="$(terraform output -raw telegram_webhook_secret)"
  curl -sS "https://api.telegram.org/bot$TOKEN/setWebhook" \
    --data-urlencode "url=$APP_URL/api/telegram/webhook" \
    --data-urlencode "secret_token=$SECRET" \
    --data-urlencode 'allowed_updates=["message","callback_query"]'
  echo
fi

step "Done"
echo "Dashboard: $APP_URL"
echo "Login:     $(tfvar admin_email) / the admin_password from terraform.tfvars"
