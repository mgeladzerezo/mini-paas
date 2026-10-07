<#
Rebuilds the whole platform from an empty AWS account region:
  infrastructure (Terraform) -> Argo CD (Helm) -> everything else (Argo CD, from Git).
Safe to re-run; every step is idempotent. Works in Windows PowerShell 5.1.

Requires deploy/terraform/terraform.tfvars (copy terraform.tfvars.example).

  .\scripts\bootstrap.ps1 -StateBucket my-tf-state -RepoUrl https://github.com/me/mini-paas.git -AcmeEmail me@example.com
#>
param(
    [Parameter(Mandatory)] [string] $StateBucket,
    [Parameter(Mandatory)] [string] $RepoUrl,
    [Parameter(Mandatory)] [string] $AcmeEmail,
    [string] $ImageTag,
    [string] $TargetRevision = "main"
)

$ErrorActionPreference = "Stop"
$ArgoCdChartVersion = "10.9.6"
$Root = Split-Path -Parent $PSScriptRoot
$TfDir = Join-Path $Root "deploy\terraform"

function Invoke-Checked {
    # Native commands do not throw on failure in PowerShell 5.1, so check each one.
    param([string] $Command, [string[]] $Arguments)
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Command exited with code $LASTEXITCODE" }
}

foreach ($tool in "aws", "terraform", "kubectl", "helm", "git") {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "missing required tool: $tool" }
}
$tfvars = Join-Path $TfDir "terraform.tfvars"
if (-not (Test-Path $tfvars)) { throw "create $tfvars first" }
if (-not $ImageTag) { $ImageTag = (& git -C $Root rev-parse HEAD).Trim() }

$stateRegion = (Select-String -Path $tfvars -Pattern '^region\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value

Write-Host "==> 1/4 Terraform: VPC, EKS, ECR, IAM, RDS, secrets"
Invoke-Checked terraform @(
    "-chdir=$TfDir", "init", "-input=false",
    "-backend-config=bucket=$StateBucket",
    "-backend-config=key=mini-paas/terraform.tfstate",
    "-backend-config=region=$stateRegion"
)
Invoke-Checked terraform @("-chdir=$TfDir", "apply", "-input=false", "-auto-approve")

$out = (& terraform "-chdir=$TfDir" output -json) -join "`n" | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "terraform output failed" }
$region = $out.region.value
$zone = $out.dns_zone_name.value
$roles = $out.irsa_role_arns.value

Write-Host "==> 2/4 kubeconfig for $($out.cluster_name.value)"
Invoke-Checked aws @("eks", "update-kubeconfig", "--region", $region, "--name", $out.cluster_name.value)

Write-Host "==> 3/4 Argo CD"
Invoke-Checked helm @(
    "upgrade", "--install", "argocd", "argo-cd",
    "--repo", "https://argoproj.github.io/argo-helm", "--version", $ArgoCdChartVersion,
    "--namespace", "argocd", "--create-namespace", "--wait"
)

Write-Host "==> 4/4 Root application (add-ons and the platform sync from Git)"
$parameters = [ordered]@{
    repoURL                    = $RepoUrl
    targetRevision             = $TargetRevision
    region                     = $region
    dnsZone                    = $zone
    host                       = $zone
    baseDomain                 = "apps.$zone"
    acmeEmail                  = $AcmeEmail
    imageRegistry              = $out.ecr_registry.value
    imageTag                   = $ImageTag
    appsRepository             = $out.apps_repository_url.value
    dbHost                     = $out.db_address.value
    dbSecretName               = $out.db_secret_name.value
    "roleArns.builder"         = $roles.builder
    "roleArns.externalDns"     = $roles."external-dns"
    "roleArns.externalSecrets" = $roles."external-secrets"
}
$parameterLines = foreach ($name in $parameters.Keys) {
    "        - { name: $name, value: `"$($parameters[$name])`", forceString: true }"
}
$manifest = @"
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: root
  namespace: argocd
spec:
  project: default
  source:
    repoURL: $RepoUrl
    targetRevision: $TargetRevision
    path: deploy/argocd
    helm:
      parameters:
$($parameterLines -join "`n")
  destination:
    server: https://kubernetes.default.svc
    namespace: argocd
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
"@
$manifest | & kubectl apply -f -
if ($LASTEXITCODE -ne 0) { throw "kubectl apply failed" }

Write-Host @"

Done. Argo CD is now syncing; watch it with:
  kubectl -n argocd get applications

One-time steps that cannot be automated from here:
  1. Create the GitHub App (webhook URL https://$zone/api/webhooks/github, setup URL
     https://$zone/github/callback, "Request user authorization during installation" on)
     and store app_id, private_key, client_id and client_secret as JSON in the
     Secrets Manager secret mini-paas/github-app.
     Webhook secret: $($out.webhook_secret_location.value)
  2. Set the GitHub Actions variables AWS_CI_ROLE_ARN=$($out.ci_role_arn.value),
     ECR_REGISTRY=$($out.ecr_registry.value) and AWS_REGION=$region so CI can push images.
"@
