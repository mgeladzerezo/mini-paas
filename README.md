# mini-paas

A small multi-tenant platform that deploys applications from Git onto Kubernetes.
Connect a GitHub repository that has a Dockerfile, push to the tracked branch, and the
platform builds an image, rolls it out into the tenant's namespace and serves it at
`https://<app>.<tenant>.<base-domain>`. Releases can be rolled back, environment
variables are managed per app, and custom domains can be attached once ownership is
proven through DNS.

It is a portfolio project. The backend, the manifests it renders and the delivery setup
are the point; the web UI is intentionally thin.

## Architecture

```
 GitHub ──push webhook──▶ API (FastAPI) ──▶ PostgreSQL ◀── worker (one replica)
                           ▲                                 │  tick every 3s
 Browser ──▶ web UI ───────┘                                 ▼
                                             ┌─ Builder ── Kubernetes Job (git clone + Kaniko) ─▶ ECR
                                             ├─ KubeClient ─ server-side apply into tenant-<slug>
                                             ├─ GitHubClient ─ installation tokens, branch heads
                                             └─ DnsResolver ─ TXT / CNAME lookups
```

- **API** (`backend/app/api`): auth, apps, releases, env vars, domains, GitHub webhook.
  It only writes rows; it never builds or deploys inside a request (except re-applying
  an Ingress when a domain is verified or removed, and deleting an app).
- **Worker** (`backend/app/services/worker.py`): a loop that moves each in-flight release
  one step per tick: `pending → building → deploying → healthy | failed`. All state is in
  the database, so it can be restarted at any point.
- **Manifests** (`backend/app/services/manifests.py`): pure functions that render the
  tenant objects and the per-app objects. No I/O, which is what makes them testable.
- **Adapters** (`backend/app/adapters`): four small interfaces with a real and an
  in-memory implementation each.

| Interface | Production | Tests and local dev |
|---|---|---|
| `KubeClient` | `DynamicKubeClient` (official client, server-side apply) | `FakeKube` records applied objects |
| `Builder` | `KanikoJobBuilder` (Job in `paas-builds`, pushes to ECR) | `FakeBuilder` |
| `GitHubClient` | `GitHubAppClient` (httpx, App JWT) | `FakeGitHub` / `DemoGitHub` |
| `DnsResolver` | `SystemDnsResolver` (dnspython) | `FakeDns` |

### What gets rendered

Per tenant, into namespace `tenant-<slug>` (re-applied on every deploy):
Namespace with Pod Security `restricted` labels, ResourceQuota (also forbids
LoadBalancer/NodePort Services), LimitRange, ServiceAccount `workload` with
`automountServiceAccountToken: false` and no RoleBindings, and three NetworkPolicies:
default-deny for ingress and egress, allow ingress from the ingress controller's pods,
allow egress to cluster DNS.

Per app: an immutable Secret per release (`<app>-env-v<N>`), Deployment (non-root UID,
all capabilities dropped, seccomp `RuntimeDefault`, requests and limits, `maxUnavailable: 0`),
ClusterIP Service, HPA, and an Ingress with the `cert-manager.io/cluster-issuer`
annotation and one TLS secret per hostname.

### Release lifecycle

1. A push webhook or "Deploy latest commit" creates a release with a snapshot of the
   app's env vars (encrypted).
2. The worker starts a build, polls it, and stores the image **by digest**.
3. It applies the manifests and watches the Deployment the way `kubectl rollout status`
   does (`observedGeneration`, updated and available replica counts,
   `ProgressDeadlineExceeded`).
4. Healthy: the release becomes current and older env Secrets are pruned.
   Failed or timed out: the release is marked failed and the previous release's
   manifests are re-applied. Because of `maxUnavailable: 0` the old pods were serving the
   whole time.
5. Rollback creates a *new* release that copies the image digest and env snapshot of
   any earlier healthy release and skips the build.

### Custom domains

Adding a domain generates a token. The user creates
`_paas-verify.<domain> TXT paas-verify=<token>` and presses Verify. Only verified
domains are passed to the Ingress renderer (`services/domains.verified_hostnames` is the
single source). A hostname can be verified by one app only. DNS status (CNAME or matching
A records to the ingress) and TLS status (the cert-manager `Certificate`'s Ready
condition) are reported separately.

