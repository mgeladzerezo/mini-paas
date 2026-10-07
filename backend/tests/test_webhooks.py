import hashlib
import hmac
import json

import pytest

from app.security import verify_github_signature

SECRET = "test-webhook-secret"


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def push_payload(repo: str, installation_id: int, branch: str = "main", sha: str = "d" * 40):
    return {
        "ref": f"refs/heads/{branch}",
        "after": sha,
        "deleted": False,
        "repository": {"full_name": repo},
        "installation": {"id": installation_id},
        "head_commit": {"message": "Fix checkout total\n\nLonger description."},
        "pusher": {"name": "octocat"},
    }


def deliver(client, payload, *, event="push", delivery="delivery-1", signature=None, body=None):
    body = body if body is not None else json.dumps(payload).encode()
    headers = {
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery,
        "Content-Type": "application/json",
    }
    signature = sign(body) if signature is None else signature
    if signature:
        headers["X-Hub-Signature-256"] = signature
    return client.post("/api/webhooks/github", content=body, headers=headers)


def releases(workspace, app_id: int) -> list[dict]:
    return workspace.request("GET", f"/apps/{app_id}/releases").json()


class TestSignatureFunction:
    def test_accepts_githubs_documented_example(self):
        # Test vector from GitHub's "Validating webhook deliveries" documentation.
        assert verify_github_signature(
            "It's a Secret to Everybody",
            b"Hello, World!",
            "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17",
        )

    def test_rejects_a_different_body(self):
        signature = sign(b'{"a": 1}')
        assert not verify_github_signature(SECRET, b'{"a": 2}', signature)

    def test_rejects_a_different_secret(self):
        assert not verify_github_signature(SECRET, b"{}", sign(b"{}", "other-secret"))

    @pytest.mark.parametrize(
        "header",
        [None, "", "sha256=", "sha1=abc", "not-a-signature", "sha256=zz", "sha256=é"],
    )
    def test_rejects_missing_or_malformed_headers(self, header):
        assert not verify_github_signature(SECRET, b"{}", header)

    def test_rejects_the_bare_digest_without_the_scheme_prefix(self):
        assert not verify_github_signature(SECRET, b"{}", sign(b"{}").removeprefix("sha256="))

    def test_an_empty_secret_never_verifies(self):
        assert not verify_github_signature("", b"{}", sign(b"{}", ""))


class TestEndpointRejectsUnsignedDeliveries:
    @pytest.fixture
    def app(self, acme):
        return acme.create_app()

    @pytest.mark.parametrize(
        "signature",
        ["", "sha256=" + "0" * 64, "sha1=" + "0" * 40],
        ids=["missing", "wrong-digest", "wrong-scheme"],
    )
    def test_bad_signature_is_rejected_and_creates_nothing(self, client, acme, app, signature):
        response = deliver(client, push_payload("acme/web", 101), signature=signature)
        assert response.status_code == 401
        assert releases(acme, app["id"]) == []

    def test_signature_from_another_secret_is_rejected(self, client, acme, app):
        body = json.dumps(push_payload("acme/web", 101)).encode()
        response = deliver(client, None, body=body, signature=sign(body, "attacker-secret"))
        assert response.status_code == 401
        assert releases(acme, app["id"]) == []

    def test_body_altered_after_signing_is_rejected(self, client, acme, app):
        signed = json.dumps(push_payload("acme/web", 101, sha="a" * 40)).encode()
        tampered = signed.replace(b"a" * 40, b"b" * 40)
        response = deliver(client, None, body=tampered, signature=sign(signed))
        assert response.status_code == 401
        assert releases(acme, app["id"]) == []

    def test_rejected_delivery_can_be_redelivered_correctly_signed(self, client, acme, app):
        payload = push_payload("acme/web", 101)
        assert deliver(client, payload, signature="sha256=" + "0" * 64).status_code == 401
        assert deliver(client, payload).status_code == 200
        assert len(releases(acme, app["id"])) == 1


