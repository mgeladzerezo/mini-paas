import { useState } from "react";
import type { FormEvent } from "react";
import { useAddDomain, useCheckDomain, useDomains, useRemoveDomain } from "../../api/hooks";
import type { AppSummary, Domain } from "../../api/types";
import { ErrorMessage, Loading } from "../../components/Feedback";
import { StatusBadge } from "../../components/StatusBadge";
import { formatTime } from "../../lib/format";

function DnsRecord({ type, name, value }: { type: string; name: string; value: string }) {
  return (
    <tr className="font-mono text-xs">
      <td className="py-1 pr-4 text-slate-500">{type}</td>
      <td className="py-1 pr-4">{name}</td>
      <td className="break-all py-1">{value}</td>
    </tr>
  );
}

function DomainCard({ app, domain }: { app: AppSummary; domain: Domain }) {
  const check = useCheckDomain(app.id);
  const remove = useRemoveDomain(app.id);

  return (
    <li className="card space-y-3 p-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="font-mono font-medium">{domain.hostname}</span>
          <StatusBadge
            status={domain.verified ? "verified" : "unverified"}
            label={domain.verified ? "ownership verified" : "ownership not verified"}
          />
          {domain.verified && (
            <>
              <StatusBadge status={domain.dns_status} label={`DNS ${domain.dns_status}`} />
              <StatusBadge status={domain.tls_status} label={`TLS ${domain.tls_status}`} />
            </>
          )}
        </div>
        <div className="space-x-2">
          <button
            type="button"
            className="btn"
            disabled={check.isPending}
            onClick={() => check.mutate(domain.id)}
          >
            {domain.verified ? "Refresh status" : "Verify"}
          </button>
          <button
            type="button"
            className="btn btn-danger"
            disabled={remove.isPending}
            onClick={() => remove.mutate(domain.id)}
          >
            Remove
          </button>
        </div>
      </div>

      {!domain.verified && (
        <p className="text-sm text-slate-600">
          Traffic for this domain is not routed until ownership is verified. Create the TXT record
          below, then press Verify.
        </p>
      )}
      <table>
        <tbody>
          <DnsRecord type="TXT" name={domain.txt_record_name} value={domain.txt_record_value} />
          <DnsRecord type="CNAME" name={domain.hostname} value={domain.cname_target} />
        </tbody>
      </table>
      <p className="text-xs text-slate-500">Last checked: {formatTime(domain.last_checked_at)}</p>
      <ErrorMessage error={check.error ?? remove.error} />
    </li>
  );
}

export function DomainsTab({ app }: { app: AppSummary }) {
  const domains = useDomains(app.id);
  const addDomain = useAddDomain(app.id);
  const [hostname, setHostname] = useState("");

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    addDomain.mutate(hostname, { onSuccess: () => setHostname("") });
  }

  return (
    <div className="space-y-4">
      <form onSubmit={onSubmit} className="flex gap-2">
        <input
          aria-label="Domain"
          required
          placeholder="shop.example.com"
          className="input max-w-sm font-mono"
          value={hostname}
          onChange={(event) => setHostname(event.target.value)}
        />
        <button type="submit" className="btn btn-primary" disabled={addDomain.isPending}>
          Add domain
        </button>
      </form>
      <ErrorMessage error={addDomain.error ?? domains.error} />
      {domains.isLoading && <Loading />}
      {domains.data?.length === 0 && (
        <p className="text-sm text-slate-600">
          The app is served at <span className="font-mono">{app.hostname}</span>. Add a domain you
          own to serve it there as well.
        </p>
      )}
      <ul className="space-y-3">
        {domains.data?.map((domain) => (
          <DomainCard key={domain.id} app={app} domain={domain} />
        ))}
      </ul>
    </div>
  );
}
