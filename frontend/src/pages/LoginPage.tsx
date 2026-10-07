import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import type { FormEvent } from "react";
import { Navigate } from "react-router-dom";
import { api } from "../api/client";
import type { TokenResponse } from "../api/types";
import { useAuth } from "../auth";
import { ErrorMessage } from "../components/Feedback";

type Mode = "login" | "signup";

export function LoginPage() {
  const { isAuthenticated, signIn } = useAuth();
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [tenantName, setTenantName] = useState("");
  const [tenantSlug, setTenantSlug] = useState("");

  const submit = useMutation({
    mutationFn: () =>
      mode === "login"
        ? api<TokenResponse>("POST", "/auth/login", { email, password })
        : api<TokenResponse>("POST", "/auth/signup", {
            email,
            password,
            tenant_name: tenantName,
            tenant_slug: tenantSlug,
          }),
    onSuccess: (response) => signIn(response.access_token),
  });

  if (isAuthenticated) {
    return <Navigate to="/" replace />;
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    submit.mutate();
  }

  return (
    <div className="mx-auto mt-24 max-w-sm px-4">
      <h1 className="mb-6 text-xl font-semibold">
        {mode === "login" ? "Sign in to mini-paas" : "Create a workspace"}
      </h1>
      <form onSubmit={onSubmit} className="card space-y-4 p-5">
        <div>
          <label className="label" htmlFor="email">
            Email
          </label>
          <input
            id="email"
            type="email"
            required
            className="input"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </div>
        <div>
          <label className="label" htmlFor="password">
            Password
          </label>
          <input
            id="password"
            type="password"
            required
            minLength={10}
            className="input"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </div>
        {mode === "signup" && (
          <>
            <div>
              <label className="label" htmlFor="tenant-name">
                Workspace name
              </label>
              <input
                id="tenant-name"
                required
                className="input"
                value={tenantName}
                onChange={(event) => setTenantName(event.target.value)}
              />
            </div>
            <div>
              <label className="label" htmlFor="tenant-slug">
                Workspace id
              </label>
              <input
                id="tenant-slug"
                required
                pattern="[a-z]([a-z0-9-]{0,28}[a-z0-9])?"
                className="input font-mono"
                value={tenantSlug}
                onChange={(event) => setTenantSlug(event.target.value)}
              />
              <p className="mt-1 text-xs text-slate-500">
                Lowercase letters, digits and dashes. Used in your namespace and hostnames.
              </p>
            </div>
          </>
        )}
        <ErrorMessage error={submit.error} />
        <button type="submit" className="btn btn-primary w-full" disabled={submit.isPending}>
          {mode === "login" ? "Sign in" : "Create workspace"}
        </button>
      </form>
      <button
        type="button"
        className="mt-4 text-sm text-slate-600 underline"
        onClick={() => {
          submit.reset();
          setMode(mode === "login" ? "signup" : "login");
        }}
      >
        {mode === "login" ? "New here? Create a workspace" : "Already have an account? Sign in"}
      </button>
    </div>
  );
}