class TestPushEvents:
    def test_push_to_the_tracked_branch_creates_a_release(self, client, acme):
        app = acme.create_app()
        acme.set_env(app["id"], {"MODE": "live"})
        response = deliver(client, push_payload("acme/web", 101, sha="d" * 40))

        assert response.json() == {"status": "accepted", "releases": 1}
        (release,) = releases(acme, app["id"])
        assert release["status"] == "pending"
        assert release["commit_sha"] == "d" * 40
        assert release["commit_message"] == "Fix checkout total"
        assert release["triggered_by"] == "octocat"

    def test_pushed_commit_is_what_gets_built(self, client, acme, worker):
        app = acme.create_app()
        deliver(client, push_payload("acme/web", 101, sha="e" * 40))
        worker.tick()
        assert acme.adapters.builder.requests[0].commit_sha == "e" * 40
        assert releases(acme, app["id"])[0]["status"] == "building"

    def test_push_to_another_branch_is_ignored(self, client, acme):
        app = acme.create_app()
        response = deliver(client, push_payload("acme/web", 101, branch="feature/x"))
        assert response.json()["releases"] == 0
        assert releases(acme, app["id"]) == []

    def test_branch_deletion_and_tag_pushes_are_ignored(self, client, acme):
        app = acme.create_app()
        deleted = {**push_payload("acme/web", 101, sha="0" * 40), "deleted": True}
        tag = {**push_payload("acme/web", 101), "ref": "refs/tags/main"}
        assert deliver(client, deleted, delivery="d1").json()["releases"] == 0
        assert deliver(client, tag, delivery="d2").json()["releases"] == 0
        assert releases(acme, app["id"]) == []

    def test_redelivery_does_not_deploy_twice(self, client, acme):
        app = acme.create_app()
        payload = push_payload("acme/web", 101)
        assert deliver(client, payload, delivery="same-id").json()["status"] == "accepted"
        assert deliver(client, payload, delivery="same-id").json()["status"] == "duplicate"
        assert len(releases(acme, app["id"])) == 1

    def test_push_only_deploys_apps_connected_through_the_same_installation(
        self, client, acme, globex
    ):
        """A repo name alone is not enough to trigger another tenant's deploy."""
        acme_app = acme.create_app()
        # Globex's installation is (legitimately or not) granted a repo called acme/web.
        globex.adapters.github.repos[202].append(
            globex.adapters.github.repos[101][0],
        )
        globex_app = globex.create_app(name="mirror", repo_full_name="acme/web")

        response = deliver(client, push_payload("acme/web", installation_id=202))
        assert response.json()["releases"] == 1
        assert releases(acme, acme_app["id"]) == []
        assert len(releases(globex, globex_app["id"])) == 1

    def test_push_for_an_unknown_repo_is_accepted_and_ignored(self, client, acme):
        response = deliver(client, push_payload("nobody/nothing", 999))
        assert response.status_code == 200
        assert response.json()["releases"] == 0


class TestOtherEvents:
    def test_ping_is_acknowledged(self, client):
        response = deliver(client, {"zen": "Keep it logically awesome."}, event="ping")
        assert response.json() == {"status": "accepted", "releases": 0}

    def test_uninstalling_the_github_app_unlinks_the_installation(self, client, acme, globex):
        payload = {"action": "deleted", "installation": {"id": 101}}
        assert deliver(client, payload, event="installation").status_code == 200
        assert acme.request("GET", "/github/installations").json() == []
        assert len(globex.request("GET", "/github/installations").json()) == 1

    @pytest.mark.parametrize(
        "body", [b"not json", b"[]", b'{"ref": "refs/heads/main", "after": "abc"}']
    )
    def test_signed_but_malformed_push_is_a_client_error(self, client, body):
        response = deliver(client, None, body=body)
        assert response.status_code == 400

    def test_delivery_id_is_required(self, client):
        response = deliver(client, {"zen": "x"}, event="ping", delivery="")
        assert response.status_code == 400
