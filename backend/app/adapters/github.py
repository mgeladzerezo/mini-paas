import time
from dataclasses import dataclass
from typing import Protocol

import httpx
import jwt


@dataclass(frozen=True)
class Installation:
    installation_id: int
    account_login: str


@dataclass(frozen=True)
class Repo:
    full_name: str
    default_branch: str
    private: bool


@dataclass(frozen=True)
class Commit:
    sha: str
    message: str


class GitHubError(Exception):
    pass


class GitHubClient(Protocol):
    def installations_for_code(self, code: str) -> list[Installation]:
        """Exchange an OAuth code and return the App installations that user can access."""
        ...

    def list_repos(self, installation_id: int) -> list[Repo]: ...

    def branch_head(self, installation_id: int, repo_full_name: str, branch: str) -> Commit: ...

    def clone_token(self, installation_id: int) -> str: ...


class GitHubAppClient:
    API = "https://api.github.com"
    OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token"

    def __init__(
        self,
        app_id: str,
        private_key: str,
        client_id: str,
        client_secret: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._app_id = app_id
        self._private_key = private_key
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = httpx.Client(
            transport=transport,
            timeout=10,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def installations_for_code(self, code: str) -> list[Installation]:
        response = self._http.post(
            self.OAUTH_TOKEN_URL,
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "code": code,
            },
            headers={"Accept": "application/json"},
        )
        user_token = self._json(response).get("access_token")
        if not user_token:
            raise GitHubError("GitHub rejected the authorization code")
        body = self._get("/user/installations", user_token, params={"per_page": 100})
        return [
            Installation(item["id"], item["account"]["login"]) for item in body["installations"]
        ]

    def list_repos(self, installation_id: int) -> list[Repo]:
        token = self.clone_token(installation_id)
        repos: list[Repo] = []
        # Five pages of 100 is plenty for a picker; beyond that the UI needs search anyway.
        for page in range(1, 6):
            body = self._get(
                "/installation/repositories", token, params={"per_page": 100, "page": page}
            )
            repos += [
                Repo(item["full_name"], item["default_branch"], item["private"])
                for item in body["repositories"]
            ]
            if len(repos) >= body["total_count"] or not body["repositories"]:
                break
        return repos

    def branch_head(self, installation_id: int, repo_full_name: str, branch: str) -> Commit:
        token = self.clone_token(installation_id)
        body = self._get(f"/repos/{repo_full_name}/commits/{branch}", token)
        return Commit(sha=body["sha"], message=body["commit"]["message"].splitlines()[0][:500])

    def clone_token(self, installation_id: int) -> str:
        response = self._http.post(
            f"{self.API}/app/installations/{installation_id}/access_tokens",
            headers={"Authorization": f"Bearer {self._app_jwt()}"},
        )
        return self._json(response)["token"]

    def _app_jwt(self) -> str:
        now = int(time.time())
        # Issued 60s in the past to tolerate clock drift; GitHub caps lifetime at 10 minutes.
        claims = {"iat": now - 60, "exp": now + 540, "iss": self._app_id}
        return jwt.encode(claims, self._private_key, algorithm="RS256")

    def _get(self, path: str, token: str, params: dict | None = None) -> dict:
        response = self._http.get(
            f"{self.API}{path}", params=params, headers={"Authorization": f"Bearer {token}"}
        )
        return self._json(response)

    @staticmethod
    def _json(response: httpx.Response) -> dict:
        if response.status_code >= 400:
            raise GitHubError(f"GitHub API returned {response.status_code} for {response.url.path}")
        return response.json()
