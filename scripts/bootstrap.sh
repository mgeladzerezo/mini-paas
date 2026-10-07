#!/usr/bin/env bash
# Rebuilds the whole platform from an empty AWS account region:
#   infrastructure (Terraform) -> Argo CD (Helm) -> everything else (Argo CD, from Git).
# Safe to re-run; every step is idempotent.
#
# Required:
#   TF_STATE_BUCKET  existing S3 bucket for Terraform state
#   REPO_URL         Git URL Argo CD pulls this repository from
#   ACME_EMAIL       contact address for Let's Encrypt
#   deploy/terraform/terraform.tfvars (copy terraform.tfvars.example)
# Optional:
#   IMAGE_TAG        platform image tag to deploy (default: current commit, as pushed by CI)
#   TARGET_REVISION  Git revision Argo CD tracks (default: main)
set -euo pipefail

: "${TF_STATE_BUCKET:?set TF_STATE_BUCKET}"
: "${REPO_URL:?set REPO_URL}"
: "${ACME_EMAIL:?set ACME_EMAIL}"

ARGOCD_CHART_VERSION="10.9.6"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TF_DIR="$ROOT/deploy/terraform"
IMAGE_TAG="${IMAGE_TAG:-$(git -C "$ROOT" rev-parse HEAD)}"
TARGET_REVISION="${TARGET_REVISION:-main}"

for tool in aws terraform kubectl helm jq; do
  command -v "$tool" >/dev/null || { echo "missing required tool: $tool" >&2; exit 1; }
done
[ -f "$TF_DIR/terraform.tfvars" ] || { echo "create $TF_DIR/terraform.tfvars first" >&2; exit 1; }

echo "==> 1/4 Terraform: VPC, EKS, ECR, IAM, RDS, secrets"
terraform -chdir="$TF_DIR" init -input=false \
  -backend-config="bucket=$TF_STATE_BUCKET" \
  -backend-config="key=mini-paas/terraform.tfstate" \
  -backend-config="region=$(awk -F'"' '/^region/ {print $2}' "$TF_DIR/terraform.tfvars")"
terraform -chdir="$TF_DIR" apply -input=false -auto-approve

outputs="$(terraform -chdir="$TF_DIR" output -json)"
out() { jq -r "$1" <<<"$outputs"; }
region="$(out .region.value)"
cluster="$(out .cluster_name.value)"
zone="$(out .dns_zone_name.value)"

echo "==> 2/4 kubeconfig for $cluster"
aws eks update-kubeconfig --region "$region" --name "$cluster"

echo "==> 3/4 Argo CD"
helm upgrade --install argocd argo-cd \
  --repo https://argoproj.github.io/argo-helm --version "$ARGOCD_CHART_VERSION" \
  --namespace argocd --create-namespace --wait

echo "==> 4/4 Root application (add-ons and the platform sync from Git)"
kubectl apply -f - <<EOF
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: root
  namespace: argocd
spec:
  project: default
  source:
    repoURL: $REPO_URL
    targetRevision: $TARGET_REVISION
    path: deploy/argocd
    helm:
      parameters:
        - { name: repoURL, value: "$REPO_URL" }
        - { name: targetRevision, value: "$TARGET_REVISION" }
        - { name: region, value: "$region" }
        - { name: dnsZone, value: "$zone" }
        - { name: host, value: "$zone" }
        - { name: baseDomain, value: "apps.$zone" }
        - { name: acmeEmail, value: "$ACME_EMAIL" }
        - { name: imageRegistry, value: "$(out .ecr_registry.value)" }
        - { name: imageTag, value: "$IMAGE_TAG", forceString: true }
        - { name: appsRepository, value: "$(out .apps_repository_url.value)" }
        - { name: dbHost, value: "$(out .db_address.value)" }
        - { name: dbSecretName, value: "$(out .db_secret_name.value)" }
        - { name: roleArns.builder, value: "$(out '.irsa_role_arns.value.builder')" }
        - { name: roleArns.externalDns, value: "$(out '.irsa_role_arns.value["external-dns"]')" }
        - { name: roleArns.externalSecrets, value: "$(out '.irsa_role_arns.value["external-secrets"]')" }
  destination:
    server: https://kubernetes.default.svc
    namespace: argocd
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
EOF

cat <<EOF

Done. Argo CD is now syncing; watch it with:
  kubectl -n argocd get applications

One-time steps that cannot be automated from here:
  1. Create the GitHub App (webhook URL https://$zone/api/webhooks/github, setup URL
     https://$zone/github/callback, "Request user authorization during installation" on)
     and store app_id, private_key, client_id and client_secret as JSON in the
     Secrets Manager secret mini-paas/github-app.
     Webhook secret: $(out .webhook_secret_location.value)
  2. Set the GitHub Actions variables AWS_CI_ROLE_ARN=$(out .ci_role_arn.value),
     ECR_REGISTRY=$(out .ecr_registry.value) and AWS_REGION=$region so CI can push images.
EOF
