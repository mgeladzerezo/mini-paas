import pytest

from app.services.deployer import rollout_state
from tests.helpers import (
    deploy_healthy,
    finish_healthy,
    live_deployment,
    live_env,
    live_image,
    run_until_rollout,
)


def env_secret_names(workspace) -> list[str]:
    secrets = workspace.adapters.kube.list_by_label(
        "v1", "Secret", workspace.namespace, "paas.dev/component=env"
    )
    return sorted(secret["metadata"]["name"] for secret in secrets)


class TestBuildAndDeploy:
    def test_release_moves_through_build_deploy_and_healthy(self, acme, worker):
        app = acme.create_app()
        acme.set_env(app["id"], {"GREETING": "hello"})
        release = acme.deploy(app["id"], "c0ffee")
        assert release["status"] == "pending"
        assert release["version"] == 1

        worker.tick()
        assert acme.release(app["id"], release["id"])["status"] == "building"
        assert live_deployment(acme) is None

        worker.tick()
        built = acme.release(app["id"], release["id"])
        assert built["status"] == "deploying"
        assert built["image"].startswith("registry.test/apps@sha256:")

        worker.tick()
        assert live_image(acme) == built["image"]
        assert live_env(acme) == {"GREETING": "hello"}
        # Applied but not rolled out yet: the app must not be reported as live.
        worker.tick()
        assert acme.release(app["id"], release["id"])["status"] == "deploying"
        assert acme.app(app["id"])["current_release_id"] is None

        acme.adapters.kube.set_rollout(acme.namespace, "web", "healthy")
        worker.tick()
        done = acme.release(app["id"], release["id"])
        assert done["status"] == "healthy"
        assert "checked out acme/web@c0ffee" in done["logs"]
        assert "rollout complete" in done["logs"]
        assert acme.app(app["id"])["current_release_id"] == release["id"]

    def test_build_receives_the_commit_and_a_token_for_the_apps_installation(self, acme, worker):
        app = acme.create_app()
        acme.deploy(app["id"], "c0ffee")
        worker.tick()

        (request,) = acme.adapters.builder.requests
        assert request.repo_full_name == "acme/web"
        assert request.commit_sha.startswith("c0ffee")
        assert request.clone_token == "fake-token-101"
        assert (request.tenant_slug, request.app_name) == ("acme", "web")

    def test_first_deploy_provisions_the_tenant_namespace(self, acme, worker):
        app = acme.create_app()
        deploy_healthy(worker, acme, app["id"], "c0ffee")

        kube = acme.adapters.kube
        assert kube.get("v1", "Namespace", None, "tenant-acme") is not None
        kinds = kube.kinds_in("tenant-acme")
        assert kinds["ResourceQuota"] == ["tenant-quota"]
        assert kinds["LimitRange"] == ["tenant-limits"]
        assert kinds["ServiceAccount"] == ["workload"]
        assert sorted(kinds["NetworkPolicy"]) == [
            "allow-dns",
            "allow-ingress-controller",
            "default-deny",
        ]
        # The namespace and its policies exist before any workload is created in it.
        applied_kinds = [manifest["kind"] for manifest in kube.applied]
        assert applied_kinds.index("NetworkPolicy") < applied_kinds.index("Deployment")
        assert applied_kinds[0] == "Namespace"

    def test_failed_build_never_touches_the_cluster(self, acme, worker):
        app = acme.create_app()
        acme.adapters.builder.fail_commits.add("bad".ljust(40, "0"))
        release = acme.deploy(app["id"], "bad")
        for _ in range(3):
            worker.tick()

        failed = acme.release(app["id"], release["id"])
        assert failed["status"] == "failed"
        assert failed["error"] == "build failed"
        assert "Dockerfile build failed" in failed["logs"]
        assert acme.adapters.kube.applied == []

    def test_build_timeout_fails_the_release(self, acme, worker, clock, settings):
        acme.adapters.builder._polls_until_done = 10**6
        app = acme.create_app()
        release = acme.deploy(app["id"], "slow")
        worker.tick()
        worker.tick()
        assert acme.release(app["id"], release["id"])["status"] == "building"

        clock.advance(settings.build_timeout_seconds + 1)
        worker.tick()
        failed = acme.release(app["id"], release["id"])
        assert (failed["status"], failed["error"]) == ("failed", "build timed out")
        assert acme.adapters.builder.cleaned == [f"fake-build-{release['id']}"]

    def test_releases_of_one_app_run_one_at_a_time(self, acme, worker):
        app = acme.create_app()
        first = acme.deploy(app["id"], "aaa")
        second = acme.deploy(app["id"], "bbb")
        run_until_rollout(worker, acme, app["id"], first["id"])
        worker.tick()

        assert acme.release(app["id"], second["id"])["status"] == "pending"
        finish_healthy(worker, acme, app["id"], first["id"])
        finish_healthy(worker, acme, app["id"], second["id"])
        assert acme.release(app["id"], first["id"])["status"] == "superseded"
        assert acme.app(app["id"])["current_version"] == 2

    def test_config_release_reuses_the_image_with_new_env(self, acme, worker):
        app = acme.create_app()
        acme.set_env(app["id"], {"MODE": "one"})
        first = deploy_healthy(worker, acme, app["id"], "aaa")

        acme.set_env(app["id"], {"MODE": "two"})
        assert live_env(acme) == {"MODE": "one"}, "saving env must not change running config"
        redeploy = acme.deploy(app["id"], "ignored", reuse_image=True)
        assert redeploy["kind"] == "config"
        finish_healthy(worker, acme, app["id"], redeploy["id"])

        assert len(acme.adapters.builder.requests) == 1
        assert live_image(acme) == first["image"]
        assert live_env(acme) == {"MODE": "two"}

    def test_config_release_needs_a_live_release(self, acme):
        app = acme.create_app()
        response = acme.request("POST", f"/apps/{app['id']}/releases", json={"reuse_image": True})
        assert response.status_code == 409


