locals {
  untagged_expiry_rule = {
    rulePriority = 1
    description  = "Expire untagged images after a week"
    selection = {
      tagStatus   = "untagged"
      countType   = "sinceImagePushed"
      countUnit   = "days"
      countNumber = 7
    }
    action = { type = "expire" }
  }
}

resource "aws_ecr_repository" "platform" {
  for_each = toset(["api", "frontend"])

  name                 = "${var.name}/${each.key}"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

# Tenant images, tagged "<tenant>-<app>-<commit>-r<release id>". Deployments reference
# them by digest.
resource "aws_ecr_repository" "apps" {
  name                 = "${var.name}/apps"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "platform" {
  for_each = aws_ecr_repository.platform

  repository = each.value.name
  policy = jsonencode({
    rules = [
      local.untagged_expiry_rule,
      {
        rulePriority = 2
        description  = "Keep the 50 most recent images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 50
        }
        action = { type = "expire" }
      },
    ]
  })
}

# Tagged app images are never expired here: any of them may be the live image of an
# app or the target of a rollback, and only the platform's database knows which.
resource "aws_ecr_lifecycle_policy" "apps" {
  repository = aws_ecr_repository.apps.name
  policy     = jsonencode({ rules = [local.untagged_expiry_rule] })
}
