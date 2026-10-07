import { Link, Outlet } from "react-router-dom";
import { useMe } from "../api/hooks";
import { useAuth } from "../auth";

export function Layout() {
  const { signOut } = useAuth();
  const me = useMe(true);

  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-4 py-3">
          <Link to="/" className="font-semibold">
            mini-paas
          </Link>
          <div className="flex items-center gap-4 text-sm text-slate-600">
            {me.data && (
              <span>
                {me.data.email} / <span className="font-mono">{me.data.tenant.slug}</span>
              </span>
            )}
            <button type="button" className="btn" onClick={signOut}>
              Sign out
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