class TestFailedDeploy:
    @pytest.fixture
    def live(self, acme, worker):
        """An app serving v1 with MODE=stable, then a v2 with MODE=broken mid-rollout."""
        app = acme.create_app()
        acme.set_env(app["id"], {"MODE": "stable"})
        v1 = deploy_healthy(worker, acme, app["id"], "aaa")
        acme.set_env(app["id"], {"MODE": "broken"})
        v2 = acme.deploy(app["id"], "bbb")
        run_until_rollout(worker, acme, app["id"], v2["id"])
        v2 = acme.release(app["id"], v2["id"])
        assert live_image(acme) == v2["image"] != v1["image"]
        return app, v1, v2

    def assert_still_serving_v1(self, acme, app, v1, v2):
        assert acme.app(app["id"])["current_release_id"] == v1["id"]
        assert acme.release(app["id"], v1["id"])["status"] == "healthy"
        assert live_image(acme) == v1["image"]
        assert live_env(acme) == {"MODE": "stable"}
        assert env_secret_names(acme) == ["web-env-v1"]

    def test_failed_rollout_leaves_the_previous_release_serving(self, acme, worker, live):
        app, v1, v2 = live
        acme.adapters.kube.set_rollout(acme.namespace, "web", "failed")
        worker.tick()

        failed = acme.release(app["id"], v2["id"])
        assert failed["status"] == "failed"
        assert "rollout failed" in failed["error"]
        assert "still serving v1" in failed["error"]
        assert failed["can_rollback"] is False
        self.assert_still_serving_v1(acme, app, v1, v2)

    def test_rollout_that_never_finishes_times_out_and_reverts(
        self, acme, worker, live, clock, settings
    ):
        app, v1, v2 = live
        acme.adapters.kube.set_rollout(acme.namespace, "web", "progressing")
        worker.tick()
        assert acme.release(app["id"], v2["id"])["status"] == "deploying"

        clock.advance(settings.deploy_timeout_seconds + 1)
        worker.tick()
        failed = acme.release(app["id"], v2["id"])
        assert failed["status"] == "failed"
        assert "rollout timed out" in failed["error"]
        self.assert_still_serving_v1(acme, app, v1, v2)

    def test_stale_status_from_the_previous_rollout_is_not_mistaken_for_success(
        self, acme, worker, live
    ):
        # The fake keeps v1's "all replicas available" status until told otherwise,
        # exactly what the API server returns before the controller observes the new spec.
        app, v1, v2 = live
        for _ in range(3):
            worker.tick()
        assert acme.release(app["id"], v2["id"])["status"] == "deploying"
        assert acme.app(app["id"])["current_release_id"] == v1["id"]

    def test_app_recovers_with_a_later_good_release(self, acme, worker, live):
        app, v1, v2 = live
        acme.adapters.kube.set_rollout(acme.namespace, "web", "failed")
        worker.tick()

        acme.set_env(app["id"], {"MODE": "fixed"})
        v3 = deploy_healthy(worker, acme, app["id"], "ccc")
        assert acme.app(app["id"])["current_release_id"] == v3["id"]
        assert acme.release(app["id"], v1["id"])["status"] == "superseded"
        assert live_env(acme) == {"MODE": "fixed"}

    def test_cluster_errors_while_applying_are_retried(self, acme, worker, monkeypatch):
        app = acme.create_app()
        release = acme.deploy(app["id"], "aaa")
        worker.tick()
        worker.tick()
        assert acme.release(app["id"], release["id"])["status"] == "deploying"

        def api_server_down(manifest):
            raise OSError("connection refused")

        with monkeypatch.context() as patch:
            patch.setattr(acme.adapters.kube, "apply", api_server_down)
            worker.tick()
        assert acme.release(app["id"], release["id"])["status"] == "deploying"

        finish_healthy(worker, acme, app["id"], release["id"])

    def test_build_that_cannot_start_fails_cleanly(self, acme, worker, monkeypatch):
        app = acme.create_app()
        release = acme.deploy(app["id"], "aaa")

        def no_token(installation_id):
            raise RuntimeError("installation suspended")

        monkeypatch.setattr(acme.adapters.github, "clone_token", no_token)
        worker.tick()
        failed = acme.release(app["id"], release["id"])
        assert (failed["status"], failed["error"]) == ("failed", "could not start the build")


