output "region" {
  value = var.region
}

output "cluster_name" {
  value = aws_eks_cluster.this.name
}

output "dns_zone_name" {
  value = var.dns_zone_name
}

output "ecr_registry" {
  value = split("/", aws_ecr_repository.apps.repository_url)[0]
}

output "apps_repository_url" {
  value = aws_ecr_repository.apps.repository_url
}

output "platform_repository_urls" {
  value = { for name, repository in aws_ecr_repository.platform : name => repository.repository_url }
}

output "irsa_role_arns" {
  value = { for name, role in aws_iam_role.irsa : name => role.arn }
}

output "ci_role_arn" {
  value = aws_iam_role.ci.arn
}

output "db_address" {
  value = aws_db_instance.this.address
}

output "db_secret_name" {
  description = "Secrets Manager secret holding the RDS master username and password."
  value       = aws_db_instance.this.master_user_secret[0].secret_arn
}

output "webhook_secret_location" {
  description = "Where to find the value to paste into the GitHub App's webhook secret field."
  value       = "${aws_secretsmanager_secret.platform.name} (key: webhook_secret)"
}
