import { Link } from "react-router-dom";
import { useApps } from "../api/hooks";
import { ErrorMessage, Loading } from "../components/Feedback";
import { StatusBadge } from "../components/StatusBadge";

export function AppsPage() {
  const apps = useApps();

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-semibold">Apps</h1>
        <Link to="/apps/new" className="btn btn-primary">
          Connect a repository
        </Link>
      </div>
      <ErrorMessage error={apps.error} />
      {apps.isLoading && <Loading />}
      {apps.data?.length === 0 && (
        <div className="card p-8 text-center text-sm text-slate-600">
          No apps yet. Connect a repository to deploy your first one.
        </div>
      )}
      {apps.data && apps.data.length > 0 && (
        <div className="card overflow-hidden">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-slate-200 bg-slate-50 text-xs uppercase text-slate-500">
              <tr>
                <th className="px-4 py-2">Name</th>
                <th className="px-4 py-2">Repository</th>
                <th className="px-4 py-2">Live</th>
                <th className="px-4 py-2">Latest release</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {apps.data.map((app) => (
                <tr key={app.id}>
                  <td className="px-4 py-3">
                    <Link to={`/apps/${app.id}`} className="font-medium underline">
                      {app.name}
                    </Link>
                    <div className="font-mono text-xs text-slate-500">{app.hostname}</div>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs">
                    {app.repo_full_name}@{app.branch}
                  </td>
                  <td className="px-4 py-3">
                    {app.current_version === null ? "-" : `v${app.current_version}`}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={app.latest_status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
