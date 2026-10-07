"""Cross-tenant access, checked on every route the API exposes.

The route tables below are compared against the application's real route list, so a
new endpoint cannot be added without deciding how it is covered here.
"""

import pytest

from app.security import create_access_token
from tests.helpers import deploy_healthy

PUBLIC_ROUTES = {
    ("POST", "/api/auth/signup"),
    ("POST", "/api/auth/login"),
    # Authenticated by HMAC signature instead of a user token, see test_webhooks.py.
    ("POST", "/api/webhooks/github"),
}

# Routes that address one app (or something inside it) by id.
APP_SCOPED_ROUTES = {
    ("GET", "/api/apps/{app_id}"): None,
    ("DELETE", "/api/apps/{app_id}"): None,
    ("GET", "/api/apps/{app_id}/releases"): None,
    ("POST", "/api/apps/{app_id}/releases"): {"reuse_image": False},
    ("GET", "/api/apps/{app_id}/releases/{release_id}"): None,
    ("POST", "/api/apps/{app_id}/releases/{release_id}/rollback"): None,
    ("GET", "/api/apps/{app_id}/env"): None,
    ("PUT", "/api/apps/{app_id}/env"): {"variables": [{"key": "PWNED", "value": "1"}]},
    ("GET", "/api/apps/{app_id}/domains"): None,
    ("POST", "/api/apps/{app_id}/domains"): {"hostname": "evil.example.org"},
    ("POST", "/api/apps/{app_id}/domains/{domain_id}/check"): None,
    ("DELETE", "/api/apps/{app_id}/domains/{domain_id}"): None,
}

# Routes that return or create things for "the caller's tenant" without an id in the path.
TENANT_SCOPED_ROUTES = {
    ("GET", "/api/auth/me"),
    ("GET", "/api/platform"),
    ("GET", "/api/apps"),
    ("POST", "/api/apps"),
    ("GET", "/api/github/installations"),
    ("POST", "/api/github/installations"),
    ("GET", "/api/github/repos"),
}


def api_routes(client) -> set[tuple[str, str]]:
    return {
        (method.upper(), path)
        for path, operations in client.app.openapi()["paths"].items()
        for method in operations
    }


def test_every_api_route_is_classified(client):
    classified = PUBLIC_ROUTES | set(APP_SCOPED_ROUTES) | TENANT_SCOPED_ROUTES
    assert api_routes(client) == classified


@pytest.fixture
def victim(globex, worker) -> dict:
    """Globex's app with two healthy releases, an env var and a domain."""
    app = globex.create_app()
    globex.set_env(app["id"], {"DB_PASSWORD": "globex-secret"})
    first = deploy_healthy(worker, globex, app["id"], "aaa")
    deploy_healthy(worker, globex, app["id"], "bbb")
    domain = globex.request(
        "POST", f"/apps/{app['id']}/domains", json={"hostname": "www.globex.org"}
    ).json()
    return {"app_id": app["id"], "release_id": first["id"], "domain_id": domain["id"]}


def snapshot(globex, victim) -> dict:
    app_id = victim["app_id"]
    return {
        "app": globex.request("GET", f"/apps/{app_id}").json(),
        "releases": globex.request("GET", f"/apps/{app_id}/releases").json(),
        "env": globex.request("GET", f"/apps/{app_id}/env").json(),
        "domains": globex.request("GET", f"/apps/{app_id}/domains").json(),
    }


@pytest.mark.parametrize(("method", "path"), sorted(APP_SCOPED_ROUTES))
def test_other_tenants_app_routes_are_not_found(acme, globex, victim, method, path):
    before = snapshot(globex, victim)
    applied_before = len(globex.adapters.kube.applied)
    deleted_before = list(globex.adapters.kube.deleted)

    response = acme.request(
        method,
        path.removeprefix("/api").format(**victim),
        json=APP_SCOPED_ROUTES[(method, path)],
    )

    # 404 rather than 403: the response must not confirm that the id exists.
    assert response.status_code == 404
    assert response.json() == {"detail": "App not found"}
    assert "globex" not in response.text
    assert snapshot(globex, victim) == before
    assert len(globex.adapters.kube.applied) == applied_before
    assert globex.adapters.kube.deleted == deleted_before


