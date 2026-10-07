resource "aws_db_subnet_group" "this" {
  name       = var.name
  subnet_ids = [for subnet in aws_subnet.private : subnet.id]
}

resource "aws_security_group" "db" {
  name        = "${var.name}-db"
  description = "PostgreSQL for the platform API"
  vpc_id      = aws_vpc.this.id
}

# EKS puts every managed node (and so every pod, with the VPC CNI) in the cluster
# security group. Tenant pods are kept away from the database by their NetworkPolicy,
# which allows no egress except DNS.
resource "aws_vpc_security_group_ingress_rule" "db_from_cluster" {
  security_group_id            = aws_security_group.db.id
  referenced_security_group_id = aws_eks_cluster.this.vpc_config[0].cluster_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_db_instance" "this" {
  identifier     = var.name
  engine         = "postgres"
  engine_version = "16"
  instance_class = var.db_instance_class

  allocated_storage     = 20
  max_allocated_storage = 100
  storage_encrypted     = true

  db_name  = "paas"
  username = "paas"
  # RDS generates the password and keeps it in Secrets Manager; it never enters state.
  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = false

  backup_retention_period   = 7
  deletion_protection       = true
  skip_final_snapshot       = false
  final_snapshot_identifier = "${var.name}-final"
}
