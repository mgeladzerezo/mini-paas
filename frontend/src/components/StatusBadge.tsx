const STYLES: Record<string, string> = {
  healthy: "bg-emerald-100 text-emerald-800",
  ok: "bg-emerald-100 text-emerald-800",
  issued: "bg-emerald-100 text-emerald-800",
  verified: "bg-emerald-100 text-emerald-800",
  pending: "bg-amber-100 text-amber-800",
  building: "bg-sky-100 text-sky-800",
  deploying: "bg-sky-100 text-sky-800",
  failed: "bg-red-100 text-red-800",
  unverified: "bg-amber-100 text-amber-800",
};

const NEUTRAL = "bg-slate-100 text-slate-600";

export function StatusBadge({ status, label }: { status: string | null; label?: string }) {
  const text = label ?? status ?? "not deployed";
  const style = (status && STYLES[status]) || NEUTRAL;
  return (
    <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${style}`}>
      {text}
    </span>
  );
}
