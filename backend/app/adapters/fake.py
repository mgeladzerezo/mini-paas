"""In-memory adapters.

They back the test suite and `PAAS_ADAPTERS=fake`, which lets the API and UI run
end to end on a laptop with no cluster, registry or GitHub App.
"""

import copy
import hashlib
import threading

from app.adapters.builder import BuildRequest, BuildState, BuildStatus, image_tag
from app.adapters.github import Commit, GitHubError, Installation, Repo

ObjectKey = tuple[str, str, str | None, str]


def object_key(manifest: dict) -> ObjectKey:
    metadata = manifest["metadata"]
    return (manifest["apiVersion"], manifest["kind"], metadata.get("namespace"), metadata["name"])


class FakeKube:
    def __init__(self, auto_ready: bool = False) -> None:
        self.objects: dict[ObjectKey, dict] = {}
        self.applied: list[dict] = []
        self.deleted: list[ObjectKey] = []
        self.logs: dict[tuple[str, str, str], str] = {}
        self.auto_ready = auto_ready
        self._lock = threading.Lock()

    def apply(self, manifest: dict) -> None:
        obj = copy.deepcopy(manifest)
        key = object_key(obj)
        with self._lock:
            existing = self.objects.get(key)
            generation = existing["metadata"]["generation"] if existing else 0
            if existing is None or existing.get("spec") != obj.get("spec"):
                generation += 1
            obj["metadata"]["generation"] = generation
            if existing and "status" in existing:
                obj["status"] = existing["status"]
            self.objects[key] = obj
            self.applied.append(copy.deepcopy(manifest))
        if self.auto_ready and obj["kind"] == "Deployment":
            self.set_rollout(key[2], key[3], "healthy")

    def get(self, api_version: str, kind: str, namespace: str | None, name: str) -> dict | None:
        with self._lock:
            return copy.deepcopy(self.objects.get((api_version, kind, namespace, name)))

    def list_by_label(
        self, api_version: str, kind: str, namespace: str, label_selector: str
    ) -> list[dict]:
        wanted = dict(pair.split("=", 1) for pair in label_selector.split(",") if pair)
        with self._lock:
            return [
                copy.deepcopy(obj)
                for (obj_api, obj_kind, obj_ns, _), obj in self.objects.items()
                if (obj_api, obj_kind, obj_ns) == (api_version, kind, namespace)
                and wanted.items() <= obj["metadata"].get("labels", {}).items()
            ]

    def delete(self, api_version: str, kind: str, namespace: str | None, name: str) -> None:
        key = (api_version, kind, namespace, name)
        with self._lock:
            self.objects.pop(key, None)
            self.deleted.append(key)

    def pod_logs(self, namespace: str, pod_name: str, container: str) -> str:
        return self.logs.get((namespace, pod_name, container), "")

    def kinds_in(self, namespace: str | None) -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        for _, kind, obj_ns, name in self.objects:
            if obj_ns == namespace:
                found.setdefault(kind, []).append(name)
        return found

    def set_rollout(self, namespace: str | None, name: str, state: str) -> None:
        """Write the Deployment status the real controller would report for `state`."""
        with self._lock:
            deployment = self.objects[("apps/v1", "Deployment", namespace, name)]
            desired = deployment["spec"].get("replicas", 1)
            status = {
                "observedGeneration": deployment["metadata"]["generation"],
                "replicas": desired,
                "updatedReplicas": desired,
                "availableReplicas": desired,
                "conditions": [],
            }
            if state != "healthy":
                # Mid-rollout: one surge pod with the new template that is not available.
                status.update(replicas=desired + 1, updatedReplicas=1)
            if state == "failed":
                status["conditions"] = [
                    {"type": "Progressing", "status": "False", "reason": "ProgressDeadlineExceeded"}
                ]
            deployment["status"] = status


class FakeBuilder:
    def __init__(self, registry_repository: str, polls_until_done: int = 1) -> None:
        self.requests: list[BuildRequest] = []
        self.cleaned: list[str] = []
        self.fail_commits: set[str] = set()
        self._repository = registry_repository
        self._polls_until_done = polls_until_done
        self._builds: dict[str, BuildRequest] = {}
        self._polls: dict[str, int] = {}

    def start(self, request: BuildRequest) -> str:
        ref = f"fake-build-{request.release_id}"
        self.requests.append(request)
        self._builds[ref] = request
        self._polls[ref] = 0
        return ref

    def status(self, build_ref: str) -> BuildStatus:
        request = self._builds.get(build_ref)
        if request is None:
            return BuildStatus(BuildState.FAILED, logs="build not found")

        self._polls[build_ref] += 1
        logs = f"checked out {request.repo_full_name}@{request.commit_sha}\n"
        if self._polls[build_ref] < self._polls_until_done:
            return BuildStatus(BuildState.RUNNING, logs=logs + "building image...")
        if request.commit_sha in self.fail_commits:
            return BuildStatus(BuildState.FAILED, logs=logs + "error: Dockerfile build failed")

        image = self.image_for(request)
        return BuildStatus(BuildState.SUCCEEDED, logs=logs + f"pushed {image}", image=image)

    def cleanup(self, build_ref: str) -> None:
        self.cleaned.append(build_ref)

    def image_for(self, request: BuildRequest) -> str:
        digest = hashlib.sha256(image_tag(request).encode()).hexdigest()
        return f"{self._repository}@sha256:{digest}"


class FakeGitHub:
    def __init__(self) -> None:
        self.installations_by_code: dict[str, list[Installation]] = {}
        self.repos: dict[int, list[Repo]] = {}
        self.heads: dict[tuple[str, str], Commit] = {}

    def installations_for_code(self, code: str) -> list[Installation]:
        if code not in self.installations_by_code:
            raise GitHubError("GitHub rejected the authorization code")
        return self.installations_by_code[code]

    def list_repos(self, installation_id: int) -> list[Repo]:
        return self.repos.get(installation_id, [])

    def branch_head(self, installation_id: int, repo_full_name: str, branch: str) -> Commit:
        known = self.heads.get((repo_full_name, branch))
        if known:
            return known
        sha = hashlib.sha1(f"{repo_full_name}:{branch}".encode()).hexdigest()
        return Commit(sha=sha, message=f"Latest commit on {branch}")

    def clone_token(self, installation_id: int) -> str:
        return f"fake-token-{installation_id}"


class DemoGitHub(FakeGitHub):
    """Used by `PAAS_ADAPTERS=fake`: every workspace gets its own pretend installation."""

    DEMO_REPOS = [
        Repo("demo-org/hello-web", "main", False),
        Repo("demo-org/orders-api", "main", True),
    ]

    def installations_for_code(self, code: str) -> list[Installation]:
        prefix, _, installation_id = code.partition("-")
        if prefix != "dev" or not installation_id.isdigit():
            raise GitHubError("GitHub rejected the authorization code")
        return [Installation(int(installation_id), "demo-org")]

    def list_repos(self, installation_id: int) -> list[Repo]:
        return self.DEMO_REPOS


class FakeDns:
    def __init__(self) -> None:
        self.txt_records: dict[str, list[str]] = {}
        self.cnames: dict[str, str] = {}
        self.address_records: dict[str, list[str]] = {}

    def txt(self, name: str) -> list[str]:
        return self.txt_records.get(name, [])

    def cname(self, name: str) -> str | None:
        return self.cnames.get(name)

    def addresses(self, name: str) -> list[str]:
        return self.address_records.get(name, [])
