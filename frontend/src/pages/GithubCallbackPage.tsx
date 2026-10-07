import { useEffect, useRef } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useClaimInstallation } from "../api/hooks";
import { ErrorMessage } from "../components/Feedback";

/** GitHub redirects here after the App is installed, with `installation_id` and an OAuth `code`. */
export function GithubCallbackPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { mutate, error } = useClaimInstallation();
  const submitted = useRef(false);

  const installationId = Number(params.get("installation_id"));
  const code = params.get("code");
  const valid = Number.isInteger(installationId) && installationId > 0 && Boolean(code);

  useEffect(() => {
    // The OAuth code is single-use, so React's development double-invoke must not resend it.
    if (!valid || !code || submitted.current) {
      return;
    }
    submitted.current = true;
    mutate(
      { installation_id: installationId, code },
      { onSuccess: () => navigate("/apps/new", { replace: true }) },
    );
  }, [valid, installationId, code, mutate, navigate]);

  return (
    <div className="max-w-xl space-y-3">
      <h1 className="text-xl font-semibold">Connecting GitHub</h1>
      {!valid && (
        <ErrorMessage error="The link is missing the installation details. Start again from the connect page." />
      )}
      {valid && !error && <p className="text-sm text-slate-600">Verifying the installation...</p>}
      <ErrorMessage error={error} />
      {(error || !valid) && (
        <Link to="/apps/new" className="text-sm underline">
          Back to connect a repository
        </Link>
      )}
    </div>
  );
}
