# Offline test of the whole stack with mocked AWS: checks that every resource
# wires together (no AWS account or credentials needed).
# Run: terraform init && terraform test

mock_provider "aws" {
  mock_data "aws_availability_zones" {
    defaults = { names = ["ap-south-1a", "ap-south-1b", "ap-south-1c"] }
  }
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  mock_resource "aws_elasticache_cluster" {
    defaults = { cache_nodes = [{ address = "redis.internal", port = 6379, id = "0001", availability_zone = "ap-south-1a", outpost_arn = "" }] }
  }
  mock_resource "aws_db_instance" {
    defaults = { address = "db.internal" }
  }
  mock_resource "aws_cloudfront_distribution" {
    defaults = { domain_name = "d123example.cloudfront.net" }
  }
  mock_resource "aws_ecr_repository" {
    defaults = { repository_url = "123456789012.dkr.ecr.ap-south-1.amazonaws.com/resolveai" }
  }
  mock_resource "aws_secretsmanager_secret" {
    defaults = { arn = "arn:aws:secretsmanager:ap-south-1:123456789012:secret:resolveai/x" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/resolveai" }
  }
  mock_resource "aws_lb" {
    defaults = { arn = "arn:aws:elasticloadbalancing:ap-south-1:123456789012:loadbalancer/app/resolveai-alb/abc", arn_suffix = "app/resolveai-alb/abc", dns_name = "resolveai-alb.elb.amazonaws.com" }
  }
  mock_resource "aws_ecs_cluster" {
    defaults = { arn = "arn:aws:ecs:ap-south-1:123456789012:cluster/resolveai", id = "arn:aws:ecs:ap-south-1:123456789012:cluster/resolveai" }
  }
  mock_resource "aws_ecs_task_definition" {
    defaults = { arn = "arn:aws:ecs:ap-south-1:123456789012:task-definition/resolveai:1" }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:ap-south-1:123456789012:log-group:/ecs/resolveai" }
  }
  mock_resource "aws_lb_target_group" {
    defaults = { arn = "arn:aws:elasticloadbalancing:ap-south-1:123456789012:targetgroup/resolveai-api/abc", arn_suffix = "targetgroup/resolveai-api/abc" }
  }
  mock_resource "aws_sns_topic" {
    defaults = { arn = "arn:aws:sns:ap-south-1:123456789012:resolveai-alerts" }
  }
}

mock_provider "random" {}

variables {
  admin_password     = "test-password"
  openai_api_key     = "sk-test"
  telegram_bot_token = "123:abc"
  image_tag          = "test"
  alert_email        = "ops@example.com"
}

run "full_stack" {
  command = apply

  assert {
    condition     = output.app_url == "https://d123example.cloudfront.net"
    error_message = "app_url should be the CloudFront HTTPS address"
  }
  assert {
    condition     = local.redis_url == "redis://redis.internal:6379/0"
    error_message = "Redis URL is wrong"
  }
  assert {
    condition     = startswith(local.database_url, "postgresql+asyncpg://resolveai:") && endswith(local.database_url, "@db.internal:5432/resolveai?ssl=require")
    error_message = "Database URL must use asyncpg and TLS"
  }
  assert {
    condition     = length(aws_secretsmanager_secret.app) == 6
    error_message = "Expected 6 secrets"
  }
  assert {
    condition     = jsondecode(aws_ecs_task_definition.worker.container_definitions)[0].command[0] == "scripts/start-worker.sh"
    error_message = "Worker must run the worker script"
  }
  assert {
    condition     = aws_ecs_task_definition.api.runtime_platform[0].cpu_architecture == "ARM64"
    error_message = "Tasks must run on ARM64 to match images built on Apple Silicon"
  }
}
