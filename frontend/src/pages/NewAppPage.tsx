import { useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useCreateApp, useInstallations, usePlatform, useRepos } from "../api/hooks";
import type { Repo } from "../api/types";
import { ErrorMessage, Loading } from "../components/Feedback";

function suggestName(repoFullName: string): string {
  const repoName = repoFullName.split("/")[1] ?? "";
  return repoName
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, "-")
    .replace(/^[^a-z]+|-+$/g, "")
    .slice(0, 30);
}

export function NewAppPage() {
  const navigate = useNavigate();
  const platform = usePlatform();
  const installations = useInstallations();
  const hasInstallation = (installations.data?.length ?? 0) > 0;
  const repos = useRepos(hasInstallation);
  const createApp = useCreateApp();

  const [repo, setRepo] = useState<Repo | null>(null);
  const [name, setName] = useState("");
  const [branch, setBranch] = useState("");
  const [port, setPort] = useState(8080);
  const [healthPath, setHealthPath] = useState("/");

  function selectRepo(fullName: string) {
    const selected = repos.data?.find((candidate) => candidate.full_name === fullName) ?? null;
    setRepo(selected);
    if (selected) {
      setName(suggestName(selected.full_name));
      setBranch(selected.default_branch);
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!repo) {
      return;
    }
    createApp.mutate(
      {
        name,
        installation_id: repo.installation_id,
        repo_full_name: repo.full_name,
        branch,
        port,
        health_path: healthPath,
        min_replicas: 1,
        max_replicas: 3,
      },
      { onSuccess: (app) => navigate(`/apps/${app.id}`) },
    );
  }

  const installUrl = platform.data?.github_install_url;

  return (
    <div className="max-w-xl">
      <Link to="/" className="text-sm text-slate-600 underline">
        Back to apps
      </Link>
      <h1 className="mb-4 mt-2 text-xl font-semibold">Connect a repository</h1>
      <ErrorMessage error={installations.error ?? repos.error ?? platform.error} />
      {installations.isLoading && <Loading />}

      <section className="card mb-4 p-5">
        <h2 className="mb-2 font-medium">1. GitHub access</h2>
        {hasInstallation ? (
          <p className="text-sm text-slate-600">
            Connected to{" "}
            {installations.data?.map((installation) => installation.account_login).join(", ")}.{" "}
            {installUrl && (
              <a href={installUrl} className="underline">
                Add another account
              </a>
            )}
          </p>
        ) : (
          <>
            <p className="mb-3 text-sm text-slate-600">
              Install the GitHub App on the account that owns the repository. It needs read access
              to code and sends push events to the platform.
            </p>
            {installUrl && (
              <a href={installUrl} className="btn btn-primary">
                Install GitHub App
              </a>
            )}
          </>
        )}
      </section>

      {hasInstallation && (
        <form onSubmit={onSubmit} className="card space-y-4 p-5">
          <h2 className="font-medium">2. Repository and settings</h2>
          <div>
            <label className="label" htmlFor="repo">
              Repository
            </label>
            <select
              id="repo"
              required
              className="input"
              value={repo?.full_name ?? ""}
              onChange={(event) => selectRepo(event.target.value)}
            >
              <option value="" disabled>
                {repos.isLoading ? "Loading repositories..." : "Select a repository"}
              </option>
              {repos.data?.map((candidate) => (
                <option key={candidate.full_name} value={candidate.full_name}>
                  {candidate.full_name}
                  {candidate.private ? " (private)" : ""}
                </option>
              ))}
            </select>
            <p className="mt-1 text-xs text-slate-500">
              The repository needs a Dockerfile at its root.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="label" htmlFor="name">
                App name
              </label>
              <input
                id="name"
                required
                pattern="[a-z]([a-z0-9-]{0,28}[a-z0-9])?"
                className="input font-mono"
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </div>
            <div>
              <label className="label" htmlFor="branch">
                Branch
              </label>
              <input
                id="branch"
                required
                className="input font-mono"
                value={branch}
                onChange={(event) => setBranch(event.target.value)}
              />
            </div>
            <div>
              <label className="label" htmlFor="port">
                Container port
              </label>
              <input
                id="port"
                type="number"
                required
                min={1024}
                max={65535}
                className="input"
                value={port}
                onChange={(event) => setPort(Number(event.target.value))}
              />
            </div>
            <div>
              <label className="label" htmlFor="health-path">
                Health check path
              </label>
              <input
                id="health-path"
                required
                pattern="/.*"
                className="input font-mono"
                value={healthPath}
                onChange={(event) => setHealthPath(event.target.value)}
              />
            </div>
          </div>
          {name && platform.data && (
            <p className="text-sm text-slate-600">
              Pushes to <span className="font-mono">{branch}</span> will deploy to a hostname under{" "}
              <span className="font-mono">{platform.data.base_domain}</span>.
            </p>
          )}
          <ErrorMessage error={createApp.error} />
          <button type="submit" className="btn btn-primary" disabled={!repo || createApp.isPending}>
            Create app
          </button>
        </form>
      )}
    </div>
  );
}
