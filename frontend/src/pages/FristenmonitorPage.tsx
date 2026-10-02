/**
 * Fristenmonitor (M9) -- read-only overview of statutory and provisional
 * deadlines: §556 III BK deadline (hard), WEG/Wirtschaftsplan deadlines
 * (provisional [§18]), overdue open items + Beleg retention (informational).
 *
 * Sorted by urgency by the backend (OVERDUE -> DUE -> UPCOMING -> INFO -> OK).
 */

import { useEffect, useState } from "react";
import { CheckCircle2 } from "lucide-react";
import { listFristen } from "../api/endpoints";
import type { DeadlineItem, DeadlineStatus } from "../api/types";
import { AccountingScopeSelector } from "../components/AccountingScopeSelector";
import { ApiError } from "../api/client";
import { Card, CardContent } from "../components/ui/card";
import { Badge, type BadgeTone } from "../components/ui/badge";

const KIND_LABELS: Record<string, string> = {
  BK_ABRECHNUNG: "Betriebskostenabrechnung",
  WEG_JAHRESABRECHNUNG: "WEG-Jahresabrechnung",
  WEG_VERMOEGENSBERICHT: "Vermögensbericht",
  WIRTSCHAFTSPLAN: "Wirtschaftsplan",
  OFFENE_POSTEN: "Überfällige offene Posten",
  BELEG_AUFBEWAHRUNG: "Beleg-Aufbewahrung",
};

const STATUS_META: Record<DeadlineStatus, { label: string; tone: BadgeTone }> = {
  OVERDUE: { label: "Überfällig", tone: "destructive" },
  DUE: { label: "Fällig", tone: "warning" },
  UPCOMING: { label: "Anstehend", tone: "primary" },
  INFO: { label: "Info", tone: "info" },
  OK: { label: "Erledigt", tone: "success" },
};

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

/** Make backend detail text reader-friendly: "section 556" -> "§556". */
function prettyDetail(detail: string): string {
  return detail.replaceAll("section ", "§");
}

function formatDate(iso: string): string {
  const [y, m, d] = iso.split("-");
  return `${d}.${m}.${y}`;
}

export function FristenmonitorPage() {
  const [referenceDate, setReferenceDate] = useState<string>(todayIso());
  const [scopeId, setScopeId] = useState<string | null>(null);
  const [result, setResult] = useState<{
    referenceDate: string;
    scopeId: string | null;
    items: DeadlineItem[];
    error: string | null;
  } | null>(null);

  useEffect(() => {
    let cancelled = false;
    listFristen(referenceDate, scopeId || undefined)
      .then(items => {
        if (!cancelled) setResult({ referenceDate, scopeId, items, error: null });
      })
      .catch(err => {
        if (cancelled) return;
        const error = err instanceof ApiError && err.status === 401
          ? "Nicht authentifiziert. Bitte erneut anmelden."
          : String(err);
        setResult({ referenceDate, scopeId, items: [], error });
      });
    return () => { cancelled = true; };
  }, [referenceDate, scopeId]);

  const loading = result?.referenceDate !== referenceDate || result?.scopeId !== scopeId;
  const error = loading ? null : result?.error;
  const items = !loading && !error ? result?.items ?? [] : [];
  const available = !loading && !error;

  const actionCount = items.filter(
    (i) => i.status === "OVERDUE" || i.status === "DUE",
  ).length;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="m-0">Fristenmonitor</h1>
        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          Stichtag
          <input
            type="date"
            value={referenceDate}
            onChange={(e) => { const date = e.target.value || todayIso(); if (date !== referenceDate) { setResult(null); setReferenceDate(date); } }}
            className="h-9 rounded-md border border-border bg-card px-2.5 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring/30"
          />
        </label>
      </div>

      <div className="form-grid max-w-xl">
        <AccountingScopeSelector capability="read" value={scopeId} onChange={id => { if (id !== scopeId) { setResult(null); setScopeId(id); } }} />
      </div>
      <p className="text-sm text-muted-foreground">
        {loading ? "Fristen werden geladen." : error ? "Fristenübersicht nicht verfügbar." : actionCount > 0
          ? `${actionCount} Frist(en) erfordern Aufmerksamkeit (überfällig oder fällig).`
          : "Keine überfälligen oder fälligen Fristen zum Stichtag."}{" "}
        Die BK-Frist (§556 III BGB) ist gesetzlich; WEG-/Wirtschaftsplan-Fristen
        sind provisorisch ([§18] fachlich zu bestätigen).
      </p>

      {error && <p role="alert" className="error-text">{error}</p>}

      {loading ? (
        <p className="text-sm text-muted-foreground">Lade…</p>
      ) : error ? null : items.length === 0 ? (
        <p className="empty-hint">Keine Fristen ermittelt.</p>
      ) : (
        <Card>
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th className="pl-5">Status</th>
                    <th>Art</th>
                    <th>Bezug</th>
                    <th>Fällig am</th>
                    <th className="pr-5">Hinweis</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((it, idx) => (
                    <tr key={`${it.kind}-${it.reference}-${idx}`}>
                      <td className="pl-5 align-top">
                        <Badge tone={STATUS_META[it.status].tone}>
                          {STATUS_META[it.status].label}
                        </Badge>
                      </td>
                      <td className="align-top">
                        <span className="font-medium">
                          {KIND_LABELS[it.kind] ?? it.kind}
                        </span>
                        <div className="mt-1 flex flex-wrap gap-1.5">
                          {it.is_statutory && <Badge tone="primary">gesetzlich</Badge>}
                          {it.is_provisional && (
                            <Badge tone="info">provisorisch [§18]</Badge>
                          )}
                        </div>
                      </td>
                      <td className="align-top">{it.reference}</td>
                      <td className="whitespace-nowrap align-top font-mono text-sm tabular-nums">
                        {it.due_date ? formatDate(it.due_date) : "—"}
                      </td>
                      <td className="max-w-md align-top text-xs text-muted-foreground pr-5">
                        {prettyDetail(it.detail)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}

      {available && actionCount === 0 && items.length > 0 && (
        <div className="flex items-center gap-3 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          <CheckCircle2 className="h-5 w-5 shrink-0" />
          Alle fälligen Fristen sind erledigt.
        </div>
      )}
    </div>
  );
}
