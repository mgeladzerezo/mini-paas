data "aws_route53_zone" "this" {
  name         = var.dns_zone_name
  private_zone = false
}

locals {
  oidc_issuer = replace(aws_iam_openid_connect_provider.cluster.url, "https://", "")

  # IAM roles for service accounts: role name => the one ServiceAccount allowed to assume it.
  # The platform API and worker are deliberately absent: they talk to Kubernetes,
  # GitHub and PostgreSQL only and need no AWS permissions.
  irsa_roles = {
    builder          = { namespace = "paas-builds", service_account = "builder" }
    external-dns     = { namespace = "external-dns", service_account = "external-dns" }
    external-secrets = { namespace = "external-secrets", service_account = "external-secrets" }
  }
}

data "aws_iam_policy_document" "irsa_assume" {
  for_each = local.irsa_roles

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.cluster.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_issuer}:sub"
      values   = ["system:serviceaccount:${each.value.namespace}:${each.value.service_account}"]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_issuer}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "irsa" {
  for_each = local.irsa_roles

  name               = "${var.name}-${each.key}"
  assume_role_policy = data.aws_iam_policy_document.irsa_assume[each.key].json
}

# Build jobs may push to the tenant image repository and nothing else.
data "aws_iam_policy_document" "builder" {
  statement {
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
    ]
    resources = [aws_ecr_repository.apps.arn]
  }
}

data "aws_iam_policy_document" "external_dns" {
  statement {
    actions   = ["route53:ChangeResourceRecordSets"]
    resources = [data.aws_route53_zone.this.arn]
  }

  statement {
    actions   = ["route53:ListHostedZones", "route53:ListResourceRecordSets"]
    resources = ["*"]
  }
}

data "aws_iam_policy_document" "external_secrets" {
  statement {
    actions = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = [
      aws_secretsmanager_secret.platform.arn,
      aws_secretsmanager_secret.github_app.arn,
      aws_db_instance.this.master_user_secret[0].secret_arn,
    ]
  }
}

locals {
  irsa_policies = {
    builder          = data.aws_iam_policy_document.builder.json
    external-dns     = data.aws_iam_policy_document.external_dns.json
    external-secrets = data.aws_iam_policy_document.external_secrets.json
  }
}

resource "aws_iam_role_policy" "irsa" {
  for_each = local.irsa_policies

  name   = each.key
  role   = aws_iam_role.irsa[each.key].id
  policy = each.value
}

# GitHub Actions pushes the platform's own images using OIDC, so the repository
# holds no long-lived AWS credentials.
resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

data "aws_iam_policy_document" "ci_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:ref:refs/heads/main"]
    }
  }
}

data "aws_iam_policy_document" "ci" {
  statement {
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
    ]
    resources = [for repository in aws_ecr_repository.platform : repository.arn]
  }
}

resource "aws_iam_role" "ci" {
  name               = "${var.name}-ci"
  assume_role_policy = data.aws_iam_policy_document.ci_assume.json
}

resource "aws_iam_role_policy" "ci" {
  name   = "push-platform-images"
  role   = aws_iam_role.ci.id
  policy = data.aws_iam_policy_document.ci.json
}
