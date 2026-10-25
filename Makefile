# ResolveAI: common commands. Run `make help` to list them.
.DEFAULT_GOAL := help
COMPOSE := docker compose

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup: ## Create .env from the template (first time only)
	@test -f .env || (cp .env.example .env && echo "Created .env - open it and add your keys")

up: setup ## Build and start everything locally, then open http://localhost:8000
	$(COMPOSE) up --build -d
	@echo "Waiting for the API..."
	@until curl -sf http://localhost:8000/ready >/dev/null; do sleep 2; done
	@echo "ResolveAI is running: http://localhost:8000  (login from ADMIN_EMAIL / ADMIN_PASSWORD in .env)"

down: ## Stop the local stack (data is kept)
	$(COMPOSE) down

reset: ## Stop and delete local data (database and Redis)
	$(COMPOSE) down -v

logs: ## Follow API and worker logs
	$(COMPOSE) logs -f api worker

dev-deps: ## Install backend and dashboard dependencies on your Mac (for tests and editing)
	cd backend && uv sync
	cd dashboard && npm ci

test: ## Run backend tests (needs `make up` running for Postgres and Redis)
	cd backend && uv run pytest -q

lint: ## Lint and type-check backend and dashboard
	cd backend && uv run ruff check app tests && uv run ruff format --check app tests
	cd dashboard && npx tsc --noEmit

evals: ## Run the evaluation suite inside the running API container
	$(COMPOSE) exec api python -m app.evals

dashboard-dev: ## Run the dashboard with hot reload on http://localhost:3000 (API must be up)
	cd dashboard && NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev

aws-deploy: ## Build, push and deploy to AWS (see docs/AWS_DEPLOYMENT.md)
	bash infra/scripts/deploy.sh

aws-webhook: ## Point the Telegram bot back at the AWS deployment
	bash infra/scripts/webhook.sh

aws-logs: ## Tail the AWS container logs
	aws logs tail /ecs/resolveai --follow --region $$(cd infra/terraform && terraform output -raw aws_region)

aws-destroy: ## Delete all AWS resources (stops all AWS costs for this project)
	bash infra/scripts/destroy.sh

.PHONY: help setup up down reset logs dev-deps test lint evals dashboard-dev aws-deploy aws-webhook aws-logs aws-destroy
