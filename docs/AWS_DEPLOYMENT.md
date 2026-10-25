# Deploy ResolveAI to AWS

This guide takes you from a fresh AWS account to a live deployment that your Telegram bot talks to. You run every command on your Mac, in the VS Code terminal, from the project folder.

**Time:** about 30 minutes the first time (most of it is AWS creating the database and CloudFront).
**Cost:** roughly US$60 to 90 a month while it runs, plus OpenAI usage. Run `make aws-destroy` when you are done showing it, and the AWS cost stops.

## What gets created

```
Telegram ──HTTPS──▶ CloudFront ──▶ Application Load Balancer ──▶ ECS Fargate "api"  (dashboard + REST + webhook)
                    (free HTTPS)   (accepts CloudFront only)    ECS Fargate "worker" (Telegram queue, auto-resolve)
                                                                     │
                                       RDS PostgreSQL 16 + pgvector ◀┤▶ ElastiCache Redis 7
                                       (private subnet)                  (private subnet)
Secrets Manager (keys)   ECR (images)   CloudWatch (logs, alarms)   SNS (alert email)   AWS Budgets
```

All of it is described in `infra/terraform/`. Both containers run on ARM64 (Graviton), which is cheaper and matches your M1 Mac, so the image builds natively.

## Step 1: Install the tools on your Mac

