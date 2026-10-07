import pytest
from sqlalchemy import select

from app.config import Settings
from app.models import EnvVar, Release, User
from app.security import SecretBox, hash_password, verify_password
from tests.helpers import PASSWORD, deploy_healthy


class TestAuth:
    def test_signup_then_login(self, client, acme):
        response = client.post(
            "/api/auth/login", json={"email": "Owner@ACME.example.com", "password": PASSWORD}
        )
        assert response.status_code == 200
        me = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {response.json()['access_token']}"}
        ).json()
        assert me["email"] == "owner@acme.example.com"
        assert me["tenant"] == {"slug": "acme", "name": "Acme", "namespace": "tenant-acme"}

    def test_wrong_password_and_unknown_user_look_the_same(self, client, acme):
        wrong = client.post(
            "/api/auth/login",
            json={"email": "owner@acme.example.com", "password": "wrong-password"},
        )
        unknown = client.post(
            "/api/auth/login", json={"email": "nobody@acme.example.com", "password": PASSWORD}
        )
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json() == unknown.json()

    def test_duplicate_email_or_workspace_is_a_conflict(self, client, acme):
        body = {
            "email": "second@acme.example.com",
            "password": PASSWORD,
            "tenant_slug": "acme",
            "tenant_name": "Acme again",
        }
        assert client.post("/api/auth/signup", json=body).status_code == 409
        body.update(email="owner@acme.example.com", tenant_slug="acme-two")
        assert client.post("/api/auth/signup", json=body).status_code == 409

    @pytest.mark.parametrize("slug", ["Acme", "-acme", "acme-", "ac me", "a" * 31, "kube.system"])
    def test_workspace_slug_must_be_a_dns_label(self, client, slug):
        body = {
            "email": "owner@x.example.com",
            "password": PASSWORD,
            "tenant_slug": slug,
            "tenant_name": "X",
        }
        assert client.post("/api/auth/signup", json=body).status_code == 422

    def test_passwords_are_stored_hashed(self, acme, session_factory):
        with session_factory() as session:
            stored = session.scalar(select(User.password_hash))
        assert PASSWORD not in stored
        assert verify_password(PASSWORD, stored)
        assert not verify_password("something-else", stored)
        assert hash_password(PASSWORD) != stored, "each hash must use a fresh salt"


class TestApps:
    def test_create_and_list(self, acme):
        created = acme.create_app(name="web", port=3000, health_path="/healthz")
        assert created["hostname"] == "web.acme.apps.example.test"
        assert created["current_release_id"] is None
        assert acme.request("GET", "/apps").json() == [created]

    def test_duplicate_name_in_the_same_tenant_is_a_conflict(self, acme):
        acme.create_app(name="web")
        response = acme.request(
            "POST",
            "/apps",
            json={
                "name": "web",
                "installation_id": 101,
                "repo_full_name": "acme/web",
                "branch": "main",
            },
        )
        assert response.status_code == 409

    @pytest.mark.parametrize(
        "override",
        [
            {"name": "Web"},
            {"name": "web_app"},
            {"port": 80},
            {"health_path": "healthz"},
            {"min_replicas": 3, "max_replicas": 2},
            {"max_replicas": 50},
            {"branch": "main; rm -rf /"},
            {"repo_full_name": "acme/web/extra"},
        ],
    )
    def test_invalid_settings_are_rejected(self, acme, override):
        body = {
            "name": "web",
            "installation_id": 101,
            "repo_full_name": "acme/web",
            "branch": "main",
            **override,
        }
        assert acme.request("POST", "/apps", json=body).status_code == 422

    def test_delete_removes_cluster_objects_and_history(self, acme, worker, session_factory):
        app = acme.create_app()
        acme.set_env(app["id"], {"KEY": "value"})
        deploy_healthy(worker, acme, app["id"], "aaa")
        kube = acme.adapters.kube
        assert {"Deployment", "Service", "Ingress", "Secret"} <= set(kube.kinds_in("tenant-acme"))

        assert acme.request("DELETE", f"/apps/{app['id']}").status_code == 204

        remaining = kube.kinds_in("tenant-acme")
        for kind in ("Deployment", "Service", "Ingress", "HorizontalPodAutoscaler", "Secret"):
            assert kind not in remaining
        # The tenant's isolation objects outlive any single app.
        assert "NetworkPolicy" in remaining
        assert acme.request("GET", f"/apps/{app['id']}").status_code == 404
        with session_factory() as session:
            assert session.scalars(select(Release)).all() == []
            assert session.scalars(select(EnvVar)).all() == []


