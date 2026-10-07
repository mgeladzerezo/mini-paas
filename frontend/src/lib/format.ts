export function shortSha(sha: string): string {
  return sha.slice(0, 7);
}

export function formatTime(iso: string | null): string {
  if (!iso) {
    return "never";
  }
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