Install [Homebrew](https://brew.sh) if you don't have it, then:

```bash
xcode-select --install                       # provides make, git and curl (skip if already installed)
brew install awscli
brew tap hashicorp/tap && brew install hashicorp/tap/terraform
brew install --cask docker                   # Docker Desktop; open it once and leave it running
```

Check:

```bash
aws --version          # aws-cli/2.x
terraform -version     # Terraform v1.10 or newer
docker info            # must not show an error
```

## Step 2: Create an IAM user for deployments

Do this once, signed in to the AWS Console as the account owner.

1. Open **IAM**, then **Users**, then **Create user**.
2. User name: `resolveai-deployer`. Do not tick console access. Click **Next**.
3. Choose **Attach policies directly** and tick **AdministratorAccess**. Click **Next**, then **Create user**.

   Terraform creates a VPC, IAM roles, a database, a cache, a load balancer, CloudFront, secrets and budgets, so it needs broad rights. For a personal learning account, AdministratorAccess on a dedicated user is the simple and common choice. Do not use the root account's keys.

4. Open the new user, go to **Security credentials**, then **Create access key**.
5. Choose **Command Line Interface (CLI)**, tick the confirmation, click **Next**, then **Create access key**.
6. Copy the **Access key ID** and **Secret access key** (or download the .csv). You cannot see the secret again.

## Step 3: Log in from the terminal

```bash
aws configure
```

Answer the prompts:

```
AWS Access Key ID:     <your access key id>
AWS Secret Access Key: <your secret access key>
Default region name:   ap-south-1
Default output format: json
```

Check it worked:

```bash
aws sts get-caller-identity
```

You should see your account number and `user/resolveai-deployer`.

`ap-south-1` is Mumbai, the closest region for Indian customers. You can choose another region, but use the same one in `terraform.tfvars`.

## Step 4: Fill in the settings

```bash
cp infra/terraform/terraform.tfvars.example infra/terraform/terraform.tfvars
```

Open `infra/terraform/terraform.tfvars` in VS Code:

| Setting | What to put |
|---|---|
| `aws_region` | `ap-south-1` (same as `aws configure`) |
| `llm_provider` | `openai` for real answers, `fake` for a free demo with plain answers |
| `openai_api_key` | Your key from platform.openai.com (starts with `sk-`) |
| `telegram_bot_token` | The token from @BotFather (see [TELEGRAM_SETUP.md](TELEGRAM_SETUP.md)). Leave `""` to deploy without Telegram. |
| `admin_email`, `admin_password` | Your dashboard login. Use a strong password: the site is public. |
| `daily_budget_usd` | Daily OpenAI spend limit. When reached, new questions go to humans instead of the model. |
| `alert_email` | Optional. Your email for error alarms and the monthly AWS bill alert. AWS sends a confirmation email you must click. |
| `monthly_budget_alert_usd` | Optional. Emails you when the forecast AWS bill passes this amount. |

`terraform.tfvars` is in `.gitignore`. Never commit it. The values are stored in AWS Secrets Manager, not in the containers' environment.

## Step 5: Deploy

If the bot is running locally, stop it first so the two don't compete for messages:

```bash
make down
```

Then:

```bash
make aws-deploy
```

What it does, in order:

1. Checks that `aws`, `terraform`, `docker` and `curl` are installed, that Docker is running and that you are logged in to AWS.
2. `terraform init`, then creates the ECR container registry.
3. Builds the image (dashboard plus API) for ARM64, tagged with the current time, and pushes it to ECR.
4. `terraform apply`: creates the network, database, Redis, secrets, ECS services, load balancer, CloudFront, alarms and budget. The first run takes 15 to 25 minutes. Later runs take 3 to 5.
5. Waits for both ECS services to be healthy. On start, the containers run the database migrations, load the demo data and index the help articles.
6. Calls `/ready` on the public URL.
7. Registers the Telegram webhook with a secret token.
8. Prints the dashboard URL, like `https://d1abcd2efgh3.cloudfront.net`.

Open the URL, sign in, and message your bot. Everything in the test table in [TELEGRAM_SETUP.md](TELEGRAM_SETUP.md#step-4-test-the-full-flow) works the same on AWS.

**To deploy a code change**, run `make aws-deploy` again. It builds a new image tag and ECS replaces the containers with no downtime.

## Step 6: Operate it

```bash
make aws-logs                                  # live logs from both containers
make aws-webhook                               # point the bot back at AWS (after local testing) and show webhook status
cd infra/terraform && terraform output         # URLs and names
```

- **Analytics page:** cost per ticket, AI resolution rate, latency, guardrail events and CSAT.
- **CloudWatch:** logs in the `/ecs/resolveai` log group. Alarms fire on 5xx errors from the API and on unhealthy containers.
- **Cost Explorer:** every resource carries the tag `Project = resolveai`. Activate it once under Billing, Cost allocation tags, then filter Cost Explorer by it.

## Step 7: Delete everything

```bash
make aws-destroy
```

This removes the Telegram webhook and deletes every AWS resource the project created, including the database and its data. It takes about 10 to 15 minutes. After it finishes the project costs nothing on AWS. You can redeploy later with `make aws-deploy`.

## Monthly cost (ap-south-1, approximate)

| Resource | Size | Approx. US$ per month |
|---|---|---|
| ECS Fargate, api | 0.5 vCPU, 1 GB, ARM64 | 15 |
| ECS Fargate, worker | 0.25 vCPU, 0.5 GB, ARM64 | 8 |
| RDS PostgreSQL | db.t4g.micro, 20 GB | 16 |
| ElastiCache Redis | cache.t4g.micro | 12 |
| Application Load Balancer | 1 | 18 |
| Public IPv4 addresses | about 4 | 15 |
| CloudFront, Secrets Manager, CloudWatch, ECR | low traffic | 2 to 5 |
| **Total** | | **about 60 to 90** |

There is no NAT gateway: containers run in public subnets with security groups that allow inbound traffic only from the load balancer, and the load balancer only accepts CloudFront. That saves about US$35 a month. RDS and ElastiCache can be covered by the AWS free tier on a new account.

## Troubleshooting

| Problem | Fix |
|---|---|
| `AWS CLI is not logged in` | Run `aws configure` again and check `aws sts get-caller-identity`. |
| `Docker Desktop is not running` | Open Docker Desktop and wait for it to say "Engine running". |
| `AccessDenied` or `UnauthorizedOperation` | The IAM user is missing AdministratorAccess (step 2). |
| `ECS services did not stabilise` | Run `make aws-logs`. A wrong `DATABASE_URL` or a missing OpenAI key shows up there. Fix `terraform.tfvars` and run `make aws-deploy` again. |
| `/ready` times out but ECS is healthy | CloudFront can take a few minutes after creation. Wait, open the URL in a browser, then run `make aws-webhook`. |
| `OptInRequired` or a service not available | New accounts sometimes need a few hours for activation. Try again later. |
| Bot does not reply on AWS | Run `make aws-webhook` and read `getWebhookInfo`. `last_error_message` tells you why. |
| The OpenAI answers stop and tickets go to humans | The daily budget was reached. Raise `daily_budget_usd` and run `make aws-deploy`. |
| Terraform state lock or a half-finished apply | Run `make aws-deploy` again. Terraform continues from where it stopped. |

## Notes for production

This setup is a sensible starting point, not the end state. For a real client you would add a custom domain with an ACM certificate, a remote Terraform state in S3 with locking, Multi-AZ RDS with longer backups, autoscaling on the API service, AWS WAF in front of CloudFront, and a CI/CD pipeline that deploys on merge using an OIDC role instead of access keys.
