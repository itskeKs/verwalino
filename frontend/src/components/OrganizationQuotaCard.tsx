import { useEffect, useState } from "react";
import { getOrganizationQuota } from "../api/endpoints";
import type { OrganizationQuota } from "../api/types";
import { Card, CardContent, CardHeader } from "./ui/card";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";

function size(bytes: number): string {
  const unit = bytes >= 1024 ** 3 ? "GiB" : "MiB";
  const divisor = unit === "GiB" ? 1024 ** 3 : 1024 ** 2;
  return `${(bytes / divisor).toLocaleString("de-DE", { maximumFractionDigits: 2 })} ${unit}`;
}

export function OrganizationQuotaCard() {
  const [quota, setQuota] = useState<OrganizationQuota | null>(null);
  const [failed, setFailed] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let cancelled = false;
    getOrganizationQuota().then(value => {
      if (!cancelled) setQuota(value);
    }).catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, [revision]);
  if (quota?.plan === "unlimited") return null;
  const rows = quota ? [
    { label: "Speicher", usage: quota.usage.storage_bytes, limit: quota.limits.storage_bytes, format: size },
    { label: "Objekte", usage: quota.usage.properties, limit: quota.limits.properties, format: (n: number) => n.toLocaleString("de-DE") },
    { label: "Einheiten", usage: quota.usage.units, limit: quota.limits.units, format: (n: number) => n.toLocaleString("de-DE") },
  ] : [];
  const nearLimit = rows.some(row => row.limit !== null && row.usage >= row.limit * 0.9);
  return <Card role="region" aria-labelledby="organization-quota-title">
    <CardHeader><h2 id="organization-quota-title" className="text-base font-semibold">Kontingent</h2></CardHeader>
    <CardContent>
      {failed ? <div role="alert" className="flex flex-wrap items-center gap-2">
        <span>Kontingent nicht verfügbar.</span>
        <Button variant="outline" onClick={() => { setFailed(false); setRevision(n => n + 1); }}>Erneut laden</Button>
      </div> : !quota ? <p role="status">Kontingent wird geladen …</p> : <>
        <dl className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          {rows.map(row => <div key={row.label} className="min-w-0">
            <dt className="text-sm text-muted-foreground">{row.label}</dt>
            <dd className="mt-1 flex flex-wrap items-center gap-2 tabular-nums">
              <span>{row.format(row.usage)} / {row.limit === null ? "—" : row.format(row.limit)}</span>
              {row.limit !== null && row.usage >= row.limit * 0.9 && <Badge tone="warning">Fast ausgeschöpft</Badge>}
            </dd>
          </div>)}
        </dl>
        {nearLimit && <p className="mt-4 text-sm">Ihr Kontingent ist zu mindestens 90 % belegt.</p>}
      </>}
    </CardContent>
  </Card>;
}