CHILD_ID_ROUTES = sorted(
    route for route in APP_SCOPED_ROUTES if "{release_id}" in route[1] or "{domain_id}" in route[1]
)


@pytest.mark.parametrize(("method", "path"), CHILD_ID_ROUTES)
def test_own_release_and_domain_ids_do_not_unlock_another_tenants_app(
    acme, globex, victim, worker, method, path
):
    """An attacker pairs their own valid child ids with the victim's app id, and vice versa."""
    own_app = acme.create_app()
    own_release = deploy_healthy(worker, acme, own_app["id"], "ccc")
    own_domain = acme.request(
        "POST", f"/apps/{own_app['id']}/domains", json={"hostname": "www.acme.org"}
    ).json()

    mixed_ids = [
        {**victim, "release_id": own_release["id"], "domain_id": own_domain["id"]},
        {**victim, "app_id": own_app["id"]},
    ]
    for ids in mixed_ids:
        response = acme.request(method, path.removeprefix("/api").format(**ids))
        assert response.status_code == 404, f"{method} {path} with {ids}"


def test_lists_only_show_the_callers_tenant(acme, globex, victim):
    own = acme.create_app(name="mine")
    assert [app["id"] for app in acme.request("GET", "/apps").json()] == [own["id"]]
    assert [app["id"] for app in globex.request("GET", "/apps").json()] == [victim["app_id"]]

    installations = acme.request("GET", "/github/installations").json()
    assert [i["installation_id"] for i in installations] == [acme.installation_id]
    repos = acme.request("GET", "/github/repos").json()
    assert {repo["full_name"] for repo in repos} == {"acme/web"}
    assert acme.request("GET", "/auth/me").json()["tenant"]["slug"] == "acme"


def test_cannot_create_an_app_on_another_tenants_installation(acme, globex):
    response = acme.request(
        "POST",
        "/apps",
        json={
            "name": "steal",
            "installation_id": globex.installation_id,
            "repo_full_name": "globex/web",
            "branch": "main",
        },
    )
    assert response.status_code == 404
    assert acme.request("GET", "/apps").json() == []


def test_cannot_create_an_app_for_a_repo_the_installation_cannot_see(acme):
    response = acme.request(
        "POST",
        "/apps",
        json={
            "name": "steal",
            "installation_id": acme.installation_id,
            "repo_full_name": "globex/web",
            "branch": "main",
        },
    )
    assert response.status_code == 422


def test_cannot_claim_an_installation_the_github_user_cannot_access(acme, globex):
    response = acme.request(
        "POST",
        "/github/installations",
        json={"installation_id": globex.installation_id, "code": "oauth-code-acme"},
    )
    assert response.status_code == 403


def test_cannot_claim_an_installation_linked_to_another_tenant(acme, globex):
    # Even a GitHub user with access to both cannot move an installation between tenants.
    response = acme.request(
        "POST",
        "/github/installations",
        json={"installation_id": globex.installation_id, "code": "oauth-code-globex"},
    )
    assert response.status_code == 409


def test_same_app_name_is_allowed_in_different_tenants(acme, globex):
    assert acme.create_app(name="api")["hostname"] == "api.acme.apps.example.test"
    assert globex.create_app(name="api")["hostname"] == "api.globex.apps.example.test"


def _fill(path: str) -> str:
    return path.format(app_id=1, release_id=1, domain_id=1)


def test_every_protected_route_rejects_anonymous_requests(client):
    for method, path in sorted(api_routes(client) - PUBLIC_ROUTES):
        response = client.request(method, _fill(path), json={})
        assert response.status_code == 401, f"{method} {path}"


def test_every_protected_route_rejects_a_forged_token(client, acme, settings):
    forged = create_access_token(1, "not-the-server-secret-0123456789abcdef", 60)
    for method, path in sorted(api_routes(client) - PUBLIC_ROUTES):
        response = client.request(
            method, _fill(path), json={}, headers={"Authorization": f"Bearer {forged}"}
        )
        assert response.status_code == 401, f"{method} {path}"


def test_expired_token_is_rejected(client, acme, settings):
    expired = create_access_token(1, settings.jwt_secret, ttl_minutes=-1)
    response = client.get("/api/apps", headers={"Authorization": f"Bearer {expired}"})
    assert response.status_code == 401