## Repository layout

```
backend/            FastAPI app, Alembic migration, tests
frontend/           React + TypeScript + Vite UI
deploy/terraform/   VPC, EKS, ECR, IAM (IRSA + GitHub OIDC), RDS, Secrets Manager
deploy/helm/        Platform chart: API, worker, UI, build namespace, RBAC, ExternalSecret
deploy/argocd/      App-of-apps chart: ingress-nginx, cert-manager, ExternalDNS,
                    External Secrets, metrics-server, and the platform chart
scripts/            bootstrap.sh / bootstrap.ps1
docs/               isolation-test.md
.github/workflows/  ci.yml
```

## Running locally

No cluster, registry or GitHub App is needed: `PAAS_ADAPTERS=fake` (the default) swaps
all four adapters for the in-memory ones, and "Install GitHub App" short-circuits to a
demo installation with two pretend repositories. Deployments become healthy after a few
seconds. The fake cluster lives in the API process, so its state is lost on restart, and
TLS status stays `pending` because nothing plays the role of cert-manager.

With Docker:

```sh
docker compose up --build
# UI on http://localhost:5179, API on http://localhost:8109
```

Without Docker (PowerShell shown; SQLite database in `backend/paas.db`):

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\pip install -c constraints.txt -e ".[dev]"
.\.venv\Scripts\alembic upgrade head
$env:PAAS_RUN_WORKER = "true"
.\.venv\Scripts\uvicorn app.asgi:app --port 8109

# second terminal
cd frontend
npm install
npm run dev        # http://localhost:5179, proxies /api to :8109
```

Configuration is read from `PAAS_*` environment variables, see `backend/app/config.py`.
With `PAAS_ADAPTERS=real` the app refuses to start on the built-in development secrets.

## Tests

```sh
cd backend
ruff check . && ruff format --check .
pytest                      # 194 tests, SQLite + in-memory adapters, no network

cd frontend
npm run lint
npm test                    # 30 tests (vitest + Testing Library)
npm run build               # tsc -b, then vite build

cd deploy/terraform
terraform fmt -check && terraform init -backend=false && terraform validate

helm lint deploy/helm/mini-paas --strict -f deploy/helm/mini-paas/ci/lint-values.yaml
helm lint deploy/argocd --strict
```

Where the important properties are tested:

| Property | Test file |
|---|---|
| Tenant isolation objects, non-root workloads with limits, no cross-tenant references | `tests/test_manifests.py` |
| Build → deploy → healthy, failed deploy keeps the previous release, exact rollback | `tests/test_release_lifecycle.py` |
| Cross-tenant access on every route | `tests/test_authorization.py` |
| Domain verification, unverified domains never reach an Ingress | `tests/test_domains.py` |
| Webhook signature verification, redelivery, branch matching | `tests/test_webhooks.py` |
| Kaniko Job spec and status handling (against the fake cluster) | `tests/test_builder.py` |
| GitHub client requests and App JWT (against `httpx.MockTransport`) | `tests/test_github_client.py` |
| Alembic migration matches the models | `tests/test_migrations.py` |

`test_authorization.py` compares its route table with the application's OpenAPI schema,
so adding an endpoint without classifying it fails the suite.

CI (`.github/workflows/ci.yml`) runs all of the above, a Trivy configuration scan of the
repository, builds both images, scans them with Trivy, and on `main` pushes them to ECR
through GitHub OIDC if the role variable is configured.

## Deploying to AWS

```sh
cp deploy/terraform/terraform.tfvars.example deploy/terraform/terraform.tfvars   # edit
TF_STATE_BUCKET=... REPO_URL=https://github.com/you/mini-paas.git ACME_EMAIL=you@example.com \
  ./scripts/bootstrap.sh