class TestEnv:
    @pytest.fixture
    def app(self, acme):
        return acme.create_app()

    def put(self, acme, app, variables):
        return acme.request("PUT", f"/apps/{app['id']}/env", json={"variables": variables})

    def test_secret_values_are_write_only(self, acme, app):
        response = self.put(
            acme,
            app,
            [
                {"key": "LOG_LEVEL", "value": "debug"},
                {"key": "DB_PASSWORD", "value": "hunter2-hunter2", "is_secret": True},
            ],
        )
        assert response.status_code == 200
        assert response.json() == [
            {"key": "DB_PASSWORD", "value": None, "is_secret": True},
            {"key": "LOG_LEVEL", "value": "debug", "is_secret": False},
        ]
        assert "hunter2" not in acme.request("GET", f"/apps/{app['id']}/env").text

    def test_values_are_encrypted_at_rest(self, acme, app, session_factory, settings):
        self.put(acme, app, [{"key": "DB_PASSWORD", "value": "hunter2-hunter2", "is_secret": True}])
        with session_factory() as session:
            stored = session.scalar(select(EnvVar.value_encrypted))
        assert "hunter2" not in stored
        assert SecretBox(settings.encryption_key).decrypt(stored) == "hunter2-hunter2"

    def test_omitting_a_secret_value_keeps_it(self, acme, app, worker):
        self.put(acme, app, [{"key": "DB_PASSWORD", "value": "hunter2-hunter2", "is_secret": True}])
        response = self.put(
            acme,
            app,
            [
                {"key": "DB_PASSWORD", "is_secret": True},
                {"key": "NEW", "value": "1"},
            ],
        )
        assert response.status_code == 200
        deploy_healthy(worker, acme, app["id"], "aaa")
        secret = acme.adapters.kube.get("v1", "Secret", "tenant-acme", "web-env-v1")
        assert set(secret["data"]) == {"DB_PASSWORD", "NEW"}

    def test_put_replaces_the_whole_set(self, acme, app):
        self.put(acme, app, [{"key": "A", "value": "1"}, {"key": "B", "value": "2"}])
        response = self.put(acme, app, [{"key": "B", "value": "3"}])
        assert response.json() == [{"key": "B", "value": "3", "is_secret": False}]

    def test_a_secret_cannot_be_revealed_by_unmarking_it(self, acme, app):
        self.put(acme, app, [{"key": "DB_PASSWORD", "value": "hunter2-hunter2", "is_secret": True}])
        response = self.put(acme, app, [{"key": "DB_PASSWORD", "is_secret": False}])
        assert response.status_code == 422
        assert acme.request("GET", f"/apps/{app['id']}/env").json()[0]["value"] is None

    @pytest.mark.parametrize(
        "variables",
        [
            [{"key": "1BAD", "value": "x"}],
            [{"key": "HAS-DASH", "value": "x"}],
            [{"key": "A", "value": "1"}, {"key": "A", "value": "2"}],
            [{"key": "PORT", "value": "9999"}],
            [{"key": "NEW_WITHOUT_VALUE"}],
        ],
    )
    def test_invalid_variables_are_rejected(self, acme, app, variables):
        assert self.put(acme, app, variables).status_code == 422


class TestOperationalEndpoints:
    def test_health_and_readiness(self, client):
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/readyz").json() == {"status": "ready"}

    def test_metrics_use_route_templates_as_labels(self, client, acme):
        app = acme.create_app()
        acme.request("GET", f"/apps/{app['id']}")
        body = client.get("/metrics").text
        assert 'route="/api/apps/{app_id}"' in body
        assert f'route="/api/apps/{app["id"]}"' not in body
        assert "paas_releases_finished_total" in body


class TestSettings:
    def test_real_adapters_refuse_to_start_with_development_secrets(self):
        with pytest.raises(ValueError, match="PAAS_JWT_SECRET"):
            Settings(_env_file=None, adapters="real")

    def test_real_adapters_accept_configured_secrets(self):
        settings = Settings(
            _env_file=None,
            adapters="real",
            jwt_secret="x" * 40,
            encryption_key="kZ1pQ0n4w3u6m8r2t5y7b9d1f3h5j7l9n1p3r5t7v9w=",
            github_webhook_secret="y" * 32,
        )
        assert settings.adapters == "real"
