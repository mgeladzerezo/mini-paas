import { useState } from "react";
import { isActive, useDeploy, useRelease, useReleases, useRollback } from "../../api/hooks";
import type { AppSummary, Release } from "../../api/types";
import { ErrorMessage, Loading } from "../../components/Feedback";
import { StatusBadge } from "../../components/StatusBadge";
import { formatTime, shortSha } from "../../lib/format";

const KIND_LABELS: Record<Release["kind"], string> = {
  build: "build",
  config: "config change",
  rollback: "rollback",
};

function describe(release: Release, releases: Release[]): string {
  if (release.kind !== "rollback") {
    return release.commit_message || "(no commit message)";
  }
  const source = releases.find((candidate) => candidate.id === release.source_release_id);
  return source ? `Rollback to v${source.version}` : "Rollback";
}

export function DeploymentsTab({ app }: { app: AppSummary }) {
  const releases = useReleases(app.id);
  const deploy = useDeploy(app.id);
  const rollback = useRollback(app.id);
  const [selectedId, setSelectedId] = useState<number | null>(null);

  // Follow the newest release until the user picks one.
  const shownId = selectedId ?? releases.data?.[0]?.id ?? null;
  const detail = useRelease(app.id, shownId);

  function onRollback(release: Release) {
    const message = `Roll back to v${release.version} (${shortSha(release.commit_sha)})? Its image and environment are restored exactly.`;
    if (window.confirm(message)) {
      rollback.mutate(release.id, { onSuccess: (created) => setSelectedId(created.id) });
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <button
          type="button"
          className="btn btn-primary"
          disabled={deploy.isPending}
          onClick={() => deploy.mutate(false, { onSuccess: (created) => setSelectedId(created.id) })}
        >
          Deploy latest commit
        </button>
        <span className="text-sm text-slate-500">
          Pushes to <span className="font-mono">{app.branch}</span> deploy automatically.
        </span>
      </div>
      <ErrorMessage error={deploy.error ?? rollback.error ?? releases.error} />
      {releases.isLoading && <Loading />}
      {releases.data?.length === 0 && (
        <p className="card p-6 text-sm text-slate-600">No deployments yet.</p>
      )}

      {releases.data && releases.data.length > 0 && (
        <div className="card overflow-hidden">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-slate-200 bg-slate-50 text-xs uppercase text-slate-500">
              <tr>
                <th className="px-4 py-2">Release</th>
                <th className="px-4 py-2">Status</th>
                <th className="px-4 py-2">Change</th>
                <th className="px-4 py-2">Started</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {releases.data.map((release) => (
                <tr key={release.id} className={release.id === shownId ? "bg-slate-50" : ""}>
                  <td className="px-4 py-3 font-medium">
                    v{release.version}
                    {release.id === app.current_release_id && (
                      <span className="ml-2 text-xs font-normal text-emerald-700">live</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={release.status} />
                  </td>
                  <td className="px-4 py-3">
                    <div>{describe(release, releases.data)}</div>
                    <div className="text-xs text-slate-500">
                      {KIND_LABELS[release.kind]},{" "}
                      <span className="font-mono">{shortSha(release.commit_sha)}</span>, by{" "}
                      {release.triggered_by || "unknown"}
                    </div>
                    {release.error && <div className="text-xs text-red-700">{release.error}</div>}
                  </td>
                  <td className="px-4 py-3 text-slate-500">{formatTime(release.created_at)}</td>
                  <td className="space-x-2 whitespace-nowrap px-4 py-3 text-right">
                    <button type="button" className="btn" onClick={() => setSelectedId(release.id)}>
                      Logs
                    </button>
                    {release.can_rollback && (
                      <button
                        type="button"
                        className="btn"
                        disabled={rollback.isPending}
                        onClick={() => onRollback(release)}
                      >
                        Roll back
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {detail.data && (
        <section className="card">
          <header className="flex items-center justify-between border-b border-slate-200 px-4 py-2 text-sm">
            <span className="font-medium">Logs for v{detail.data.version}</span>
            {isActive(detail.data.status) && <span className="text-slate-500">updating...</span>}
          </header>
          <pre className="max-h-96 overflow-auto whitespace-pre-wrap bg-slate-900 p-4 font-mono text-xs text-slate-100">
            {detail.data.logs || "No output yet."}
          </pre>
        </section>
      )}
    </div>
  );
}