```

(`scripts/bootstrap.ps1` takes the same inputs as parameters.) The script runs Terraform,
fetches a kubeconfig, installs Argo CD with Helm and applies one root `Application` that
points at `deploy/argocd`, passing Terraform outputs (role ARNs, registry, database host)
as Helm parameters. Argo CD installs the add-ons in sync wave 0 and the platform chart in
wave 1. Two steps stay manual and are printed at the end: creating the GitHub App and
storing its credentials in Secrets Manager, and setting three GitHub Actions variables.

`docs/isolation-test.md` is the procedure for proving on the running cluster that one
tenant cannot reach another.

## Decisions and trade-offs

- **Polling worker instead of a queue or an operator.** One process, one loop, state in
  PostgreSQL. Easy to reason about and to test deterministically (`worker.tick()` with a
  fake clock). The cost is up to one tick of latency per step and a single-replica worker.
- **Rollback is a new release**, not a pointer move, so history is linear and a rollback
  can itself fail safely or be rolled back.
- **One immutable Secret per release.** Pods of the previous ReplicaSet keep the config
  they started with even if they restart mid-rollout; reverting a failed deploy cannot
  mix old code with new config.
- **Images are referenced by digest** and tagged `<tenant>-<app>-<sha>-r<release>`, so a
  tag never moves and a rollback gets byte-identical image content.
- **Workloads are forced to UID 10001.** This guarantees non-root regardless of the
  Dockerfile, at the price of breaking images that need root-owned paths or ports below
  1024 (the API rejects such ports).
- **Tenant pods have no egress except DNS**, as specified. Apps that call external APIs
  or a database would need an opt-in egress policy; that is not built.
- **Plain Secrets for tenant env vars** (etcd encrypted with a KMS key) rather than an
  ExternalSecret per app. External Secrets is used for the platform's own credentials.
- **404, not 403, for another tenant's resources**, enforced in one dependency
  (`deps.get_owned_app`) that every `/apps/{id}/...` route uses.
- **Claiming a GitHub installation requires the OAuth code** from the install redirect.
  The installation id alone is guessable; the code proves the user can access it.
- **Kaniko on a dedicated, tainted node group** with its own namespace, quota and a
  NetworkPolicy that blocks the VPC and the instance metadata endpoint. Kaniko still runs
  as root inside its container, so the build namespace is `baseline`, not `restricted`.
- **Hand-written Terraform resources** instead of community modules: more lines, but every
  resource is visible and explainable.
- **JWT in `localStorage`** for the UI. Simple, but readable by any script on the page;
  an HttpOnly cookie with CSRF protection would be the stricter choice.

## Known limitations

- Not run against real infrastructure. `DynamicKubeClient`, `SystemDnsResolver`, the
  Dockerfiles, docker-compose stack, Helm charts on a cluster, Terraform `plan`/`apply`,
  the bootstrap scripts and the CI workflow have never been executed; only what the
  "Tests" section lists was run. `KanikoJobBuilder` and `GitHubAppClient` are tested
  against fakes only.
- Kaniko was archived by Google in 2025 and ingress-nginx has been retired upstream.
  Both were requirements here; the `Builder` interface makes a move to rootless BuildKit a
  contained change, and the Ingress renderer is one function, but neither migration is done.
- One worker replica; no leader election. Two workers could start the same build.
- If the first release of an app fails to roll out there is nothing to revert to, so the
  broken Deployment stays until the next deploy.
- While the cluster API is unreachable, in-flight releases stay in `building`/`deploying`
  and are retried; they are not failed.
- Rollback restores the release's image and env snapshot but does not rewrite the env
  editor, which keeps the latest saved values; the next push deploys with those.
- App settings (port, health path, replica range) cannot be edited after creation.
- Build logs are refreshed per poll and stored as one text column; no streaming.
- One user per workspace: no invitations, roles, password reset or rate limiting on login.
- App images are never garbage-collected from ECR.
- The platform's ClusterRole can read and write Secrets in every namespace, because
  tenant namespaces are created dynamically. Scoping it per namespace with generated
  RoleBindings would be tighter.
- GitHub Actions are pinned by tag, not by commit SHA.
- Terraform state contains the generated JWT, webhook and encryption secrets; the state
  bucket must be private and encrypted.
