import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, uploadFormData } from "../api/client";
import { Button } from "./ui/button";
import { Card, CardContent, CardHeader } from "./ui/card";

import { Input } from "./ui/input";
import { OrganizationDeletion } from "./OrganizationDeletion";
import { lifecycleChanged, type OrganizationLifecycle } from "../api/organizationLifecycle";

export function OrganizationDataCard() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [technicalDetails, setTechnicalDetails] = useState<string | undefined>();
  const [organization, setOrganization] = useState<OrganizationLifecycle | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const refresh = useCallback(() => { void api.get<OrganizationLifecycle>("/api/organizations/me").then(setOrganization).catch(() => setError("Organisationsstatus konnte nicht geladen werden.")); }, []);
  useEffect(() => { refresh(); window.addEventListener("organization-lifecycle-changed", refresh); return () => window.removeEventListener("organization-lifecycle-changed", refresh); }, [refresh]);
  async function restore() {
    if (!file || importing) return;
    const data = new FormData(); data.append("file", file);
    setImporting(true); setError(null); setTechnicalDetails(undefined); setResult(null);
    try {
      const response = await uploadFormData<{ row_counts: Record<string, number> }>("/api/organizations/import", data);
      setResult(`Import abgeschlossen: ${Object.values(response.row_counts).reduce((sum, count) => sum + count, 0).toLocaleString("de-DE")} Datensätze aus dem Archiv verarbeitet.`);
      refresh(); lifecycleChanged();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Der Import ist fehlgeschlagen.");
      setTechnicalDetails(reason instanceof ApiError
        ? [reason.code ? `Fehlercode: ${reason.code}` : undefined, reason.technicalDetails].filter(Boolean).join("\n\n") || undefined
        : undefined);
    }
    finally { setImporting(false); }
  }
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => { active.current?.abort(); }, []);

  async function download() {
    if (active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true); setError(null); setTechnicalDetails(undefined);
    try {
      const blob = await api.getBlob("/api/organizations/export", { method: "POST", signal: controller.signal });
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      try {
        anchor.href = url;
        anchor.download = "verwalino-organisation.tar.gz";
        document.body.append(anchor);
        anchor.click();
      } finally {
        anchor.remove(); URL.revokeObjectURL(url);
      }
    } catch (reason) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Export konnte nicht geladen werden. Bitte versuchen Sie es erneut.");
    } finally {
      if (!controller.signal.aborted) { active.current = null; setBusy(false); }
    }
  }

  return <Card role="region" aria-labelledby="organization-data-title" aria-busy={busy || importing}>
    <CardHeader><h2 id="organization-data-title" className="text-base font-semibold">Organisationsdaten</h2></CardHeader>
    <CardContent className="space-y-4">
      <p className="text-sm text-muted-foreground">Der Export enthält die für Sie freigegebenen Daten und Originaldokumente. Daten mit eingeschränkten Freigaben können fehlen. Bewahren Sie das Archiv geschützt auf.</p>
      {error && <p role="alert" className="break-words text-sm text-destructive">{error}</p>}
      {error && technicalDetails && <details className="min-w-0 text-sm"><summary className="cursor-pointer">Technische Details</summary><pre className="whitespace-pre-wrap break-all">{technicalDetails}</pre></details>}
      {result && <p role="status">{result}</p>}
      <div className="flex flex-wrap gap-3">
      <Button type="button" variant="outline" size="lg" disabled={busy || importing} onClick={() => void download()}>
        {busy ? "Export wird vorbereitet …" : "Daten exportieren"}
      </Button>
      {organization && organization.deletion?.status !== "cooling_off" && <OrganizationDeletion changed={refresh} download={download} exporting={busy} />}
      </div>
      {organization?.import_available && organization.deletion?.status !== "cooling_off" && <div className="space-y-3 border-t pt-4">
        <p className="text-sm text-muted-foreground">Importieren Sie ein Verwalino-Exportarchiv. Der Import ist nur in eine leere Organisation möglich. Fehlende Abhängigkeiten oder ungültige Prüfsummen verhindern den gesamten Import.</p>
        <label className="block space-y-2 text-sm">Exportarchiv<Input type="file" accept=".tar.gz,application/gzip" onChange={event => setFile(event.target.files?.[0] ?? null)} disabled={importing} /></label>
        <Button type="button" variant="outline" size="lg" disabled={!file || importing || busy} onClick={() => void restore()}>{importing ? "Import wird ausgeführt …" : "Daten importieren"}</Button>
      </div>}
    </CardContent>
  </Card>;
}
