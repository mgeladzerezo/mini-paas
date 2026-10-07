import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.adapters.github import Commit, GitHubAppClient, GitHubError, Installation, Repo


@pytest.fixture(scope="module")
def keypair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private, public


class GitHubStub:
    """Answers the handful of GitHub endpoints the client calls and records requests."""

    def __init__(self, public_key: str) -> None:
        self.public_key = public_key
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        auth = request.headers.get("Authorization", "")

        if request.url.host == "github.com" and path == "/login/oauth/access_token":
            form = dict(pair.split("=") for pair in request.content.decode().split("&"))
            if form["code"] != "good-code" or form["client_secret"] != "client-secret":
                return httpx.Response(200, json={"error": "bad_verification_code"})
            return httpx.Response(200, json={"access_token": "ghu_user"})
        if path == "/user/installations" and auth == "Bearer ghu_user":
            return httpx.Response(
                200, json={"installations": [{"id": 7, "account": {"login": "acme"}}]}
            )
        if path == "/app/installations/7/access_tokens" and request.method == "POST":
            claims = jwt.decode(auth.removeprefix("Bearer "), self.public_key, algorithms=["RS256"])
            assert claims["iss"] == "12345"
            assert claims["exp"] - claims["iat"] <= 600
            return httpx.Response(201, json={"token": "ghs_installation"})
        if path == "/installation/repositories" and auth == "Bearer ghs_installation":
            return httpx.Response(
                200,
                json={
                    "total_count": 1,
                    "repositories": [
                        {"full_name": "acme/web", "default_branch": "main", "private": True}
                    ],
                },
            )
        if path == "/repos/acme/web/commits/main" and auth == "Bearer ghs_installation":
            return httpx.Response(
                200, json={"sha": "a" * 40, "commit": {"message": "Subject line\n\nBody"}}
            )
        return httpx.Response(404, json={"message": "Not Found"})


@pytest.fixture
def stub(keypair) -> GitHubStub:
    return GitHubStub(keypair[1])


@pytest.fixture
def github(keypair, stub) -> GitHubAppClient:
    return GitHubAppClient(
        app_id="12345",
        private_key=keypair[0],
        client_id="client-id",
        client_secret="client-secret",
        transport=httpx.MockTransport(stub),
    )


def test_installation_token_is_requested_with_a_signed_app_jwt(github):
    assert github.clone_token(7) == "ghs_installation"


def test_oauth_code_is_exchanged_for_the_users_installations(github):
    assert github.installations_for_code("good-code") == [Installation(7, "acme")]


def test_rejected_oauth_code_raises(github):
    with pytest.raises(GitHubError):
        github.installations_for_code("stolen-or-expired")


def test_list_repos_uses_the_installation_token(github):
    assert github.list_repos(7) == [Repo("acme/web", "main", True)]


def test_branch_head_returns_sha_and_subject(github):
    assert github.branch_head(7, "acme/web", "main") == Commit("a" * 40, "Subject line")


def test_api_errors_are_raised_without_leaking_tokens(github):
    with pytest.raises(GitHubError) as error:
        github.clone_token(999)
    assert "404" in str(error.value)
    assert "Bearer" not in str(error.value)
