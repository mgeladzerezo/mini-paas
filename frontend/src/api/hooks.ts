import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type {
  AppCreate,
  AppSummary,
  Domain,
  EnvVar,
  EnvVarInput,
  Installation,
  PlatformInfo,
  Release,
  ReleaseDetail,
  ReleaseStatus,
  Repo,
  User,
} from "./types";

const ACTIVE: ReleaseStatus[] = ["pending", "building", "deploying"];
const POLL_MS = 3000;

export function isActive(status: ReleaseStatus | null | undefined): boolean {
  return status != null && ACTIVE.includes(status);
}

const keys = {
  me: ["me"] as const,
  platform: ["platform"] as const,
  installations: ["installations"] as const,
  repos: ["repos"] as const,
  apps: ["apps"] as const,
  app: (id: number) => ["apps", id] as const,
  releases: (id: number) => ["apps", id, "releases"] as const,
  release: (id: number, releaseId: number) => ["apps", id, "releases", releaseId] as const,
  env: (id: number) => ["apps", id, "env"] as const,
  domains: (id: number) => ["apps", id, "domains"] as const,
};

export function useMe(enabled: boolean) {
  return useQuery({ queryKey: keys.me, queryFn: () => api<User>("GET", "/auth/me"), enabled });
}

export function usePlatform() {
  return useQuery({
    queryKey: keys.platform,
    queryFn: () => api<PlatformInfo>("GET", "/platform"),
    staleTime: Infinity,
  });
}

export function useInstallations() {
  return useQuery({
    queryKey: keys.installations,
    queryFn: () => api<Installation[]>("GET", "/github/installations"),
  });
}

export function useRepos(enabled: boolean) {
  return useQuery({
    queryKey: keys.repos,
    queryFn: () => api<Repo[]>("GET", "/github/repos"),
    enabled,
  });
}

export function useClaimInstallation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (claim: { installation_id: number; code: string }) =>
      api<Installation>("POST", "/github/installations", claim),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.installations });
      void queryClient.invalidateQueries({ queryKey: keys.repos });
    },
  });
}

export function useApps() {
  return useQuery({
    queryKey: keys.apps,
    queryFn: () => api<AppSummary[]>("GET", "/apps"),
    refetchInterval: (query) =>
      query.state.data?.some((app) => isActive(app.latest_status)) ? POLL_MS : false,
  });
}

export function useApp(appId: number) {
  return useQuery({
    queryKey: keys.app(appId),
    queryFn: () => api<AppSummary>("GET", `/apps/${appId}`),
    refetchInterval: (query) => (isActive(query.state.data?.latest_status) ? POLL_MS : false),
  });
}

export function useCreateApp() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: AppCreate) => api<AppSummary>("POST", "/apps", body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.apps }),
  });
}

export function useDeleteApp() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (appId: number) => api<void>("DELETE", `/apps/${appId}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.apps }),
  });
}

export function useReleases(appId: number) {
  return useQuery({
    queryKey: keys.releases(appId),
    queryFn: () => api<Release[]>("GET", `/apps/${appId}/releases`),
    refetchInterval: (query) =>
      query.state.data?.some((release) => isActive(release.status)) ? POLL_MS : false,
  });
}

export function useRelease(appId: number, releaseId: number | null) {
  return useQuery({
    queryKey: keys.release(appId, releaseId ?? 0),
    queryFn: () => api<ReleaseDetail>("GET", `/apps/${appId}/releases/${releaseId}`),
    enabled: releaseId !== null,
    refetchInterval: (query) => (isActive(query.state.data?.status) ? POLL_MS : false),
  });
}

function useReleaseMutation<TArg>(appId: number, request: (arg: TArg) => Promise<Release>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: request,
    // The app key is a prefix of its release keys, so this refreshes all of them.
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.app(appId) }),
  });
}

export function useDeploy(appId: number) {
  return useReleaseMutation(appId, (reuseImage: boolean) =>
    api<Release>("POST", `/apps/${appId}/releases`, { reuse_image: reuseImage }),
  );
}

export function useRollback(appId: number) {
  return useReleaseMutation(appId, (releaseId: number) =>
    api<Release>("POST", `/apps/${appId}/releases/${releaseId}/rollback`),
  );
}

export function useEnv(appId: number) {
  return useQuery({
    queryKey: keys.env(appId),
    queryFn: () => api<EnvVar[]>("GET", `/apps/${appId}/env`),
  });
}

export function useSaveEnv(appId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (variables: EnvVarInput[]) =>
      api<EnvVar[]>("PUT", `/apps/${appId}/env`, { variables }),
    onSuccess: (saved) => queryClient.setQueryData(keys.env(appId), saved),
  });
}

export function useDomains(appId: number) {
  return useQuery({
    queryKey: keys.domains(appId),
    queryFn: () => api<Domain[]>("GET", `/apps/${appId}/domains`),
  });
}

export function useAddDomain(appId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (hostname: string) => api<Domain>("POST", `/apps/${appId}/domains`, { hostname }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.domains(appId) }),
  });
}

export function useCheckDomain(appId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (domainId: number) =>
      api<Domain>("POST", `/apps/${appId}/domains/${domainId}/check`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.domains(appId) }),
  });
}

export function useRemoveDomain(appId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (domainId: number) => api<void>("DELETE", `/apps/${appId}/domains/${domainId}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.domains(appId) }),
  });
}