class TestRollback:
    @pytest.fixture
    def history(self, acme, worker):
        app = acme.create_app()
        acme.set_env(app["id"], {"MODE": "one", "ONLY_IN_V1": "yes"})
        v1 = deploy_healthy(worker, acme, app["id"], "aaa")
        acme.set_env(app["id"], {"MODE": "two"})
        v2 = deploy_healthy(worker, acme, app["id"], "bbb")
        acme.set_env(app["id"], {"MODE": "three"})
        v3 = deploy_healthy(worker, acme, app["id"], "ccc")
        return app, v1, v2, v3

    def rollback(self, acme, app, target):
        return acme.request("POST", f"/apps/{app['id']}/releases/{target['id']}/rollback")

    def test_rollback_restores_the_exact_image_and_config(self, acme, worker, history):
        app, v1, _, v3 = history
        v1_pod_spec = None
        for manifest in acme.adapters.kube.applied:
            if manifest["kind"] == "Deployment":
                v1_pod_spec = manifest["spec"]["template"]["spec"]
                break

        response = self.rollback(acme, app, v1)
        assert response.status_code == 201
        rollback = response.json()
        assert (rollback["kind"], rollback["version"]) == ("rollback", 4)
        assert rollback["source_release_id"] == v1["id"]
        assert rollback["image"] == v1["image"]

        finish_healthy(worker, acme, app["id"], rollback["id"])
        assert live_image(acme) == v1["image"] != v3["image"]
        assert live_env(acme) == {"MODE": "one", "ONLY_IN_V1": "yes"}
        assert acme.app(app["id"])["current_release_id"] == rollback["id"]
        assert acme.release(app["id"], v3["id"])["status"] == "superseded"

        # Apart from the name of the per-release Secret, the pod spec is v1's, byte for byte.
        restored = live_deployment(acme)["spec"]["template"]["spec"]
        assert restored["containers"][0].pop("envFrom") == [{"secretRef": {"name": "web-env-v4"}}]
        assert v1_pod_spec["containers"][0].pop("envFrom") == [
            {"secretRef": {"name": "web-env-v1"}}
        ]
        assert restored == v1_pod_spec

    def test_rollback_does_not_rebuild(self, acme, worker, history):
        app, v1, _, _ = history
        builds_before = len(acme.adapters.builder.requests)
        rollback = self.rollback(acme, app, v1).json()
        finish_healthy(worker, acme, app["id"], rollback["id"])
        assert len(acme.adapters.builder.requests) == builds_before

    def test_any_earlier_healthy_release_can_be_restored(self, acme, worker, history):
        app, v1, v2, _ = history
        for target, expected_mode in ((v2, "two"), (v1, "one")):
            rollback = self.rollback(acme, app, target).json()
            finish_healthy(worker, acme, app["id"], rollback["id"])
            assert live_image(acme) == target["image"]
            assert live_env(acme)["MODE"] == expected_mode

    def test_rollback_flags_follow_the_live_release(self, acme, history):
        app, v1, v2, v3 = history
        releases = acme.request("GET", f"/apps/{app['id']}/releases").json()
        assert {r["version"]: r["can_rollback"] for r in releases} == {1: True, 2: True, 3: False}

    def test_cannot_roll_back_to_the_live_release(self, acme, history):
        app, _, _, v3 = history
        assert self.rollback(acme, app, v3).status_code == 409

    def test_cannot_roll_back_to_a_release_that_was_never_healthy(self, acme, worker, history):
        app, _, _, _ = history
        acme.adapters.builder.fail_commits.add("bad".ljust(40, "0"))
        failed = acme.deploy(app["id"], "bad")
        for _ in range(3):
            worker.tick()
        assert acme.release(app["id"], failed["id"])["status"] == "failed"
        assert self.rollback(acme, app, failed).status_code == 409

    def test_failed_rollback_keeps_the_current_release(self, acme, worker, history):
        app, v1, _, v3 = history
        rollback = self.rollback(acme, app, v1).json()
        run_until_rollout(worker, acme, app["id"], rollback["id"])
        acme.adapters.kube.set_rollout(acme.namespace, "web", "failed")
        worker.tick()

        assert acme.release(app["id"], rollback["id"])["status"] == "failed"
        assert acme.app(app["id"])["current_release_id"] == v3["id"]
        assert live_image(acme) == v3["image"]
        assert live_env(acme) == {"MODE": "three"}

    def test_only_the_live_releases_secret_is_kept(self, acme, history):
        assert env_secret_names(acme) == ["web-env-v3"]


