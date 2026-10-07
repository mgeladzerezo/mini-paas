import json

import pytest

from app.adapters.builder import BuildRequest, BuildState, KanikoJobBuilder
from app.adapters.fake import FakeKube

NAMESPACE = "paas-builds"
REQUEST = BuildRequest(
    release_id=42,
    tenant_slug="acme",
    app_name="web",
    repo_full_name="acme/web",
    commit_sha="0123456789abcdef0123456789abcdef01234567",
    clone_token="ghs_installation_token",
)


@pytest.fixture
def kube() -> FakeKube:
    return FakeKube()


@pytest.fixture
def builder(kube) -> KanikoJobBuilder:
    return KanikoJobBuilder(
        kube=kube,
        namespace=NAMESPACE,
        service_account="builder",
        registry_repository="123456789012.dkr.ecr.eu-central-1.amazonaws.com/mini-paas/apps",
        timeout_seconds=900,
    )


def job(kube: FakeKube) -> dict:
    return kube.get("batch/v1", "Job", NAMESPACE, "build-42")


def finish_job(kube: FakeKube, condition: str, digest: str | None = None) -> None:
    kube.objects[("batch/v1", "Job", NAMESPACE, "build-42")]["status"] = {
        "conditions": [{"type": condition, "status": "True"}]
    }
    terminated = {"exitCode": 0 if condition == "Complete" else 1}
    if digest:
        terminated["message"] = digest
    kube.apply(
        {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": "build-42-abcde",
                "namespace": NAMESPACE,
                "labels": {"job-name": "build-42"},
            },
            "status": {
                "containerStatuses": [{"name": "kaniko", "state": {"terminated": terminated}}]
            },
        }
    )
    kube.logs[(NAMESPACE, "build-42-abcde", "clone")] = "checked out acme/web\n"
    kube.logs[(NAMESPACE, "build-42-abcde", "kaniko")] = "INFO Pushing image\n"


def test_start_creates_a_job_in_the_builds_namespace(builder, kube):
    assert builder.start(REQUEST) == "build-42"
    spec = job(kube)["spec"]
    pod = spec["template"]["spec"]

    assert spec["backoffLimit"] == 0
    assert spec["activeDeadlineSeconds"] == 900
    assert pod["restartPolicy"] == "Never"
    assert pod["serviceAccountName"] == "builder"
    destination = (
        "123456789012.dkr.ecr.eu-central-1.amazonaws.com/mini-paas/apps:acme-web-0123456789ab-r42"
    )
    assert f"--destination={destination}" in pod["containers"][0]["args"]
    assert "--digest-file=/dev/termination-log" in pod["containers"][0]["args"]


def test_builds_are_pinned_to_the_dedicated_node_pool(builder, kube):
    builder.start(REQUEST)
    pod = job(kube)["spec"]["template"]["spec"]
    assert pod["nodeSelector"] == {"paas.dev/pool": "builds"}
    assert pod["tolerations"][0]["key"] == "paas.dev/pool"


def test_clone_token_is_passed_by_secret_reference_only(builder, kube):
    builder.start(REQUEST)
    assert "ghs_installation_token" not in json.dumps(job(kube))
    secret = kube.get("v1", "Secret", NAMESPACE, "build-42-git")
    assert secret["stringData"] == {"token": "ghs_installation_token"}
    env = {e["name"]: e for e in job(kube)["spec"]["template"]["spec"]["initContainers"][0]["env"]}
    assert env["GIT_TOKEN"]["valueFrom"]["secretKeyRef"] == {"name": "build-42-git", "key": "token"}


def test_repo_and_commit_reach_the_clone_script_as_env_not_shell_text(builder, kube):
    hostile = BuildRequest(**{**REQUEST.__dict__, "repo_full_name": "acme/web$(reboot)"})
    builder.start(hostile)
    clone = job(kube)["spec"]["template"]["spec"]["initContainers"][0]
    assert "$(reboot)" not in " ".join(clone["command"])
    assert {"name": "REPO", "value": "acme/web$(reboot)"} in clone["env"]


def test_every_build_container_has_resource_limits(builder, kube):
    builder.start(REQUEST)
    pod = job(kube)["spec"]["template"]["spec"]
    for container in pod["initContainers"] + pod["containers"]:
        assert set(container["resources"]["limits"]) == {"cpu", "memory"}


def test_status_is_running_until_the_job_reports_a_condition(builder, kube):
    builder.start(REQUEST)
    assert builder.status("build-42").state == BuildState.RUNNING


def test_successful_build_returns_the_image_pinned_by_digest(builder, kube):
    builder.start(REQUEST)
    digest = "sha256:" + "ab" * 32
    finish_job(kube, "Complete", digest)

    status = builder.status("build-42")
    assert status.state == BuildState.SUCCEEDED
    assert status.image == (
        f"123456789012.dkr.ecr.eu-central-1.amazonaws.com/mini-paas/apps@{digest}"
    )
    assert status.logs == "checked out acme/web\nINFO Pushing image"


def test_completed_job_without_a_digest_is_a_failure(builder, kube):
    builder.start(REQUEST)
    finish_job(kube, "Complete")
    status = builder.status("build-42")
    assert status.state == BuildState.FAILED
    assert "did not report a digest" in status.logs


def test_failed_job_reports_failure_with_logs(builder, kube):
    builder.start(REQUEST)
    finish_job(kube, "Failed")
    status = builder.status("build-42")
    assert status.state == BuildState.FAILED
    assert status.image is None
    assert "INFO Pushing image" in status.logs


def test_missing_job_is_a_failure(builder):
    assert builder.status("build-999").state == BuildState.FAILED


def test_cleanup_deletes_the_token_secret(builder, kube):
    builder.start(REQUEST)
    builder.cleanup("build-42")
    assert kube.get("v1", "Secret", NAMESPACE, "build-42-git") is None
