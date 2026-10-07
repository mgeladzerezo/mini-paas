# Secrets the platform generates for itself. External Secrets Operator copies them
# into the cluster; nothing reads them from Terraform outputs.
resource "random_password" "jwt" {
  length  = 48
  special = false
}

resource "random_password" "webhook" {
  length  = 40
  special = false
}

resource "random_bytes" "encryption_key" {
  length = 32
}

resource "aws_secretsmanager_secret" "platform" {
  name = "${var.name}/platform"
}

resource "aws_secretsmanager_secret_version" "platform" {
  secret_id = aws_secretsmanager_secret.platform.id
  secret_string = jsonencode({
    jwt_secret     = random_password.jwt.result
    webhook_secret = random_password.webhook.result
    # Fernet keys are URL-safe base64; random_bytes only offers the standard alphabet.
    encryption_key = replace(replace(random_bytes.encryption_key.base64, "+", "-"), "/", "_")
  })
}

# Filled in by hand after creating the GitHub App (app_id, private_key, client_id,
# client_secret); Terraform only owns the container.
resource "aws_secretsmanager_secret" "github_app" {
  name = "${var.name}/github-app"
}
