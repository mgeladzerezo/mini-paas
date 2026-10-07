import base64
from dataclasses import dataclass

from fastapi.testclient import TestClient

from app.adapters import Adapters
from app.adapters.github import Commit, Installation, Repo
from app.services.worker import ReleaseWorker

PASSWORD = "correct-horse-battery"


@dataclass
class Workspace:
    """A signed-up tenant with a linked GitHub installation, plus request helpers."""

    client: TestClient
    adapters: Adapters
    slug: str
    installation_id: int
    headers: dict[str, str]

    @classmethod
    def create(
        cls, client: TestClient, adapters: Adapters, slug: str, installation_id: int
    ) -> "Workspace":
        response = client.post(
            "/api/auth/signup",
            json={
                "email": f"owner@{slug}.example.com",
                "password": PASSWORD,
                "tenant_slug": slug,
                "tenant_name": slug.title(),
            },
        )
        assert response.status_code == 201, response.text
        headers = {"Authorization": f"Bearer {response.json()['access_token']}"}

        code = f"oauth-code-{slug}"
        adapters.github.installations_by_code[code] = [Installation(installation_id, slug)]
        adapters.github.repos[installation_id] = [Repo(f"{slug}/web", "main", True)]
        response = client.post(
            "/api/github/installations",
            json={"installation_id": installation_id, "code": code},
            headers=headers,
        )
        assert response.status_code == 201, response.text
        return cls(client, adapters, slug, installation_id, headers)

    @property
    def namespace(self) -> str:
        return f"tenant-{self.slug}"

    @property
    def repo(self) -> str:
        return f"{self.slug}/web"

    def request(self, method: str, path: str, **kwargs):
        return self.client.request(method, f"/api{path}", headers=self.headers, **kwargs)

    def create_app(self, name: str = "web", **overrides) -> dict:
        body = {
            "name": name,
            "installation_id": self.installation_id,
            "repo_full_name": self.repo,
            "branch": "main",
            **overrides,
        }
        response = self.request("POST", "/apps", json=body)
        assert response.status_code == 201, response.text
        return response.json()

    def set_env(self, app_id: int, variables: dict[str, str], secret: bool = False) -> None:
        body = {
            "variables": [
                {"key": key, "value": value, "is_secret": secret}
                for key, value in variables.items()
            ]
        }
        response = self.request("PUT", f"/apps/{app_id}/env", json=body)
        assert response.status_code == 200, response.text

    def deploy(self, app_id: int, sha: str, reuse_image: bool = False) -> dict:
        self.adapters.github.heads[(self.repo, "main")] = Commit(
            sha.ljust(40, "0"), f"commit {sha}"
        )
        response = self.request(
            "POST", f"/apps/{app_id}/releases", json={"reuse_image": reuse_image}
        )
        assert response.status_code == 201, response.text
        return response.json()

    def release(self, app_id: int, release_id: int) -> dict:
        response = self.request("GET", f"/apps/{app_id}/releases/{release_id}")
        assert response.status_code == 200, response.text
        return response.json()

    def app(self, app_id: int) -> dict:
        return self.request("GET", f"/apps/{app_id}").json()


def run_until_rollout(
    worker: ReleaseWorker, workspace: Workspace, app_id: int, release_id: int
) -> None:
    """Tick until the release's manifests are applied and it is waiting on the rollout."""
    version = str(workspace.release(app_id, release_id)["version"])
    for _ in range(6):
        worker.tick()
        if _deployment_release(workspace, "web") == version:
            return
    raise AssertionError(f"release {release_id} was never applied")


def _deployment_release(workspace: Workspace, name: str) -> str | None:
    deployment = workspace.adapters.kube.get("apps/v1", "Deployment", workspace.namespace, name)
    if deployment is None:
        return None
    return deployment["spec"]["template"]["metadata"]["annotations"]["paas.dev/release"]


def deploy_healthy(worker: ReleaseWorker, workspace: Workspace, app_id: int, sha: str) -> dict:
    """Deploy a commit and drive it all the way to healthy."""
    release = workspace.deploy(app_id, sha)
    return finish_healthy(worker, workspace, app_id, release["id"])


def finish_healthy(
    worker: ReleaseWorker, workspace: Workspace, app_id: int, release_id: int
) -> dict:
    run_until_rollout(worker, workspace, app_id, release_id)
    workspace.adapters.kube.set_rollout(workspace.namespace, "web", "healthy")
    worker.tick()
    release = workspace.release(app_id, release_id)
    assert release["status"] == "healthy", release
    return release


def live_deployment(workspace: Workspace, name: str = "web") -> dict:
    return workspace.adapters.kube.get("apps/v1", "Deployment", workspace.namespace, name)


def live_image(workspace: Workspace, name: str = "web") -> str:
    return live_deployment(workspace, name)["spec"]["template"]["spec"]["containers"][0]["image"]


def live_env(workspace: Workspace, name: str = "web") -> dict[str, str]:
    """Decode the Secret the live Deployment's pods load their environment from."""
    container = live_deployment(workspace, name)["spec"]["template"]["spec"]["containers"][0]
    secret_name = container["envFrom"][0]["secretRef"]["name"]
    secret = workspace.adapters.kube.get("v1", "Secret", workspace.namespace, secret_name)
    assert secret is not None, f"deployment references missing secret {secret_name}"
    return {key: base64.b64decode(value).decode() for key, value in secret["data"].items()}
