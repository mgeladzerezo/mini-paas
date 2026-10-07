import { useState } from "react";
import { useDeploy, useEnv, useSaveEnv } from "../../api/hooks";
import type { AppSummary, EnvVar } from "../../api/types";
import { ErrorMessage, Loading } from "../../components/Feedback";
import { emptyRow, rowsFromEnv, toEnvPayload, validateRows } from "../../lib/env";
import type { EnvRow } from "../../lib/env";

export function EnvTab({ app }: { app: AppSummary }) {
  const env = useEnv(app.id);
  if (env.isLoading) {
    return <Loading />;
  }
  if (!env.data) {
    return <ErrorMessage error={env.error} />;
  }
  // Remount the editor whenever the saved set changes so its draft starts from it.
  return <EnvEditor key={JSON.stringify(env.data)} app={app} saved={env.data} />;
}

function EnvEditor({ app, saved }: { app: AppSummary; saved: EnvVar[] }) {
  const saveEnv = useSaveEnv(app.id);
  const deploy = useDeploy(app.id);
  const [rows, setRows] = useState<EnvRow[]>(() => rowsFromEnv(saved));
  const [validationError, setValidationError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  function updateRow(index: number, patch: Partial<EnvRow>) {
    setRows(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
    setNotice(null);
  }

  function save(redeploy: boolean) {
    const problem = validateRows(rows);
    setValidationError(problem);
    if (problem) {
      return;
    }
    saveEnv.mutate(toEnvPayload(rows), {
      onSuccess: () => {
        if (redeploy) {
          deploy.mutate(true, {
            onSuccess: (release) => setNotice(`Saved. Release v${release.version} is rolling out.`),
          });
        } else {
          setNotice("Saved. The new values apply from the next deployment.");
        }
      },
    });
  }

  const busy = saveEnv.isPending || deploy.isPending;

  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-600">
        Variables are delivered to the container as a Kubernetes Secret. Secret values are
        write-only: they can be replaced here but are never shown again.
      </p>
      <div className="card divide-y divide-slate-100">
        {rows.length === 0 && <p className="p-4 text-sm text-slate-500">No variables defined.</p>}
        {rows.map((row, index) => (
          <div key={index} className="flex items-center gap-2 p-3">
            <input
              aria-label="Name"
              placeholder="NAME"
              className="input w-1/3 font-mono"
              value={row.key}
              onChange={(event) => updateRow(index, { key: event.target.value })}
            />
            <input
              aria-label={`Value of ${row.key || "new variable"}`}
              type={row.isSecret ? "password" : "text"}
              autoComplete="off"
              placeholder={row.hasStoredSecret ? "stored, leave blank to keep" : "value"}
              className="input flex-1 font-mono"
              value={row.value}
              onChange={(event) => updateRow(index, { value: event.target.value })}
            />
            <label className="flex items-center gap-1 text-sm text-slate-600">
              <input
                type="checkbox"
                checked={row.isSecret}
                onChange={(event) => updateRow(index, { isSecret: event.target.checked })}
              />
              Secret
            </label>
            <button
              type="button"
              className="btn"
              aria-label={`Remove ${row.key || "variable"}`}
              onClick={() => setRows(rows.filter((_, i) => i !== index))}
            >
              Remove
            </button>
          </div>
        ))}
      </div>
      <ErrorMessage error={validationError ?? saveEnv.error ?? deploy.error} />
      {notice && <p className="text-sm text-emerald-700">{notice}</p>}
      <div className="flex gap-2">
        <button type="button" className="btn" onClick={() => setRows([...rows, emptyRow()])}>
          Add variable
        </button>
        <button type="button" className="btn" disabled={busy} onClick={() => save(false)}>
          Save
        </button>
        <button
          type="button"
          className="btn btn-primary"
          disabled={busy || app.current_release_id === null}
          title={app.current_release_id === null ? "Deploy the app once first" : undefined}
          onClick={() => save(true)}
        >
          Save and redeploy
        </button>
      </div>
    </div>
  );
}
