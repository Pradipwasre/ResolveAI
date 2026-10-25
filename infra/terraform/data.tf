# PostgreSQL 16 on RDS (pgvector is built in) and Redis on ElastiCache.

resource "random_password" "db" {
  length  = 32
  special = false # keeps the connection URL simple
}

resource "aws_db_subnet_group" "main" {
  name       = "${var.project}-db"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_instance" "postgres" {
  identifier              = "${var.project}-db"
  engine                  = "postgres"
  engine_version          = "16"
  instance_class          = "db.t4g.micro"
  allocated_storage       = 20
  storage_type            = "gp3"
  storage_encrypted       = true
  db_name                 = "resolveai"
  username                = "resolveai"
  password                = random_password.db.result
  db_subnet_group_name    = aws_db_subnet_group.main.name
  vpc_security_group_ids  = [aws_security_group.data.id]
  publicly_accessible     = false
  backup_retention_period = 1
  skip_final_snapshot     = true # demo project: no snapshot on destroy
  deletion_protection     = false
  apply_immediately       = true
}

resource "aws_elasticache_subnet_group" "main" {
  name       = "${var.project}-redis"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_elasticache_cluster" "redis" {
  cluster_id         = "${var.project}-redis"
  engine             = "redis"
  engine_version     = "7.1"
  node_type          = "cache.t4g.micro"
  num_cache_nodes    = 1
  port               = 6379
  subnet_group_name  = aws_elasticache_subnet_group.main.name
  security_group_ids = [aws_security_group.data.id]
}

locals {
  # RDS requires TLS; asyncpg takes it as ?ssl=require
  database_url = "postgresql+asyncpg://resolveai:${random_password.db.result}@${aws_db_instance.postgres.address}:5432/resolveai?ssl=require"
  redis_url    = "redis://${aws_elasticache_cluster.redis.cache_nodes[0].address}:6379/0"
}
