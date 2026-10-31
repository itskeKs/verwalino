import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { Button } from "./ui/button";
import { Card, CardContent, CardHeader } from "./ui/card";

export function OrganizationDataCard() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => { active.current?.abort(); }, []);

  async function download() {
    if (active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true); setError(null);
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

  return <Card role="region" aria-labelledby="organization-data-title" aria-busy={busy}>
    <CardHeader><h2 id="organization-data-title" className="text-base font-semibold">Organisationsdaten</h2></CardHeader>
    <CardContent className="space-y-4">
      <p className="text-sm text-muted-foreground">Der Export enthält die für Sie freigegebenen Daten und Originaldokumente. Daten mit eingeschränkten Freigaben können fehlen. Bewahren Sie das Archiv geschützt auf.</p>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <Button type="button" variant="outline" size="lg" disabled={busy} onClick={() => void download()}>
        {busy ? "Export wird vorbereitet …" : "Daten exportieren"}
      </Button>
    </CardContent>
  </Card>;
}