class TestTenantSeparationInTheCluster:
    def test_two_tenants_with_the_same_app_name_stay_apart(self, acme, globex, worker):
        acme_app = acme.create_app()
        globex_app = globex.create_app()
        acme.set_env(acme_app["id"], {"TOKEN": "acme-secret"}, secret=True)
        globex.set_env(globex_app["id"], {"TOKEN": "globex-secret"}, secret=True)
        acme_release = acme.deploy(acme_app["id"], "aaa")
        globex_release = globex.deploy(globex_app["id"], "bbb")
        for _ in range(4):
            worker.tick()
        for workspace in (acme, globex):
            workspace.adapters.kube.set_rollout(workspace.namespace, "web", "healthy")
        worker.tick()

        assert acme.release(acme_app["id"], acme_release["id"])["status"] == "healthy"
        assert globex.release(globex_app["id"], globex_release["id"])["status"] == "healthy"
        assert live_env(acme) == {"TOKEN": "acme-secret"}
        assert live_env(globex) == {"TOKEN": "globex-secret"}
        assert live_image(acme) != live_image(globex)

        allowed_namespaces = {None, "tenant-acme", "tenant-globex"}
        assert {key[2] for key in acme.adapters.kube.objects} == allowed_namespaces
        for (_, kind, namespace, name), obj in acme.adapters.kube.objects.items():
            if kind == "Namespace":
                continue
            tenant = obj["metadata"]["labels"]["paas.dev/tenant"]
            assert namespace == f"tenant-{tenant}", f"{kind}/{name} is in the wrong namespace"


@pytest.mark.parametrize(
    ("status", "generation", "expected"),
    [
        (None, 1, "progressing"),
        ({"observedGeneration": 1, "replicas": 2, "updatedReplicas": 2}, 1, "progressing"),
        (
            {"observedGeneration": 1, "replicas": 2, "updatedReplicas": 2, "availableReplicas": 2},
            1,
            "healthy",
        ),
        (
            {"observedGeneration": 1, "replicas": 2, "updatedReplicas": 2, "availableReplicas": 2},
            2,
            "progressing",
        ),
        (
            {"observedGeneration": 2, "replicas": 3, "updatedReplicas": 1, "availableReplicas": 2},
            2,
            "progressing",
        ),
        (
            {
                "observedGeneration": 2,
                "replicas": 3,
                "updatedReplicas": 1,
                "availableReplicas": 2,
                "conditions": [
                    {"type": "Progressing", "status": "False", "reason": "ProgressDeadlineExceeded"}
                ],
            },
            2,
            "failed",
        ),
    ],
)
def test_rollout_state(status, generation, expected):
    deployment = {"metadata": {"generation": generation}, "spec": {"replicas": 2}}
    if status is not None:
        deployment["status"] = status
    assert rollout_state(deployment) == expected


def test_rollout_state_of_a_missing_deployment_is_progressing():
    assert rollout_state(None) == "progressing"
