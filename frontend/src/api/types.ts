// Mirrors the response models in backend/app/schemas.py.

export type ReleaseStatus =
  | "pending"
  | "building"
  | "deploying"
  | "healthy"
  | "superseded"
  | "failed";

export type ReleaseKind = "build" | "config" | "rollback";

export interface Tenant {
  slug: string;
  name: string;
  namespace: string;
}

export interface User {
  id: number;
  email: string;
  tenant: Tenant;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

export interface PlatformInfo {
  base_domain: string;
  ingress_target: string;
  github_install_url: string;
}

export interface Installation {
  installation_id: number;
  account_login: string;
}

export interface Repo {
  installation_id: number;
  full_name: string;
  default_branch: string;
  private: boolean;
}

export interface AppSummary {
  id: number;
  name: string;
  repo_full_name: string;
  branch: string;
  port: number;
  health_path: string;
  min_replicas: number;
  max_replicas: number;
  hostname: string;
  current_release_id: number | null;
  current_version: number | null;
  latest_status: ReleaseStatus | null;
  created_at: string;
}

export interface AppCreate {
  name: string;
  installation_id: number;
  repo_full_name: string;
  branch: string;
  port: number;
  health_path: string;
  min_replicas: number;
  max_replicas: number;
}

export interface Release {
  id: number;
  version: number;
  kind: ReleaseKind;
  status: ReleaseStatus;
  commit_sha: string;
  commit_message: string;
  triggered_by: string;
  image: string | null;
  error: string | null;
  source_release_id: number | null;
  can_rollback: boolean;
  created_at: string;
  finished_at: string | null;
}

export interface ReleaseDetail extends Release {
  logs: string;
}

export interface EnvVar {
  key: string;
  /** null for secrets: the API never returns their values. */
  value: string | null;
  is_secret: boolean;
}

export interface EnvVarInput {
  key: string;
  /** null keeps the value already stored for this key. */
  value: string | null;
  is_secret: boolean;
}

export interface Domain {
  id: number;
  hostname: string;
  verified: boolean;
  txt_record_name: string;
  txt_record_value: string;
  cname_target: string;
  dns_status: "unknown" | "pending" | "ok";
  tls_status: "unknown" | "pending" | "issued";
  last_checked_at: string | null;
}
