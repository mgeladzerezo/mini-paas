import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useApp, useDeleteApp } from "../api/hooks";
import { ErrorMessage, Loading } from "../components/Feedback";
import { StatusBadge } from "../components/StatusBadge";
import { DeploymentsTab } from "./app/DeploymentsTab";
import { DomainsTab } from "./app/DomainsTab";
import { EnvTab } from "./app/EnvTab";

const TABS = ["Deployments", "Environment", "Domains"] as const;
type Tab = (typeof TABS)[number];

export function AppPage() {
  const appId = Number(useParams().appId);
  const navigate = useNavigate();
  const app = useApp(appId);
  const deleteApp = useDeleteApp();
  const [tab, setTab] = useState<Tab>("Deployments");

  if (app.isLoading) {
    return <Loading />;
  }
  if (!app.data) {
    return <ErrorMessage error={app.error ?? "App not found"} />;
  }
  const current = app.data;

  function onDelete() {
    if (!window.confirm(`Delete ${current.name}? This removes it from the cluster.`)) {
      return;
    }
    deleteApp.mutate(current.id, { onSuccess: () => navigate("/") });
  }

  return (
    <div>
      <Link to="/" className="text-sm text-slate-600 underline">
        Back to apps
      </Link>
      <div className="mb-4 mt-2 flex items-start justify-between">
        <div>
          <h1 className="flex items-center gap-3 text-xl font-semibold">
            {current.name}
            <StatusBadge status={current.latest_status} />
          </h1>
          <p className="mt-1 font-mono text-sm text-slate-600">
            <a href={`https://${current.hostname}`} className="underline">
              {current.hostname}
            </a>
          </p>
          <p className="mt-1 text-sm text-slate-500">
            {current.repo_full_name}@{current.branch}, port {current.port}, live release:{" "}
            {current.current_version === null ? "none" : `v${current.current_version}`}
          </p>
        </div>
        <button
          type="button"
          className="btn btn-danger"
          onClick={onDelete}
          disabled={deleteApp.isPending}
        >
          Delete app
        </button>
      </div>
      <ErrorMessage error={deleteApp.error} />

      <div role="tablist" className="mb-4 flex gap-1 border-b border-slate-200">
        {TABS.map((name) => (
          <button
            key={name}
            type="button"
            role="tab"
            aria-selected={tab === name}
            onClick={() => setTab(name)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm ${
              tab === name
                ? "border-slate-900 font-medium text-slate-900"
                : "border-transparent text-slate-500 hover:text-slate-800"
            }`}
          >
            {name}
          </button>
        ))}
      </div>

      {tab === "Deployments" && <DeploymentsTab app={current} />}
      {tab === "Environment" && <EnvTab app={current} />}
      {tab === "Domains" && <DomainsTab app={current} />}
    </div>
  );
}
