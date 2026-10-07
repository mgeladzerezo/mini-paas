from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.adapters.kube import KubeClient


class BuildState(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class BuildRequest:
    release_id: int
    tenant_slug: str
    app_name: str
    repo_full_name: str
    commit_sha: str
    clone_token: str


@dataclass(frozen=True)
class BuildStatus:
    state: BuildState
    logs: str = ""
    image: str | None = None


class Builder(Protocol):
    def start(self, request: BuildRequest) -> str:
        """Start a build and return a reference used to poll it."""
        ...

    def status(self, build_ref: str) -> BuildStatus: ...

    def cleanup(self, build_ref: str) -> None: ...


def image_tag(request: BuildRequest) -> str:
    # The release id makes every tag unique, so a tag never moves to a newer digest and
    # leaves an older release pointing at an untagged image the registry may expire.
    return (
        f"{request.tenant_slug}-{request.app_name}-{request.commit_sha[:12]}-r{request.release_id}"
    )


CLONE_SCRIPT = """set -eu
cd /workspace
git init -q .
git remote add origin "https://x-access-token:${GIT_TOKEN}@github.com/${REPO}.git"
git fetch -q --depth 1 origin "${COMMIT_SHA}"
git checkout -q FETCH_HEAD
rm -rf .git
echo "checked out ${REPO}@${COMMIT_SHA}"
"""


class KanikoJobBuilder:
    """Builds images with a Kubernetes Job: a git clone init container, then Kaniko.

    Kaniko needs no Docker daemon and no privileged mode. It pushes to ECR with the
    builder ServiceAccount's IAM role and writes the pushed digest to the container's
    termination message, which is how the platform learns the immutable image reference.
    """

    GIT_IMAGE = "alpine/git:2.47.2"
    KANIKO_IMAGE = "gcr.io/kaniko-project/executor:v1.23.2"

    def __init__(
        self,
        kube: KubeClient,
        namespace: str,
        service_account: str,
        registry_repository: str,
        timeout_seconds: int,
    ) -> None:
        self._kube = kube
        self._namespace = namespace
        self._service_account = service_account
        self._repository = registry_repository
        self._timeout_seconds = timeout_seconds

    def start(self, request: BuildRequest) -> str:
        name = f"build-{request.release_id}"
        self._kube.apply(self._token_secret(name, request.clone_token))
        self._kube.apply(self._job(name, request))
        return name

    def status(self, build_ref: str) -> BuildStatus:
        job = self._kube.get("batch/v1", "Job", self._namespace, build_ref)
        if job is None:
            return BuildStatus(BuildState.FAILED, logs="build job not found")

        pods = self._kube.list_by_label("v1", "Pod", self._namespace, f"job-name={build_ref}")
        logs = self._collect_logs(pods)
        conditions = {c["type"]: c["status"] for c in job.get("status", {}).get("conditions") or []}
        if conditions.get("Failed") == "True":
            return BuildStatus(BuildState.FAILED, logs=logs)
        if conditions.get("Complete") != "True":
            return BuildStatus(BuildState.RUNNING, logs=logs)

        destination = job["metadata"]["annotations"]["paas.dev/destination"]
        digest = self._pushed_digest(pods)
        if digest is None:
            return BuildStatus(BuildState.FAILED, logs=logs + "\nbuild did not report a digest")
        repository = destination.rsplit(":", 1)[0]
        return BuildStatus(BuildState.SUCCEEDED, logs=logs, image=f"{repository}@{digest}")

    def cleanup(self, build_ref: str) -> None:
        self._kube.delete("v1", "Secret", self._namespace, f"{build_ref}-git")

    def _collect_logs(self, pods: list[dict]) -> str:
        parts = []
        for pod in pods:
            for container in ("clone", "kaniko"):
                text = self._kube.pod_logs(self._namespace, pod["metadata"]["name"], container)
                if text:
                    parts.append(text.rstrip())
        return "\n".join(parts)

    @staticmethod
    def _pushed_digest(pods: list[dict]) -> str | None:
        for pod in pods:
            for status in pod.get("status", {}).get("containerStatuses") or []:
                terminated = status.get("state", {}).get("terminated") or {}
                message = (terminated.get("message") or "").strip()
                if status.get("name") == "kaniko" and message.startswith("sha256:"):
                    return message
        return None

    def _token_secret(self, job_name: str, token: str) -> dict:
        return {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": f"{job_name}-git", "namespace": self._namespace},
            "type": "Opaque",
            "stringData": {"token": token},
        }

    def _job(self, name: str, request: BuildRequest) -> dict:
        destination = f"{self._repository}:{image_tag(request)}"
        workspace = {"name": "workspace", "mountPath": "/workspace"}
        return {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": name,
                "namespace": self._namespace,
                "labels": {
                    "app.kubernetes.io/managed-by": "mini-paas",
                    "paas.dev/tenant": request.tenant_slug,
                    "paas.dev/app": request.app_name,
                },
                "annotations": {"paas.dev/destination": destination},
            },
            "spec": {
                "backoffLimit": 0,
                "activeDeadlineSeconds": self._timeout_seconds,
                "ttlSecondsAfterFinished": 3600,
                "template": {
                    "metadata": {"labels": {"paas.dev/role": "build"}},
                    "spec": {
                        "restartPolicy": "Never",
                        "serviceAccountName": self._service_account,
                        "nodeSelector": {"paas.dev/pool": "builds"},
                        "tolerations": [
                            {
                                "key": "paas.dev/pool",
                                "operator": "Equal",
                                "value": "builds",
                                "effect": "NoSchedule",
                            }
                        ],
                        "initContainers": [
                            {
                                "name": "clone",
                                "image": self.GIT_IMAGE,
                                "command": ["sh", "-c", CLONE_SCRIPT],
                                # Values reach the script as env vars, never by string
                                # interpolation, so a repo name cannot inject shell.
                                "env": [
                                    {"name": "REPO", "value": request.repo_full_name},
                                    {"name": "COMMIT_SHA", "value": request.commit_sha},
                                    {
                                        "name": "GIT_TOKEN",
                                        "valueFrom": {
                                            "secretKeyRef": {"name": f"{name}-git", "key": "token"}
                                        },
                                    },
                                ],
                                "volumeMounts": [workspace],
                                "resources": {
                                    "requests": {"cpu": "100m", "memory": "128Mi"},
                                    "limits": {"cpu": "500m", "memory": "512Mi"},
                                },
                            }
                        ],
                        "containers": [
                            {
                                "name": "kaniko",
                                "image": self.KANIKO_IMAGE,
                                "args": [
                                    "--context=dir:///workspace",
                                    f"--destination={destination}",
                                    "--digest-file=/dev/termination-log",
                                    "--snapshot-mode=redo",
                                ],
                                "volumeMounts": [workspace],
                                "resources": {
                                    "requests": {"cpu": "500m", "memory": "1Gi"},
                                    "limits": {"cpu": "2", "memory": "4Gi"},
                                },
                            }
                        ],
                        "volumes": [{"name": "workspace", "emptyDir": {"sizeLimit": "5Gi"}}],
                    },
                },
            },
        }
